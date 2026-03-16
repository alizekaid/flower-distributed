from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.lib import hub
from network_managers import NetworkManager
from stats_manager import get_stats_manager
import time

class BWAwareController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(BWAwareController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {} # dpid -> datapath object
        self.topology_file = "topology.json"
        self.stats = {} # (dpid, port) -> (rx_bytes, tx_bytes, timestamp)
        
        # Logging setup
        import logging
        fh = logging.FileHandler('bw_controller.log', mode='w')
        fh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(fh)
        self.logger.setLevel(logging.INFO)

        self.logger.info("Initializing Bandwidth-Aware Network Manager...")
        self.network_manager = NetworkManager(self.topology_file)
        self.stats_manager = get_stats_manager(self.topology_file)
        
        # Start the monitoring thread (stats collection)
        self.monitor_thread = hub.spawn(self._monitor)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        self.datapaths[datapath.id] = datapath

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions, permanent=True)  # Table-miss must be permanent!
        self.logger.info("Switch connected: %d", datapath.id)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None, permanent=False):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        if permanent:
            # Table-miss and other permanent rules must NOT expire
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority, match=match,
                                    instructions=inst,
                                    buffer_id=buffer_id if buffer_id else ofproto.OFP_NO_BUFFER)
        else:
            # idle_timeout: expire if no traffic for 10s
            # hard_timeout: expire unconditionally after 15s so active flows also get re-evaluated
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority, match=match,
                                    idle_timeout=10, hard_timeout=15,
                                    instructions=inst,
                                    buffer_id=buffer_id if buffer_id else ofproto.OFP_NO_BUFFER)
        datapath.send_msg(mod)

    def _get_widest_path(self, src_mac, dst_mac):
        """
        Calculates the path with the maximum bottleneck bandwidth.
        Strategy: Max-Min (Maximize the minimum available bandwidth).
        Tie-breaker: Shortest hop count (if bandwidths are equal).
        """
        src_host = self.network_manager.host_manager.get_host(src_mac)
        dst_host = self.network_manager.host_manager.get_host(dst_mac)
        if not src_host or not dst_host: return None

        all_paths = self.network_manager.get_all_paths_between_switches(src_host.switch_id, dst_host.switch_id)
        if not all_paths: return None

        self.stats_manager.load_usage()

        best_path = None
        max_bottleneck = -1.0
        best_hop_count = float('inf')
        selected_bottleneck_link = "none"

        # Selection Logic: Maximize bottleneck, then minimize hops
        self.logger.info("  Analyzing %d potential paths for %s -> %s...", len(all_paths), src_mac, dst_mac)
        for idx, path in enumerate(all_paths):
            bottleneck = float('inf')
            bottleneck_link = "none"
            
            for i in range(len(path) - 1):
                cur_sw, next_sw = path[i]['name'], path[i+1]['name']
                avail = self.stats_manager.get_available_bandwidth(cur_sw, next_sw)
                if avail is not None and avail < bottleneck:
                    bottleneck = avail
                    bottleneck_link = f"{cur_sw}<->{next_sw}"
            
            h_avail = self.stats_manager.get_available_bandwidth(src_host.name, src_host.switch_id)
            if h_avail is not None and h_avail < bottleneck:
                bottleneck = h_avail
                bottleneck_link = f"{src_host.name}<->{src_host.switch_id}"
            
            t_avail = self.stats_manager.get_available_bandwidth(dst_host.switch_id, dst_host.name)
            if t_avail is not None and t_avail < bottleneck:
                bottleneck = t_avail
                bottleneck_link = f"{dst_host.switch_id}<->{dst_host.name}"

            self.logger.info("    Path %d: %s | BW: %.2f Mbps (Bottleneck: %s)", 
                              idx + 1, [h['name'] for h in path], bottleneck, bottleneck_link)

            if bottleneck > max_bottleneck:
                max_bottleneck = bottleneck
                best_path = path
                best_hop_count = len(path)
                selected_bottleneck_link = bottleneck_link
            elif bottleneck == max_bottleneck and max_bottleneck >= 0:
                if len(path) < best_hop_count:
                    best_path = path
                    best_hop_count = len(path)
                    selected_bottleneck_link = bottleneck_link

        if best_path:
            best_path[0]['in_port'] = self.network_manager._parse_port(src_host.switch_port)
            best_path[-1]['out_port'] = self.network_manager._parse_port(dst_host.switch_port)
            self.logger.info("  >> SELECTED: %s (Bottleneck: %.2f Mbps on %s, Hops: %d)", 
                             [h['name'] for h in best_path], max_bottleneck, selected_bottleneck_link, best_hop_count)
        
        return best_path

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP: return

        dst = eth.dst
        src = eth.src
        dpid = datapath.id

        # 1. Host Learning (Self-Healing)
        sw_obj = self.network_manager.switch_manager.SwitchDict.get(dpid, None)
        if sw_obj:
            host_obj = self.network_manager.host_manager.get_host(src)
            if not host_obj or host_obj.switch_id != sw_obj.name or host_obj.switch_port != in_port:
                from network_managers import Host
                self.logger.info("  Learning host: %s at %s port %d", src, sw_obj.name, in_port)
                self.network_manager.host_manager.add_host(Host(
                    name=f"learned-{src}", mac=src, ip=None,
                    switch_id=sw_obj.name, switch_port=in_port
                ))
                sw_obj.add_host_port(in_port, f"learned-{src}")

        # 2. Ignore broadcast/multicast
        if dst.startswith("ff:ff:ff") or dst.startswith("33:33:00") or dst.startswith("01:00:5e"):
            return

        # Path discovery (Dynamic/Bandwidth-Aware)
        self.logger.info("  PacketIn: %s -> %s on dpid=%d port=%d", src, dst, dpid, in_port)
        path = self._get_widest_path(src, dst)
        if not path:
            self.logger.warning("  NO PATH found for %s -> %s! Check topology.json knows both hosts.", src, dst)
            # Flood as fallback so packet isn't silently dropped
            ofproto = datapath.ofproto
            actions = [datapath.ofproto_parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
            out = datapath.ofproto_parser.OFPPacketOut(
                datapath=datapath, buffer_id=msg.buffer_id,
                in_port=in_port, actions=actions, data=msg.data)
            datapath.send_msg(out)
            return

        # Install flow entries across the path (Bidirectional)
        for hop in path:
            dp = self.datapaths.get(hop['dpid'])
            if dp:
                h_parser = dp.ofproto_parser
                # Forward Flow
                f_match = h_parser.OFPMatch(eth_src=src, eth_dst=dst)
                f_actions = [h_parser.OFPActionOutput(hop['out_port'])]
                self.add_flow(dp, 20, f_match, f_actions)
                
                # Reverse Flow
                r_match = h_parser.OFPMatch(eth_src=dst, eth_dst=src)
                r_actions = [h_parser.OFPActionOutput(hop['in_port'])]
                self.add_flow(dp, 20, r_match, r_actions)

        # Send current packet
        out_port = next((h['out_port'] for h in path if h['dpid'] == dpid), None)
        if out_port:
            actions = [parser.OFPActionOutput(out_port)]
            out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                    in_port=in_port, actions=actions, data=msg.data)
            datapath.send_msg(out)

    def _monitor(self):
        """Standard stats collection loop."""
        while True:
            for dp in self.datapaths.values():
                ofproto = dp.ofproto
                parser = dp.ofproto_parser
                req = parser.OFPPortStatsRequest(dp, 0, ofproto.OFPP_ANY)
                dp.send_msg(req)
            hub.sleep(3)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        timestamp = time.time()
        sw_obj = self.network_manager.switch_manager.SwitchDict.get(dpid)
        if not sw_obj: return

        for stat in body:
            port_no = stat.port_no
            if port_no > 65535: continue
            key = (dpid, port_no)
            new_rx, new_tx = stat.rx_bytes, stat.tx_bytes
            
            if key in self.stats:
                old_rx, old_tx, old_ts = self.stats[key]
                duration = timestamp - old_ts
                if duration > 0:
                    usage = (new_rx - old_rx + new_tx - old_tx) * 8 / (duration * 1000000.0)
                    # Mapping logic
                    host_name = sw_obj.port_to_host.get(port_no)
                    if host_name:
                        self.stats_manager.update_usage(sw_obj.name, host_name, usage)
                    else:
                        for u, v, data in self.network_manager.graph.edges(sw_obj.name, data=True):
                            if self.network_manager._parse_port(data['ports'][sw_obj.name]) == port_no:
                                self.stats_manager.update_usage(sw_obj.name, v, usage)
                                break
            self.stats[key] = (new_rx, new_tx, timestamp)
        self.stats_manager.save_usage()
