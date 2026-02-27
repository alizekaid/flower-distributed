import json
import os
import networkx as nx

class Host:
    def __init__(self, name, mac, ip, switch_id, switch_port):
        self.name = name
        self.mac = mac
        self.ip = ip
        self.switch_id = switch_id
        self.switch_port = switch_port

    def __repr__(self):
        return f"Host(name={self.name}, mac={self.mac}, ip={self.ip}, switch={self.switch_id}, port={self.switch_port})"

class HostManager:
    def __init__(self):
        self.HostDict = {} # MAC -> Host object

    def add_host(self, host_obj):
        self.HostDict[host_obj.mac] = host_obj

    def get_host(self, mac):
        return self.HostDict.get(mac)

    def load_from_topo(self, hosts_data):
        for h in hosts_data:
            host_obj = Host(
                name=h['name'],
                mac=h['mac'],
                ip=h['ip'],
                switch_id=h['switch'],
                switch_port=h['port']
            )
            self.add_host(host_obj)

class Link:
    def __init__(self, src, dst, src_port, dst_port):
        self.src = src
        self.dst = dst
        self.src_port = src_port
        self.dst_port = dst_port

    def __repr__(self):
        return f"Link({self.src}:{self.src_port} <-> {self.dst}:{self.dst_port})"

class LinkManager:
    def __init__(self):
        self.links = []

    def add_link(self, link_obj):
        self.links.append(link_obj)

    def load_from_topo(self, links_data):
        for l in links_data:
            # We only care about network links (switch-switch) or all links?
            # User mentioned "links in the network", not clients.
            # But the path finding needs to know where hosts are too.
            link_obj = Link(
                src=l['src'],
                dst=l['dst'],
                src_port=l['src_port'],
                dst_port=l['dst_port']
            )
            self.add_link(link_obj)

class Switch:
    def __init__(self, dpid, name):
        self.dpid = dpid
        self.name = name
        self.port_to_host = {} # port -> host_name

    def add_host_port(self, port, host_name):
        self.port_to_host[port] = host_name

    def __repr__(self):
        return f"Switch(name={self.name}, dpid={self.dpid}, hosts={self.port_to_host})"

class SwitchManager:
    def __init__(self):
        self.SwitchDict = {} # name/dpid -> Switch object

    def add_switch(self, switch_obj):
        self.SwitchDict[switch_obj.name] = switch_obj
        self.SwitchDict[switch_obj.dpid] = switch_obj

    def load_from_topo(self, switches_data, hosts_data):
        for s in switches_data:
            sw_obj = Switch(dpid=s['dpid'], name=s['name'])
            # Find hosts connected to this switch
            for h in hosts_data:
                if h['switch'] == s['name']:
                    sw_obj.add_host_port(h['port'], h['name'])
            self.add_switch(sw_obj)

class PathManager:
    def __init__(self, graph):
        self.graph = graph

    def find_all_paths(self, src, dst):
        try:
            return list(nx.all_simple_paths(self.graph, source=src, target=dst))
        except nx.NetworkXError:
            return []

class AllPaths:
    def __init__(self, graph, links, switch_ids):
        self.graph = graph
        self.links = links
        self.switch_ids = switch_ids # List of switch names

    def get_paths(self):
        """Build paths using the graph and link map."""
        paths = {}
        for src in self.switch_ids:
            for dst in self.switch_ids:
                if src != dst:
                    pm = PathManager(self.graph)
                    paths[(src, dst)] = pm.find_all_paths(src, dst)
        return paths

class NetworkManager:
    """Consolidated manager to be used in RYU."""
    def __init__(self, topology_file="topology.json"):
        self.host_manager = HostManager()
        self.link_manager = LinkManager()
        self.switch_manager = SwitchManager()
        self.graph = nx.Graph()
        self.topology_file = topology_file
        self.all_paths = None

        if os.path.exists(self.topology_file):
            self.load_topology()

    def load_topology(self):
        with open(self.topology_file, 'r') as f:
            data = json.load(f)
        
        self.host_manager.load_from_topo(data.get('hosts', []))
        self.link_manager.load_from_topo(data.get('links', []))
        self.switch_manager.load_from_topo(data.get('switches', []), data.get('hosts', []))
        
        # Build graph
        for s in data.get('switches', []):
            self.graph.add_node(s['name'], dpid=s['dpid'])
        
        for l in data.get('links', []):
            self.graph.add_edge(l['src'], l['dst'], 
                              ports={l['src']: l['src_port'], l['dst']: l['dst_port']})

        switch_names = [s['name'] for s in data.get('switches', [])]
        self.all_paths = AllPaths(self.graph, self.link_manager.links, switch_names)

    def get_all_possible_paths(self):
        if self.all_paths:
            return self.all_paths.get_paths()
        return {}

    def _parse_port(self, p):
        """Convert port strings (e.g. 's1-eth1') to integers."""
        if isinstance(p, int):
            return p
        try:
            return int(str(p).split('eth')[-1])
        except (ValueError, IndexError):
            return p

    def _build_path_structure(self, sw_path, src_port_str=None, dst_port_str=None):
        """
        Internal helper to convert a list of switch names into a path structure with ports.
        """
        path_with_ports = []

        for i, sw_name in enumerate(sw_path):
            sw_obj = self.switch_manager.SwitchDict.get(sw_name)
            if not sw_obj: continue
            
            dpid_int = int(sw_obj.dpid, 16)
            
            # Determine in_port
            if i == 0:
                # First switch: in_port is the host's port or None (if switch-to-switch only)
                in_port_str = src_port_str
            else:
                # Middle/Last switch: in_port comes from the link with previous switch
                prev_sw = sw_path[i-1]
                edge_data = self.graph.get_edge_data(prev_sw, sw_name)
                in_port_str = edge_data['ports'][sw_name]

            # Determine out_port
            if i == len(sw_path) - 1:
                # Last switch: out_port is the host's port or None
                out_port_str = dst_port_str
            else:
                # First/Middle switch: out_port comes from the link with next switch
                next_sw = sw_path[i+1]
                edge_data = self.graph.get_edge_data(sw_name, next_sw)
                out_port_str = edge_data['ports'][sw_name]

            in_port = self._parse_port(in_port_str)
            out_port = self._parse_port(out_port_str)

            path_with_ports.append({
                'name': sw_name,
                'dpid': dpid_int,
                'in_port': in_port,
                'out_port': out_port
            })

        return path_with_ports

    def get_path_with_ports(self, src_mac, dst_mac):
        """
        Returns the SHORTEST proactive path structure:
        [ {'dpid': dpid, 'in_port': p1, 'out_port': p2}, ... ]
        """
        src_host = self.host_manager.get_host(src_mac)
        dst_host = self.host_manager.get_host(dst_mac)

        if not src_host or not dst_host:
            return None

        try:
            sw_path = nx.shortest_path(self.graph, source=src_host.switch_id, target=dst_host.switch_id)
        except nx.NetworkXNoPath:
            return None

        return self._build_path_structure(sw_path, src_host.switch_port, dst_host.switch_port)

    def get_all_paths_between_switches(self, src_sw_name, dst_sw_name):
        """
        Finds ALL simple paths between two switch names and returns them with ports.
        Returns: [ [path1_hops], [path2_hops], ... ]
        """
        if src_sw_name not in self.graph or dst_sw_name not in self.graph:
            return []

        try:
            all_sw_paths = list(nx.all_simple_paths(self.graph, source=src_sw_name, target=dst_sw_name))
        except nx.NetworkXError:
            return []

        all_paths_structured = []
        for sw_path in all_sw_paths:
            all_paths_structured.append(self._build_path_structure(sw_path))
        
        return all_paths_structured
