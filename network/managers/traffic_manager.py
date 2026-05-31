#!/usr/bin/env python3
"""
Traffic Manager for Mininet Flower Topology.
Handles starting and stopping background traffic scenarios using iperf3.
"""

import time
import threading
from mininet.log import info

import random

class DynamicTrafficSession(threading.Thread):
    """
    A professional traffic generator that mimics real-world network fluctuations.
    Uses a random walk (clamped) to ensure changes are fluid, not jumpy.
    """
    def __init__(self, client, server, min_bw=5, max_bw=25, interval=7):
        super().__init__()
        self.client = client
        self.server = server
        self.min_bw = min_bw
        self.max_bw = max_bw
        self.interval = interval
        self.current_bw = random.uniform(min_bw, max_bw)
        self.stop_event = threading.Event()
        self.daemon = True

    def run(self):
        # Start server if not running
        self.server.cmd('pgrep iperf || iperf -s -u &')
        
        while not self.stop_event.is_set():
            # "Drunk Walk" logic: change is between -4 and +4 Mbps
            change = random.uniform(-4, 4)
            self.current_bw = max(self.min_bw, min(self.max_bw, self.current_bw + change))
            
            bw_str = f"{int(self.current_bw)}M"
            info(f"*** [Dynamic] {self.client.name} -> {self.server.name}: Updating to {bw_str}\n")
            
            # Restart iperf with new bandwidth
            self.client.cmd('pkill -9 iperf')
            self.client.cmd(f'iperf -c {self.server.IP()} -u -b {bw_str} -t 3600 &')
            
            # Sleep until next fluctuation (with slight jitter in timing too)
            if self.stop_event.wait(self.interval + random.uniform(-2, 2)):
                break

    def stop(self):
        self.stop_event.set()
        self.client.cmd('pkill -9 iperf')

class TrafficManager:
    def __init__(self, net):
        self.net = net
        self.active_sessions = []
        self.dynamic_sessions = []
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
        # Stop dynamic sessions
        for session in self.dynamic_sessions:
            session.stop()
        self.dynamic_sessions = []
        
        # Kill iperf on all hosts
        for host in self.net.hosts:
            host.cmd('pkill -9 iperf')
        self.active_sessions = []

    def scenario_congested(self, bandwidth="45M"):
        """
        Congest the s1->s2 direct link by sending heavy traffic from c1 to h1.
        Forces path selection to bypass the s1-s2 bottleneck.
        """
        self.start_iperf_session('c1', 'h1', bandwidth)

    def scenario_congest_s3(self, bandwidth="45M"):
        """
        Congest the s3->s2 direct link by sending heavy traffic from c6 to h1.
        Forces path selection to bypass the s3-s2 bottleneck (rerouting via s1).
        """
        self.start_iperf_session('c6', 'h1', bandwidth)

    def scenario_bottleneck(self, bandwidth="13M"):
        """All 4 clients sending traffic to the server (h1)."""
        for i in range(1, 5):
            self.start_iperf_session(f'c{i}', 'h1', bandwidth)

    def scenario_backbone(self, bandwidth="13M"):
        """c1 (s1) -> c4 (s3): congests s1<->s3 backbone link."""
        self.start_iperf_session('c1', 'c4', bandwidth)

    def scenario_cross(self, bandwidth="8M"):
        """
        Send traffic from c4 (s3) -> c3 (s2).
        Shortest paths are 2 hops: s3-s4-s2 or s3-s1-s2.
        Combine with 'congested' (c1->h1) to saturate s1-s4, making the 
        longer s3-s1-s4-s2 path less desirable.
        """
        self.start_iperf_session('c4', 'c3', bandwidth)

    def scenario_stochastic(self, min_bw=5, max_bw=25):
        """
        Realistic dynamic traffic between c1 and c4.
        Fluctuates between min_bw and max_bw.
        """
        client = self.net.get('c1')
        server = self.net.get('c4')
        info(f"*** Starting dynamic stochastic traffic: c1 -> c4 ({min_bw}M to {max_bw}M)\n")
        
        session = DynamicTrafficSession(client, server, min_bw, max_bw)
        session.start()
        self.dynamic_sessions.append(session)

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
        Scenarios: congested, bottleneck, backbone, cross, random, stochastic, stop
          cross      - c4->c3 via s3->s1->s4->s2 (default 8M)
          stochastic - c1->c4 dynamic fluctuating traffic (default 5M-25M)
        """
        args = line.split()
        if not args:
            print("Usage: traffic [congested|bottleneck|backbone|cross|random|stochastic|stop] [bandwidth]")
            return

        command = args[0]
        bandwidth = args[1] if len(args) > 1 else "10M"

        if command == 'stop':
            manager.stop_all_traffic()
        elif command == 'congested':
            manager.scenario_congested(bandwidth)
        elif command == 'congest_s3':
            manager.scenario_congest_s3(bandwidth)
        elif command == 'bottleneck':
            manager.scenario_bottleneck(bandwidth)
        elif command == 'backbone':
            manager.scenario_backbone(bandwidth)
        elif command == 'cross':
            manager.scenario_cross(bandwidth)
        elif command == 'random':
            manager.scenario_random()
        elif command == 'stochastic':
            min_bw = int(args[1]) if len(args) > 1 else 5
            max_bw = int(args[2]) if len(args) > 2 else 25
            manager.scenario_stochastic(min_bw, max_bw)
        else:
            print(f"Unknown traffic command: {command}")

    # Attach to the CLI class
    cli_class.do_traffic = do_traffic
    cli_class.help_traffic = lambda self: print(do_traffic.__doc__)
