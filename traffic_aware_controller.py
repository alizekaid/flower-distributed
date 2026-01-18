#!/usr/bin/env python3
"""
Traffic-Aware SDN Controller for Flower Distributed Learning

WORKFLOW:
1. Topology Discovery: Learn network structure via LLDP
2. Traffic Monitoring: Poll switch statistics every 5 seconds
3. Path Calculation: Use Dijkstra with traffic-based weights
4. Flow Installation: Install rules on all switches in the path

PIPELINE:
[Packet In] -> [MAC Learning] -> [Path Calculation] -> [Flow Installation] -> [Packet Out]
     ^                                    |
     |                                    v
[Stats Monitor] -> [Update Link Weights] -> [Graph]

SCHEMA:
- Graph: NetworkX DiGraph with switches as nodes, links as edges
- Edge Attributes: port, weight (1-11 based on utilization), capacity
- Host Table: {MAC: (switch_dpid, port)}
- Stats Table: {(dpid, port): {tx_bytes, rx_bytes, timestamp}}
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.topology import event
import networkx as nx
from threading import Thread
import time


class TrafficAwareController(app_manager.RyuApp):
    """
    SDN Controller that routes traffic based on real-time link utilization.
    
    Key Features:
    - Automatic topology discovery (LLDP)
    - Periodic traffic monitoring (every 5s)
    - Dynamic path calculation (Dijkstra with traffic weights)
    - Proactive flow installation for known hosts
    """
    
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(TrafficAwareController, self).__init__(*args, **kwargs)
        
        # SCHEMA: Network Graph
        # Nodes = Switch DPIDs, Edges = Links with attributes
        self.net = nx.DiGraph()
        
        # SCHEMA: Host Location Table
        # {MAC_address: (switch_dpid, port_number)}
        self.hosts = {}
        
        # SCHEMA: Port Statistics
        # {(dpid, port): {'tx_bytes': int, 'rx_bytes': int, 'timestamp': float}}
        self.port_stats = {}
        
        # SCHEMA: Active Datapaths
        # {dpid: datapath_object}
        self.datapaths = {}
        
        # Flag to track if proactive flows have been installed
        self.proactive_flows_installed = False
        
        # Start background monitoring thread
        self.monitor_thread = Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        self.logger.info("=" * 60)
        self.logger.info("Traffic-Aware Controller Started")
        self.logger.info("=" * 60)

    # ========================================================================
    # PIPELINE STAGE 1: DATAPATH MANAGEMENT
    # ========================================================================
    
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        """Track switch connections/disconnections"""
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.info('[DATAPATH] Switch connected: dpid=%s', datapath.id)
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.info('[DATAPATH] Switch disconnected: dpid=%s', datapath.id)
                del self.datapaths[datapath.id]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Install table-miss flow entry on new switches"""
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Table-miss: send unknown packets to controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        self.logger.info('[INIT] Table-miss flow installed on switch %s', datapath.id)

    # ========================================================================
    # PIPELINE STAGE 2: TOPOLOGY DISCOVERY
    # ========================================================================
    
    @set_ev_cls(event.EventSwitchEnter)
    def switch_enter_handler(self, ev):
        """Add switch to topology graph"""
        switch = ev.switch
        self.net.add_node(switch.dp.id)
        self.logger.info('[TOPOLOGY] Switch added to graph: dpid=%s', switch.dp.id)

    @set_ev_cls(event.EventSwitchLeave)
    def switch_leave_handler(self, ev):
        """Remove switch from topology graph"""
        switch = ev.switch
        if switch.dp.id in self.net:
            self.net.remove_node(switch.dp.id)
            self.logger.info('[TOPOLOGY] Switch removed from graph: dpid=%s', switch.dp.id)

    @set_ev_cls(event.EventLinkAdd)
    def link_add_handler(self, ev):
        """Add bidirectional link to topology graph"""
        link = ev.link
        src_dpid = link.src.dpid
        dst_dpid = link.dst.dpid
        src_port = link.src.port_no
        dst_port = link.dst.port_no
        
        # Add bidirectional edges (network is bidirectional)
        self.net.add_edge(src_dpid, dst_dpid, 
                         port=src_port, weight=1, capacity=100)
        self.net.add_edge(dst_dpid, src_dpid, 
                         port=dst_port, weight=1, capacity=100)
        
        self.logger.info('[TOPOLOGY] Link added: %s(port %s) <-> %s(port %s)', 
                        src_dpid, src_port, dst_dpid, dst_port)
        
        # After topology is discovered, install proactive flows
        # Trigger when we have at least 10 links (we expect 20 total)
        if not self.proactive_flows_installed and len(self.net.edges()) >= 10:
            # Wait a bit for remaining links to be discovered
            import threading
            def delayed_install():
                time.sleep(3)  # Wait 3 more seconds for remaining links
                if not self.proactive_flows_installed:
                    self._install_proactive_flows()
                    self.proactive_flows_installed = True
            
            threading.Thread(target=delayed_install, daemon=True).start()

    @set_ev_cls(event.EventLinkDelete)
    def link_delete_handler(self, ev):
        """Remove link from topology graph"""
        link = ev.link
        src_dpid = link.src.dpid
        dst_dpid = link.dst.dpid
        
        if self.net.has_edge(src_dpid, dst_dpid):
            self.net.remove_edge(src_dpid, dst_dpid)
        if self.net.has_edge(dst_dpid, src_dpid):
            self.net.remove_edge(dst_dpid, src_dpid)
            
        self.logger.info('[TOPOLOGY] Link removed: %s <-> %s', src_dpid, dst_dpid)

    # ========================================================================
    # PIPELINE STAGE 3: TRAFFIC MONITORING
    # ========================================================================
    
    def _monitor_loop(self):
        """Background thread: monitor traffic every 5 seconds"""
        iteration = 0
        while True:
            time.sleep(5)
            
            for dp in self.datapaths.values():
                self._request_port_stats(dp)
            
            # Log topology state every 30 seconds
            if iteration % 6 == 0 and len(self.net.nodes()) > 0:
                self._log_topology_state()
            
            iteration += 1

    def _request_port_stats(self, datapath):
        """Request port statistics from a switch"""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        """
        Process port statistics and update link weights.
        
        WEIGHT CALCULATION:
        weight = 1 + (utilization * 10)
        where utilization = current_throughput / link_capacity
        
        Examples:
        - Idle link (0% usage):   weight = 1.0
        - Half-full (50% usage):  weight = 6.0
        - Full link (100% usage): weight = 11.0
        """
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        
        for stat in body:
            port_no = stat.port_no
            if port_no == 0xfffffffe:  # Skip LOCAL port
                continue
                
            key = (dpid, port_no)
            curr_time = time.time()
            
            # Calculate bandwidth usage
            if key in self.port_stats:
                prev_stats = self.port_stats[key]
                time_diff = curr_time - prev_stats['timestamp']
                
                if time_diff > 0:
                    # Calculate throughput in Mbps
                    tx_speed = (stat.tx_bytes - prev_stats['tx_bytes']) / time_diff
                    rx_speed = (stat.rx_bytes - prev_stats['rx_bytes']) / time_diff
                    throughput_mbps = (tx_speed + rx_speed) * 8 / 1_000_000
                    
                    # Update edge weights in graph
                    for neighbor in self.net.neighbors(dpid):
                        edge_data = self.net[dpid][neighbor]
                        if edge_data.get('port') == port_no:
                            capacity = edge_data.get('capacity', 100)
                            utilization = min(throughput_mbps / capacity, 1.0)
                            new_weight = 1 + (utilization * 10)
                            self.net[dpid][neighbor]['weight'] = new_weight
                            
                            if throughput_mbps > 0.1:  # Only log significant traffic
                                self.logger.debug('[MONITOR] Link %s->%s: %.2f Mbps, weight=%.2f',
                                                dpid, neighbor, throughput_mbps, new_weight)
            
            # Store current stats
            self.port_stats[key] = {
                'tx_bytes': stat.tx_bytes,
                'rx_bytes': stat.rx_bytes,
                'timestamp': curr_time
            }

    # ========================================================================
    # PIPELINE STAGE 4: PATH CALCULATION
    # ========================================================================
    
    def calculate_best_path(self, src_dpid, dst_dpid):
        """
        Calculate least-congested path using Dijkstra's algorithm.
        
        Returns:
            list: Path as sequence of switch DPIDs, or None if no path exists
        """
        if src_dpid == dst_dpid:
            return [src_dpid]
        
        try:
            path = nx.shortest_path(self.net, src_dpid, dst_dpid, weight='weight')
            self.logger.info('[ROUTING] Path calculated: %s', ' -> '.join(map(str, path)))
            return path
        except nx.NetworkXNoPath:
            self.logger.warning('[ROUTING] No path found: %s -> %s', src_dpid, dst_dpid)
            return None

    # ========================================================================
    # PIPELINE STAGE 5: FLOW INSTALLATION
    # ========================================================================
    
    def add_flow(self, datapath, priority, match, actions, idle_timeout=0, hard_timeout=0):
        """Install a flow entry on a switch"""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst,
                                idle_timeout=idle_timeout,
                                hard_timeout=hard_timeout)
        datapath.send_msg(mod)

    def _install_proactive_flows(self):
        """
        Install proactive flows for all known host pairs.
        This eliminates the need for reactive flow installation.
        """
        # Known hosts from topology
        known_hosts = {
            '00:00:00:00:00:01': (2, 4),   # server on s2
            '00:00:00:00:00:02': (7, 1),   # client1 on s7
            '00:00:00:00:00:03': (7, 2),   # client2 on s7
            '00:00:00:00:00:04': (8, 1),   # client3 on s8
            '00:00:00:00:00:05': (8, 2),   # client4 on s8
            '00:00:00:00:00:06': (9, 1),   # client5 on s9
            '00:00:00:00:00:07': (9, 2),   # client6 on s9
            '00:00:00:00:00:08': (10, 1),  # client7 on s10
            '00:00:00:00:00:09': (10, 2),  # client8 on s10
        }
        
        # Store in hosts table
        for mac, (dpid, port) in known_hosts.items():
            self.hosts[mac] = (dpid, port)
        
        self.logger.info('[PROACTIVE] Installing flows for %d hosts', len(known_hosts))
        
        installed_count = 0
        for src_mac, (src_dpid, src_port) in known_hosts.items():
            for dst_mac, (dst_dpid, dst_port) in known_hosts.items():
                if src_mac == dst_mac:
                    continue
                
                # Calculate path
                path = self.calculate_best_path(src_dpid, dst_dpid)
                if path and len(path) >= 1:
                    self._install_path_flows(path, src_mac, dst_mac, src_port, dst_port)
                    installed_count += 1
        
        self.logger.info('[PROACTIVE] Installation complete: %d flows installed', installed_count)

    def _install_path_flows(self, path, src_mac, dst_mac, first_in_port, last_out_port):
        """Install flows on all switches in the calculated path"""
        for i in range(len(path)):
            dpid = path[i]
            
            if dpid not in self.datapaths:
                continue
            
            datapath = self.datapaths[dpid]
            parser = datapath.ofproto_parser
            
            # Determine output port
            if i == len(path) - 1:
                # Last switch: output to destination host
                out_port = last_out_port
            else:
                # Intermediate switch: output to next switch
                next_dpid = path[i+1]
                out_port = self.net[dpid][next_dpid]['port']
            
            # Install flow
            match = parser.OFPMatch(eth_dst=dst_mac, eth_src=src_mac)
            actions = [parser.OFPActionOutput(out_port)]
            self.add_flow(datapath, 1, match, actions, idle_timeout=0, hard_timeout=0)

    # ========================================================================
    # PIPELINE STAGE 6: PACKET HANDLING
    # ========================================================================
    
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        """
        Handle packets sent to controller.
        
        WORKFLOW:
        1. Parse packet and learn source MAC
        2. If destination unknown: flood
        3. If destination known: calculate path and install flows
        4. Forward packet
        """
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return  # Ignore LLDP

        dst = eth.dst
        src = eth.src
        dpid = datapath.id

        # Learn source MAC
        self.hosts[src] = (dpid, in_port)
        
        # Determine output port
        if dst not in self.hosts:
            # Unknown destination: flood
            out_port = ofproto.OFPP_FLOOD
        else:
            # Known destination: calculate path
            dst_dpid, dst_port = self.hosts[dst]
            
            if dpid == dst_dpid:
                # Same switch
                out_port = dst_port
            else:
                # Different switch: use calculated path
                path = self.calculate_best_path(dpid, dst_dpid)
                if path and len(path) >= 2:
                    out_port = self.net[dpid][path[1]]['port']
                    # Install flows for this path
                    self._install_path_flows(path, src, dst, in_port, dst_port)
                else:
                    out_port = ofproto.OFPP_FLOOD
        
        # Forward packet
        actions = [parser.OFPActionOutput(out_port)]
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
        
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)

    # ========================================================================
    # UTILITY FUNCTIONS
    # ========================================================================
    
    def _log_topology_state(self):
        """Log current topology state for debugging"""
        self.logger.info('=' * 60)
        self.logger.info('[TOPOLOGY STATE]')
        self.logger.info('  Switches: %d', len(self.net.nodes()))
        self.logger.info('  Links: %d', len(self.net.edges()))
        self.logger.info('  Known Hosts: %d', len(self.hosts))
        self.logger.info('=' * 60)
