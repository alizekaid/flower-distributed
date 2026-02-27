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
        
        # Initialize the new Network Manager
        self.logger.info("Initializing Network Manager...")
        self.network_manager = NetworkManager(self.topology_file)
        self._print_manager_info()

    def _print_manager_info(self):
        """Log some info from the managers to verify they are working."""
        hosts = self.network_manager.host_manager.HostDict
        self.logger.info("-" * 40)
        self.logger.info("NETWORK MANAGERS INITIALIZED")
        self.logger.info("Number of Hosts: %d", len(hosts))
        self.logger.info("Number of Switches: %d", len(self.network_manager.switch_manager.SwitchDict) // 2)
        
        paths = self.network_manager.get_all_possible_paths()
        self.logger.info("Total switch-to-switch paths calculated: %d", len(paths))
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
        """Pre-install flows for all server-client pairs."""
        # Wait until we have enough switches connected (optional safety check)
        num_switches = len(self.network_manager.switch_manager.SwitchDict) // 2
        if len(self.datapaths) < num_switches:
            self.logger.info("Waiting for more switches... (%d/%d)", len(self.datapaths), num_switches)
            from threading import Timer
            Timer(5.0, self._install_proactive_flows).start()
            return

        self.logger.info("Starting Proactive Flow Installation...")
        
        hosts = self.network_manager.host_manager.HostDict
        server = None
        clients = []

        for host in hosts.values():
            if "server" in host.name:
                server = host
            elif "client" in host.name:
                clients.append(host)

        if not server:
            self.logger.warning("No server found in topology for proactive installation.")
            return

        for client in clients:
            self.logger.info("Installing path: %s <-> %s", server.name, client.name)
            # Server to Client
            self._install_path(server.mac, client.mac)
            # Client to Server
            self._install_path(client.mac, server.mac)

        self.logger.info("Proactive Flow Installation Complete.")

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
        # If you hit this point, it means the switch doesn't know what to do
        # and sent the packet to the controller.
        # For a proactive controller (infrastructure), you might want to 
        # install flows beforehand based on the loaded topology.
        
        # For now, this is a simple learning switch implementation as a fallback
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            # ignore lldp packet
            return
        
        dst = eth.dst
        src = eth.src

        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})

        # learn a mac address to avoid FLOOD next time.
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # install a flow to avoid packet_in next time
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            # verify if we have a valid buffer_id, if yes avoid to send both
            # flow_mod & packet_out
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, 1, match, actions, msg.buffer_id)
                return
            else:
                self.add_flow(datapath, 1, match, actions)
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)
