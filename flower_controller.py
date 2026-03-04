from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from network_managers import NetworkManager

class FlowerController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(FlowerController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {} # dpid -> datapath object
        self.topology_file = "topology.json"
        self.proactive_installed = False # Flag to ensure single run
        
        # Initialize the new Network Manager
        # Add file logging for verification
        import logging
        fh = logging.FileHandler('controller_startup.log', mode='w')
        fh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(fh)

        self.logger.info("Initializing Network Manager...")
        self.network_manager = NetworkManager(self.topology_file)
        self._print_manager_info()

    def _print_manager_info(self):
        """Log the discovery of all paths between all switch pairs."""
        hosts = self.network_manager.host_manager.HostDict
        self.logger.info("-" * 40)
        self.logger.info("NETWORK MANAGERS INITIALIZED")
        self.logger.info("Number of Hosts: %d", len(hosts))
        
        switches = list(set(self.network_manager.switch_manager.SwitchDict.values()))
        sw_names = sorted([sw.name for sw in switches])
        self.logger.info("Number of Switches: %d", len(sw_names))
        self.logger.info("-" * 40)

        self.logger.info("MULTI-PATH DISCOVERY REPORT:")
        
        # Track pairs to avoid double-logging (A->B and B->A)
        processed_pairs = set()

        for s1_name in sw_names:
            for s2_name in sw_names:
                if s1_name == s2_name: continue
                
                # Sort names to create a unique key for the pair
                pair = tuple(sorted([s1_name, s2_name]))
                if pair in processed_pairs: continue
                processed_pairs.add(pair)

                paths = self.network_manager.get_all_paths_between_switches(s1_name, s2_name)
                # Sort paths by hop count to easily identify shortest/longest
                paths.sort(key=len)
                
                self.logger.info("  %s <-> %s : %d path(s) found", s1_name, s2_name, len(paths))
                for idx, path in enumerate(paths):
                    # Determine labels
                    labels = []
                    if idx == 0:
                        labels.append("SHORTEST")
                    if idx == len(paths) - 1 and len(paths) > 1:
                        labels.append("LONGEST")
                    if idx == len(paths) // 2 and len(paths) > 2:
                        # Only label median if we have at least 3 distinct paths
                        labels.append("MEDIAN")
                    
                    label_str = f" ({', '.join(labels)})" if labels else ""
                    
                    # Format as s1(p1) -> s2(p2) -> ...
                    hops = []
                    for hop in path:
                        name = hop.get('name', f"dpid:{hop['dpid']}")
                        out_port = hop.get('out_port')
                        if out_port is not None:
                            hops.append(f"{name}(port {out_port})")
                        else:
                            hops.append(f"{name}")
                    self.logger.info("    Path %d%s: %s", idx + 1, label_str, " -> ".join(hops))
        
        self.logger.info("-" * 40)


    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Store datapath
        self.datapaths[datapath.id] = datapath

        # If we have no topology info, try to reload it (sync with Mininet)
        if len(self.network_manager.switch_manager.SwitchDict) == 0:
            self.logger.info("Switch connected but no topology map found. Checking topology.json...")
            if self.network_manager.reload_topology():
                self.logger.info("Topology loaded successfully.")
                self._print_manager_info()

        # Install table-miss flow entry
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        self.logger.info("Switch connected: %d", datapath.id)

    def _install_path(self, src_mac, dst_mac):
        path = self.network_manager.get_path_with_ports(src_mac, dst_mac)
        if not path:
            return

        for hop in path:
            dpid = hop['dpid']
            datapath = self._get_datapath(dpid)
            if not datapath: continue

            parser = datapath.ofproto_parser
            match = parser.OFPMatch(eth_src=src_mac, eth_dst=dst_mac)
            actions = [parser.OFPActionOutput(hop['out_port'])]
            
            # Priority 10 for proactive flows
            self.add_flow(datapath, 10, match, actions)

    def _get_datapath(self, dpid):
        """Helper to find datapath object by DPID."""
        return self.datapaths.get(dpid)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst = eth.dst
        src = eth.src
        dpid = datapath.id

        self.logger.debug("PacketIn: dpid=%d src=%s dst=%s in_port=%d", dpid, src, dst, in_port)

        # 1. Host Learning (Self-Healing)
        # If the switch is known but the host isn't, or if the host moved, update the manager
        sw_name = self.network_manager.switch_manager.SwitchDict.get(dpid, None)
        if sw_name:
            host_obj = self.network_manager.host_manager.get_host(src)
            # Simple learning: if host unknown or port/switch changed
            if not host_obj or host_obj.switch_id != sw_name.name or host_obj.switch_port != in_port:
                from network_managers import Host
                self.logger.info("  Learning/Updating host: %s at %s port %d", src, sw_name.name, in_port)
                self.network_manager.host_manager.add_host(Host(
                    name=f"learned-{src}", mac=src, ip=None,
                    switch_id=sw_name.name, switch_port=in_port
                ))
                # Add host port to switch record
                sw_name.add_host_port(in_port, f"learned-{src}")
                # We don't necessarily need to reload topology, but we could re-run path discovery if needed
                # For now, host ports are added to switch objects dynamically

        # 2. Handle Broadcast / Multicast / Unknown Unicast
        is_broadcast = dst.startswith("ff:ff:ff") or dst.startswith("33:33:00") or dst.startswith("01:00:5e")
        
        path = None
        if not is_broadcast:
            path = self.network_manager.get_path_with_ports(src, dst)

        if is_broadcast or not path:
            if is_broadcast:
                self.logger.debug("  Dropping broadcast/multicast packet (fallback disabled)")
            else:
                self.logger.warning("  No path found for %s -> %s and fallback is disabled. Dropping.", src, dst)
            return

        # 3. Handle Unicast with known path
        self.logger.info("  Path found for %s -> %s: %s", src, dst, path)
        
        # Install flows on ALL switches in the path (Reactive-Proactive)
        for hop in path:
            sw_dpid = hop['dpid']
            sw_dp = self._get_datapath(sw_dpid)
            if sw_dp:
                sw_parser = sw_dp.ofproto_parser
                match = sw_parser.OFPMatch(eth_src=src, eth_dst=dst)
                actions = [sw_parser.OFPActionOutput(hop['out_port'])]
                self.add_flow(sw_dp, 10, match, actions)
        
        # Send current packet out from the correct port on the current switch
        out_port = None
        for hop in path:
            if hop['dpid'] == dpid:
                out_port = hop['out_port']
                break
        
        if out_port:
            actions = [parser.OFPActionOutput(out_port)]
            out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                    in_port=in_port, actions=actions, data=msg.data)
            datapath.send_msg(out)
        