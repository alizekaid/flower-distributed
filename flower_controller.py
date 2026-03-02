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

    def load_topology(self):
        """Deprecated: NetworkManager handles this now."""
        pass

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Store datapath
        self.datapaths[datapath.id] = datapath

        # Install table-miss flow entry
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        self.logger.info("Switch connected: %d", datapath.id)

        # Trigger proactive installation after a short delay to ensure all switches connect
        from threading import Timer
        Timer(5.0, self._install_proactive_flows).start()

    def _install_proactive_flows(self):
        """Pre-install flows for all host-to-host pairs and broadcast traffic."""
        # Check if already installed or installation is underway
        if self.proactive_installed:
            return
        
        num_switches = len(self.network_manager.switch_manager.SwitchDict) // 2
        if len(self.datapaths) < num_switches:
            self.logger.info("Waiting for more switches... (%d/%d)", len(self.datapaths), num_switches)
            from threading import Timer
            Timer(5.0, self._install_proactive_flows).start()
            return

        self.proactive_installed = True # Mark as started
        self.logger.info("Starting Full Proactive Flow Installation...")
        
        hosts = list(self.network_manager.host_manager.HostDict.values())
        
        # 1. All-to-All Unicast paths
        for i in range(len(hosts)):
            for j in range(len(hosts)):
                if i == j: continue
                src = hosts[i]
                dst = hosts[j]
                self.logger.debug("Installing path: %s -> %s", src.name, dst.name)
                self._install_path(src.mac, dst.mac)

        # 2. Broadcast and Multicast handling
        self._install_broadcast_rules()

        self.logger.info("Full Proactive Flow Installation Complete.")

    def _install_broadcast_rules(self):
        """Install rules for ARP (Broadcast) and Discovery (Multicast) traffic."""
        # Common broadcast/multicast MACs
        broadcast_macs = [
            "ff:ff:ff:ff:ff:ff",  # ARP
            "33:33:00:00:00:02", # IPv6 Router Discovery
            "33:33:00:00:00:fb"  # mDNS
        ]

        for datapath in self.datapaths.values():
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser
            
            for mac in broadcast_macs:
                match = parser.OFPMatch(eth_dst=mac)
                # For simplicity, we use FLOOD. 
                # Note: In a looped topology, this would require a spanning tree.
                # Since topology.json may have loops, we use OFPP_FLOOD as a basic mechanism.
                actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
                self.add_flow(datapath, 5, match, actions)
            
            self.logger.info("Broadcast rules installed on switch %d", datapath.id)

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
        """
        Handle packets that didn't match any proactive flow.
        Now that we are 100% proactive, this should only happen for 
        extremely rare or unwanted traffic.
        """
        msg = ev.msg
        datapath = msg.datapath
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        self.logger.info("UNMATCHED PACKET: src=%s dst=%s in_port=%d dpid=%d",
                         eth.src, eth.dst, in_port, datapath.id)
