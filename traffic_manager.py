#!/usr/bin/env python3
"""
Traffic Manager for Mininet Flower Topology.
Handles starting and stopping background traffic scenarios using iperf3.
"""

import time
import threading
from mininet.log import info

class TrafficManager:
    def __init__(self, net):
        self.net = net
        self.active_sessions = []
        self.stop_event = threading.Event()

    def start_iperf_session(self, client_name, server_name, bandwidth="10M", duration=3600):
        """Start an iperf session between two hosts."""
        try:
            client = self.net.get(client_name)
            server = self.net.get(server_name)
            
            info(f"*** Starting traffic: {client_name} -> {server_name} ({bandwidth})\n")
            
            # Start iperf server in background if not already running
            server.cmd('pgrep iperf || iperf -s -u &')
            
            # Start iperf client
            cmd = f'iperf -c {server.IP()} -u -b {bandwidth} -t {duration}'
            client.cmd(f'{cmd} &')
            
            self.active_sessions.append((client, server))
        except Exception as e:
            info(f"*** Error starting traffic: {e}\n")

    def stop_all_traffic(self):
        """Stop all active iperf sessions."""
        info("*** Stopping all background traffic...\n")
        # Kill iperf on all hosts
        for host in self.net.hosts:
            host.cmd('pkill -9 iperf')
        self.active_sessions = []

    def scenario_congested(self, bandwidth="13M"):
        """
        Congest the s1->s4 direct link by sending heavy traffic from c1 to h1.
        Forces c3 and c4 to reroute through alternate paths (via s2 or s3).
        """
        self.start_iperf_session('c1', 'h1', bandwidth)

    def scenario_bottleneck(self, bandwidth="13M"):
        """All 4 clients sending traffic to the server (h1)."""
        for i in range(1, 5):
            self.start_iperf_session(f'c{i}', 'h1', bandwidth)

    def scenario_backbone(self, bandwidth="13M"):
        """c1 (s1) -> c4 (s3): congests s1<->s3 backbone link."""
        self.start_iperf_session('c1', 'c4', bandwidth)

    def scenario_cross(self, bandwidth="8M"):
        """
        Send traffic from c4 (s3) -> c3 (s2), forcing it through s3->s1->s4->s2
        or s3->s4->s2 depending on available bandwidth.
        Combine with 'congested' to saturate s3<->s4, pushing traffic onto
        the longer s3->s1->s4->s2 path.
        """
        self.start_iperf_session('c4', 'c3', bandwidth)

    def scenario_random(self):
        """Low bandwidth random noise."""
        import random
        # Find all hosts starting with 'c' (c1-c8)
        clients = [h.name for h in self.net.hosts if h.name.startswith('c')]
        if len(clients) < 2: return
        for _ in range(3):
            src, dst = random.sample(clients, 2)
            self.start_iperf_session(src, dst, "5M")

def add_traffic_commands(cli_class, manager):
    """Add custom traffic commands to a Mininet CLI class."""
    
    def do_traffic(self, line):
        """
        Control virtual traffic scenarios.
        Usage: traffic [scenario_name] [bandwidth]
        Scenarios: congested, bottleneck, backbone, cross, random, stop
          cross     - c4->c3 via s3->s1->s4->s2 (default 8M)
        """
        args = line.split()
        if not args:
            print("Usage: traffic [congested|bottleneck|backbone|cross|random|stop] [bandwidth]")
            return

        command = args[0]
        bandwidth = args[1] if len(args) > 1 else "10M"

        if command == 'stop':
            manager.stop_all_traffic()
        elif command == 'congested':
            manager.scenario_congested(bandwidth)
        elif command == 'bottleneck':
            manager.scenario_bottleneck(bandwidth)
        elif command == 'backbone':
            manager.scenario_backbone(bandwidth)
        elif command == 'cross':
            manager.scenario_cross(bandwidth)
        elif command == 'random':
            manager.scenario_random()
        else:
            print(f"Unknown traffic command: {command}")

    # Attach to the CLI class
    cli_class.do_traffic = do_traffic
    cli_class.help_traffic = lambda self: print(do_traffic.__doc__)
