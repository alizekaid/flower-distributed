#!/usr/bin/env python3
"""
Mininet topology for Flower Federated Learning.
3-switch triangle: s1(c1,c2) - s2(c3) - s3(c4) - s4(h1)
All three switches are fully interconnected (K3 graph).
"""

import sys
import time
import argparse
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink
import json
import os

# Import configuration
import network.topology.mininet_config as config
from network.managers.traffic_manager import TrafficManager, add_traffic_commands


class FlowerTopology:
    """Custom Mininet topology for Flower FL."""
    
    def __init__(self, test_only=False):
        """
        Initialize the Flower topology.
        
        Args:
            test_only: If True, only test connectivity without starting FL
        """
        self.test_only = test_only
        self.net = None
        self.server = None
        self.clients = []
        self.switch = None
        self.traffic_manager = None
        
        # Dynamic Bandwidth Scenarios
        # Format: { client_index: [[target_bw_mbps, trigger_after_round], ...] }
        # Triggered when 'client_stats_round_X.json' appears in the logs.
        self.dynamic_bw_scenarios = {
            7: [[2, 1], [5, 3], [7, 4]],   # c8: Crashes to 2Mbps(R1), then improves to 5Mbps(R3) and 7Mbps(R4)
            6: [[3, 1], [8, 3], [12, 4]],  # c7: Crashes to 3Mbps(R1), improved recovery
            5: [[100, 2]],                # c6: Scales up to 100Mbps after R2
            4: [[10, 2], [30, 4]],         # c5: Throttled to 10Mbps(R2), recovers to 30Mbps(R4)
            3: [[15, 3]],                 # c4: Drops to 15Mbps after R3
            2: [[80, 3]],                 # c3: Scales up to 80Mbps after R3
            1: [[5, 4]],                  # c2: Severe drop to 5Mbps after R4
            0: [[60, 4]],                 # c1: Improvements to 60Mbps after R4
        }
        
    def create_topology(self):
        """Create the Mininet network topology."""
        info("*** Creating Mininet network\n")
        
        # Create network with custom link parameters
        self.net = Mininet(
            switch=OVSKernelSwitch,
            controller=RemoteController,
            link=TCLink,
            autoSetMacs=True,
            autoStaticArp=True
        )
        
        # Add controller explicitly
        info("*** Adding remote controller (Ryu)\n")
        self.net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
        
        info("*** Adding 3 explicit switches for Triangle Topology...\n")
        switches = {}
        for i in range(1, 4):
            swname = f's{i}'
            dpid_str = str(i).zfill(16) 
            switches[swname] = self.net.addSwitch(swname, dpid=dpid_str)
        
        self.switch = switches['s1'] # Set main hub reference
        
        info("*** Creating links between switches (Triangle)...\n")
        # Triangle topology: s1-s2, s2-s3, s1-s3
        normal_links = [
            ('s1', 's2'), ('s2', 's3'), ('s1', 's3')
        ]
        
        # Throttled links: shortest-path routes that the BW-aware controller
        # must detect as bottlenecks and route around.
        throttled_links = [
            # Intentionally left empty for now
        ]

        for src, dst in normal_links:
            self.net.addLink(switches[src], switches[dst], bw=config.SWITCH_BW, delay=config.DELAY)

        for src, dst in throttled_links:
            self.net.addLink(switches[src], switches[dst], bw=config.THROTTLED_LINK_BW, delay=config.DELAY)
            info(f"    ⚠️  Throttled link {src}-{dst}: {config.THROTTLED_LINK_BW} Mbps\n")
            
        info("*** Adding hosts (h1, c1-c8)...\n")
        self.server = self.net.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        
        # Clients c1-c8 with re-indexed IPs and heterogeneous resources
        self.clients = []
        # Heterogeneous resource and network profile mapping
        # (CPU_Quota_%, Memory_MB, Core_ID, BW_Mbps, Latency_ms)
        self.resource_profiles = [
            (50, 4096,  0, 40,  20),  # c1: Standard-ish hardware
            (55, 4096,  2, 45,  18),  # c2:
            (60, 4096,  4, 50,  16),  # c3:
            (65, 4096,  6, 55,  14),  # c4:
            (70, 4096,  8, 60,  12),  # c5:
            (75, 4096,  10, 65, 10),  # c6:
            (80, 4096,  12, 70, 8),   # c7:
            (85, 4096,  14, 75, 6),   # c8: Closely ranked powerhouse
        ]
        
        for i, name in enumerate(config.CLIENT_NAMES):
            mac = f'00:00:00:00:00:{i+2:02x}'
            ip = config.CLIENT_IPS[i]
            
            # Use standard Host
            host = self.net.addHost(name, ip=f'{ip}/24', mac=mac)
            self.clients.append(host)
            
        c1, c2, c3, c4, c5, c6, c7, c8 = self.clients
        
        info("*** Connecting hosts to switches...\n")
        # Connect server h1 exclusively to s2
        self.net.addLink(self.server, switches['s2'], bw=config.SERVER_BW, delay=config.DELAY)
        
        # Distribute clients across switches (s1, s3) with physical link throttling
        for i, client in enumerate(self.clients):
            _, _, _, bw, lat = self.resource_profiles[i]
            target_switch = switches['s1'] if i < 4 else switches['s3']
            
            self.net.addLink(client, target_switch, bw=bw, delay=f"{lat}ms")
            info(f"    Link {config.CLIENT_NAMES[i]} <-> {target_switch}: {bw} Mbps, {lat} ms\n")
        
        info("*** Disabling IPv6 and multicast noise...\n")
        for node in self.net.values():
            # Disable IPv6
            node.cmd('sysctl -w net.ipv6.conf.all.disable_ipv6=1')
            node.cmd('sysctl -w net.ipv6.conf.default.disable_ipv6=1')
            node.cmd('sysctl -w net.ipv6.conf.lo.disable_ipv6=1')
            
            # Additional suppression for hosts
            if node in [self.server] + self.clients:
                node.cmd('sysctl -w net.ipv4.icmp_echo_ignore_broadcasts=1')
            
            # Kill avahi-daemon to prevent mDNS noise
            node.cmd('killall -9 avahi-daemon 2>/dev/null')
            
        info("*** Starting network...\n")
        self.net.start()
        self.net.staticArp()
        
        # Initialize Traffic Manager
        self.traffic_manager = TrafficManager(self.net)
        
        # Export topology to JSON for Ryu
        self.export_topology_json()
        
        # Wait for controller connection
        self.wait_for_controller()

    def export_topology_json(self):
        """Export topology to JSON file for the controller."""
        info("*** Exporting topology to topology.json\n")
        topo_data = {
            "switches": [],
            "links": [],
            "hosts": []
        }
        
        # Add switches
        for s in self.net.switches:
            topo_data["switches"].append({"dpid": s.dpid, "name": s.name})
            
        # Add links
        for link in self.net.links:
            node1, node2 = link.intf1.node, link.intf2.node
            port1, port2 = link.intf1.name, link.intf2.name
            # Only include switch-to-switch or switch-to-host links
            topo_data["links"].append({
                "src": node1.name,
                "dst": node2.name,
                "src_port": port1,
                "dst_port": port2,
                "bw": link.intf1.params.get('bw'),
                "delay": link.intf1.params.get('delay')
            })
            
        # Add hosts
        for h in self.net.hosts:
            # Find the switch and port this host is connected to
            # In Mininet, we can check the interfaces
            switch_name = None
            port_id = None
            for intf in h.intfList():
                link = intf.link
                if link:
                    node1, node2 = link.intf1.node, link.intf2.node
                    if node1 == h:
                        other = node2
                        other_intf = link.intf2
                    else:
                        other = node1
                        other_intf = link.intf1
                    
                    if isinstance(other, OVSSwitch):
                        switch_name = other.name
                        # Get port number from the switch interface
                        port_id = other.ports[other_intf]
            
            topo_data["hosts"].append({
                "name": h.name,
                "mac": h.MAC(),
                "ip": h.IP(),
                "switch": switch_name,
                "port": port_id
            })
            
        with open("network/topology/topology.json", "w") as f:
            json.dump(topo_data, f, indent=4)
        info("    Topology exported successfully\n")

    def wait_for_controller(self):
        """Wait for all switches to connect to the controller."""
        info("*** Waiting for controller to connect...\n")
        
        start_time = time.time()
        timeout = 30
        
        while time.time() - start_time < timeout:
            all_connected = True
            for sw in self.net.switches:
                if not sw.connected():
                    all_connected = False
                    break
            
            if all_connected:
                info("    All switches connected!\n")
                return True
            
            time.sleep(1)
            sys.stdout.flush()
        print() # Newline
        
    def _print_network_info(self):
        """Print network information."""
        info("\n" + "="*60 + "\n")
        info("Network Topology Information:\n")
        info("="*60 + "\n")
        info(f"Server: {config.SERVER_NAME} ({config.SERVER_IP}) -> Connected to S2\n")
        info("Clients:\n")
        for name, ip in zip(config.CLIENT_NAMES, config.CLIENT_IPS):
            info(f"  - {name} ({ip})\n")
        info("="*60 + "\n\n")
        
    def test_connectivity(self):
        """Test network connectivity."""
        info("*** Testing connectivity with pingall\n")
        loss = self.net.pingAll()
        
        if loss == 0:
            info("*** Connectivity test PASSED: All nodes can reach each other\n")
            return True
        else:
            info(f"*** Connectivity test FAILED: {loss}% packet loss\n")
            return False
    
    def setup_log_directory(self):
        """Create log directory on server node."""
        info("*** Setting up log directory and clearing old telemetry\n")
        self.server.cmd(f"mkdir -p {config.LOG_DIR}")
        for client in self.clients:
            client.cmd(f"mkdir -p {config.LOG_DIR}")
    
    def setup_dataset_mount(self):
        """Setup dataset mount for all nodes."""
        info("*** Setting up dataset mount\n")
        
        dataset_root = config.DATASET_ROOT
        
        # Check if dataset exists
        result = self.server.cmd(f"test -d {dataset_root} && echo 'exists' || echo 'missing'")
        if 'missing' in result:
            info(f"    WARNING: Dataset directory {dataset_root} does not exist!\n")
            info(f"    Please run: python3 download_cifar10.py\n")
            return False
        
        # Verify dataset files exist
        result = self.server.cmd(f"test -f {dataset_root}/cifar-10-batches-py/data_batch_1 && echo 'ok' || echo 'missing'")
        if 'missing' in result:
            info(f"    WARNING: Dataset files not found in {dataset_root}\n")
            info(f"    Please run: python3 download_cifar10.py\n")
            return False
        
        # Set environment variable for all nodes (server and clients)
        info(f"    Dataset found at: {dataset_root}\n")
        info(f"    Setting CIFAR10_DATASET_ROOT environment variable\n")
        
        # Export for server
        self.server.cmd(f"export CIFAR10_DATASET_ROOT={dataset_root}")
        
        # Export for all clients
        for client in self.clients:
            client.cmd(f"export CIFAR10_DATASET_ROOT={dataset_root}")
        
        info("    Dataset mount setup complete\n")
        return True
    
    def start_superlink(self):
        """Start Flower SuperLink on server node."""
        info("*** Starting Flower SuperLink on server node\n")
        
        # Set PATH to include virtual environment binaries
        venv_bin = f"{config.VENV_PATH}/bin"
        cmd = (
            f"export PATH={venv_bin}:$PATH && "
            f"export HF_DATASETS_CACHE={config.HF_CACHE_DIR} && "
            f"export HF_DATASETS_OFFLINE=1 && "
            f"export CIFAR10_DATASET_ROOT={config.DATASET_ROOT} && "
            f"export FLOCK_MODEL={config.FLOCK_MODEL} && "
            f"{config.FLOWER_SUPERLINK_BIN} "
            f"--insecure "
            f"> {config.SERVER_LOG} 2>&1 &"
        )
        
        info(f"    Command: {cmd}\n")
        self.server.cmd(cmd)
        
        # Wait for SuperLink to start
        time.sleep(3)
        
        # Check if SuperLink is running
        result = self.server.cmd("pgrep -f flower-superlink")
        if result.strip():
            info(f"    SuperLink started successfully (PID: {result.strip()})\n")
            info(f"    Logs: {config.SERVER_LOG}\n")
            return True
        else:
            info("    ERROR: SuperLink failed to start\n")
            return False
    
    def start_supernodes(self):
        """Start Flower SuperNodes on client nodes."""
        info("*** Starting Flower SuperNodes on client nodes\n")
        
        # Set PATH to include virtual environment binaries
        venv_bin = f"{config.VENV_PATH}/bin"
        
        for i, (client, client_name) in enumerate(zip(self.clients, config.CLIENT_NAMES)):
            partition_id = i
            log_file = f"{config.CLIENT_LOG_PREFIX}_{i+1}.log"
            
            # Use centralized resource and network profile mapping
            cpu_quota, ram_mb, core_id, bw, lat = self.resource_profiles[i]
            
            # Wrap the client in a systemd-run scope for real Cgroup v2 resource isolation
            systemd_prefix = (
                f"systemd-run --scope --quiet "
                f"-p CPUQuota={cpu_quota}% "
                f"-p AllowedCPUs={core_id} "
                f"-p MemoryMax={ram_mb}M "
            )

            cmd = (
                f"export PATH={venv_bin}:$PATH && "
                f"export HF_DATASETS_CACHE={config.HF_CACHE_DIR} && "
                f"export HF_DATASETS_OFFLINE=1 && "
                f"export CIFAR10_DATASET_ROOT={config.DATASET_ROOT} && "
                f"export FLOCK_MODEL={config.FLOCK_MODEL} && "
                f"export LINK_BW={bw} && "
                f"export LINK_LATENCY={lat}ms && "
                f"export RAM_LIMIT_MB={ram_mb} && "
                f"export CPU_CORE_ID={core_id} && "
                f"{systemd_prefix} "
                f"{config.FLOWER_SUPERNODE_BIN} "
                f"--insecure "
                f"--superlink {config.SERVER_IP}:{config.SUPERLINK_PORT} "
                f"--node-config \"partition-id={partition_id} num-partitions={config.NUM_CLIENTS}\" "
                f"> {log_file} 2>&1 &"
            )
            
            info(f"    Starting {client_name} (partition {partition_id})\n")
            client.cmd(cmd)
        
        # Wait for SuperNodes to connect
        time.sleep(3)
        info("    SuperNodes started\n")
    
    def run_server_app(self):
        """Run the Flower ServerApp using flwr run."""
        info("*** Running Flower ServerApp\n")
        
        cmd = (
            f"cd {config.FLOWER_APP_PATH} && "
            f"{config.FLWR_RUN_BIN} . "
            f"--run-config num-server-rounds=3 "
        )
        
        info(f"    Command: {cmd}\n")
        info("    This will run 3 federated learning rounds...\n")
        
        # Run in foreground to see output
        result = self.server.cmd(cmd)
        info(result)
        
    def schedule_scenario_engine(self):
        """Schedule dynamic bandwidth scenarios in a background thread."""
        import threading
        
        def scenario_runner():
            info("\n[Scenario Engine] Started. Monitoring for End-of-Round triggers...\n")
            applied = set()
            
            # Watch for the creation of client_stats_round_X.json
            log_dir = config.LOG_DIR
            
            while True:
                for client_idx, transitions in self.dynamic_bw_scenarios.items():
                    for target_bw, trigger_round in transitions:
                        if (client_idx, trigger_round) not in applied:
                            trigger_file = os.path.join(log_dir, f"client_stats_round_{trigger_round}.json")
                            if os.path.exists(trigger_file):
                                # Give the logger a tiny moment to flush, then apply change immediately
                                time.sleep(0.5)
                                client_node = self.clients[client_idx]
                                client_name = config.CLIENT_NAMES[client_idx]
                                
                                info(f"\n[Scenario Engine] Triggering scenario for {client_name}: BW -> {target_bw}Mbps (Round {trigger_round} ended)\nmininet> ")
                                
                                try:
                                    # Config the interface dynamically (TCIntf)
                                    client_node.defaultIntf().config(bw=target_bw)
                                    
                                    # Manually update the param dictionary so export_topology_json sees it
                                    client_node.defaultIntf().params['bw'] = target_bw
                                    
                                    # Update the switch side of the link's params as well just in case
                                    if client_node.defaultIntf().link:
                                        client_node.defaultIntf().link.intf1.params['bw'] = target_bw
                                        client_node.defaultIntf().link.intf2.params['bw'] = target_bw
                                    
                                    # Write new BW to tmp file so client_app.py picks it up without iperf
                                    with open(f"/tmp/client_{client_name}_bw.txt", "w") as f:
                                        f.write(str(target_bw))
                                        
                                    applied.add((client_idx, trigger_round))
                                    
                                    # Update the static topology file so stats scripts see the new capacity
                                    self.export_topology_json()
                                except Exception as e:
                                    info(f"\n[Scenario Engine] Error during execution: {e}\nmininet> ")
                
                time.sleep(2)
                
        t = threading.Thread(target=scenario_runner, daemon=True)
        t.start()
    
    def run(self):
        """Main execution flow."""
        try:
            # Create topology
            self.create_topology()
            
            # Test connectivity (Skipping as per user request)
            # if not self.test_connectivity():
            #     info("*** WARNING: Connectivity test had some packet loss (expected with STP).\n")
            #     info("*** Continuing with simulation...\n")
                # Do not exit on partial failure for STP topologies
                # self.cleanup()
                # return
            
            if self.test_only:
                info("*** Test-only mode: Skipping Flower FL execution\n")
                info("*** Entering CLI for manual testing\n")
                CLI(self.net)
                self.cleanup()
                return
            
            # Setup logging
            self.setup_log_directory()
            
            # Setup dataset mount
            if not self.setup_dataset_mount():
                info("*** WARNING: Dataset mount setup failed. Continuing anyway...\n")
                info("*** Make sure to run: python3 download_cifar10.py before starting training\n")
            
            # Start Flower components
            if not self.start_superlink():
                info("*** ERROR: Failed to start SuperLink. Exiting.\n")
                self.cleanup()
                return
            
            self.start_supernodes()
            
            # Start dynamic scenario engine in the background
            self.schedule_scenario_engine()
            
            info("\n" + "="*60 + "\n")
            info("Flower Federated Learning Environment Ready!\n")
            info("="*60 + "\n")
            info(f"SuperLink running on {config.SERVER_IP}:{config.SUPERLINK_PORT}\n")
            info(f"{config.NUM_CLIENTS} SuperNodes connected\n")
            info("\nYou can now:\n")
            info(f"1. Run '{config.SERVER_NAME} {config.VENV_PATH}/bin/flwr run {config.FLOWER_APP_PATH}' to start training\n")
            info("2. Use Mininet CLI commands (pingall, net, dump, etc.)\n")
            info(f"3. Check logs in {config.LOG_DIR}/\n")
            info("="*60 + "\n\n")
            
            # Add traffic commands to CLI
            add_traffic_commands(CLI, self.traffic_manager)
            
            # Enter CLI for monitoring
            CLI(self.net)
            
        except KeyboardInterrupt:
            info("\n*** Interrupted by user\n")
        except Exception as e:
            info(f"\n*** Error: {e}\n")
            import traceback
            traceback.print_exc()
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Clean up the network."""
        if self.net:
            info("*** Stopping network\n")
            try:
                self.net.stop()
            except Exception as e:
                info(f"*** Warning during cleanup: {e}\n")
            info("*** Cleanup complete\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Mininet topology for Flower Federated Learning"
    )
    parser.add_argument(
        '--test-only',
        action='store_true',
        help='Only test topology connectivity without starting FL'
    )
    
    args = parser.parse_args()
    
    # Set logging level
    setLogLevel('info')
    
    # Create and run topology
    topology = FlowerTopology(test_only=args.test_only)
    topology.run()


if __name__ == '__main__':
    # Check if running as root
    import os
    if os.geteuid() != 0:
        print("ERROR: This script must be run as root (use sudo)")
        sys.exit(1)
    
    main()
