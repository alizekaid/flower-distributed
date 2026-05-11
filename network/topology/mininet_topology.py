#!/usr/bin/env python3
"""
Mininet topology for Flower Federated Learning.
4-switch diamond: s1(c1,c2) - s2(c3) - s3(c4) - s4(h1)
All four switches are fully interconnected (K4 graph).
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
    
    def __init__(self, test_only=False, non_interactive=False):
        """
        Initialize the Flower topology.
        
        Args:
            test_only: If True, only test connectivity without starting FL
            non_interactive: If True, skip CLI and run server app
        """
        self.test_only = test_only
        self.non_interactive = non_interactive
        self.net = None
        self.server = None
        self.clients = []
        self.switch = None
        self.traffic_manager = None
        
        # Dynamic Bandwidth & Latency Scenarios (Floored at 15 Mbps, No Surges)
        self.dynamic_bw_scenarios = {
            7: [[15, 20, 1], [15, 15, 3], [15, 10, 4]],  # c8 (base 25)
            6: [[15, 25, 1], [15, 15, 3], [15, 10, 4]],  # c7 (base 35)
            5: [[16, 5, 2]],                             # c6 (base 40)
            4: [[15, 20, 2], [15, 10, 4]],               # c5 (base 15)
            3: [[15, 15, 3]],                            # c4 (base 15)
            2: [[18, 8, 3]],                             # c3 (base 15)
            1: [[18, 20, 4]],                            # c2 (base 15)
            0: [[18, 10, 4]],                            # c1 (base 15)
        }
        
        # Inter-Switch Bottleneck Scenarios (Dynamic Interface Throttling)
        # Strategy: Progressive throttling of ONLY shortest-path links.
        # This keeps the 30 Mbps backup core paths (s1-s3, s1-s5) fully open, 
        # allowing FLOCK to demonstrate intelligence while Default FL stays trapped.
        self.dynamic_switch_scenarios = {
            2: [('s2', 's4', 2)],    # Round 2: Phase 1 Group A (Wait for baseline R1)
            6: [('s2', 's4', 0.5)],  # Round 6: Phase 2 Group A (Hard block)
            10: [('s2', 's6', 2)],   # Round 10: Phase 1 Group B 
            14: [('s2', 's6', 0.5)], # Round 14: Phase 2 Group B (Hard block)
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
        
        info("*** Adding 10 switches plus core switch...\n")
        switches = {}
        for i in range(1, 11):
            swname = f's{i}'
            dpid_str = str(i).zfill(16) 
            switches[swname] = self.net.addSwitch(swname, dpid=dpid_str)
        
        # Add core switch
        switches['core1'] = self.net.addSwitch('core1', dpid='0000000000000100')
        
        self.switch = switches['s1'] # Set main hub reference
        
        info("*** Creating links between switches...\n")
        # Normal inter-switch links including formerly throttled shortest paths
        normal_links = [
            ('core1', 's1'), ('core1', 's2'),
            ('s1', 's5'), ('s1', 's3'),
            ('s3', 's7'), ('s3', 's8'),
            ('s4', 's7'), ('s4', 's8'),
            ('s5', 's9'), ('s5', 's10'),
            ('s6', 's9'), ('s6', 's10'),
            ('s2', 's4'), ('s2', 's6')  # Left and Right wing gateways to server
        ]

        for src, dst in normal_links:
            self.net.addLink(switches[src], switches[dst], bw=config.SWITCH_BW, delay=config.DELAY)
            
        info("*** Adding hosts (h1, c1-c8)...\n")
        self.server = self.net.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        
        # Clients c1-c8 with re-indexed IPs and heterogeneous resources
        self.clients = []
        # Heterogeneous resource and network profile mapping
        # (CPU_Quota_%, Memory_MB, Core_ID, BW_Mbps, Latency_ms)
        self.resource_profiles = [
            (90, 2048,  0, 15, 25),  # c1: Data King (High IID, Very Weak HW)
            (90, 4096,  2, 15, 20),  # c2: Data King (High IID, Weak HW)
            (90, 3072,  4, 15, 20),  # c3: Data King (High IID, Weak HW)
            (80, 8192,  6, 15, 15),  # c4: Compute King (Best CPU, Bad IID)
            (55, 7168,   8, 15, 15),  # c5: Compute King (Strong CPU, Bad IID)
            (60, 2048,  10, 40,  2),  # c6: Network Star (Best BW/Lat, Bad IID)
            (45, 3072,  12, 35,  5),  # c7: Network Star (Strong BW/Lat, Med IID)
            (60, 4096,  14, 25, 10),  # c8: All-Rounder (Balanced)
        ]
        
        for i, name in enumerate(config.CLIENT_NAMES):
            mac = f'00:00:00:00:00:{i+2:02x}'
            ip = config.CLIENT_IPS[i]
            
            # Use standard Host
            host = self.net.addHost(name, ip=f'{ip}/24', mac=mac)
            self.clients.append(host)
        
        info("*** Connecting hosts to switches...\n")
        # Connect server h1 exclusively to s2
        self.net.addLink(self.server, switches['s2'], bw=config.SERVER_BW, delay=config.DELAY)
        
        # Distribute clients across specific access switches
        client_to_switch = {
            0: 's7', 1: 's7',
            2: 's8', 3: 's8',
            4: 's9', 5: 's9',
            6: 's10', 7: 's10'
        }
        
        for i, client in enumerate(self.clients):
            _, _, _, bw, lat = self.resource_profiles[i]
            target_switch = switches[client_to_switch.get(i, 's7')]
            
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
                f"export CPU_QUOTA={cpu_quota} && "
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
            f"{config.FLWR_RUN_BIN} run . "
            f"--stream "
            f"--run-config num-server-rounds=20 "
        )
        
        info(f"    Command: {cmd}\n")
        info("    This will run 20 federated learning rounds...\n")
        
        # Run in foreground to see output
        result = self.server.cmd(cmd)
        info(result)
        
    def schedule_scenario_engine(self):
        """Schedule dynamic bandwidth scenarios in a background thread."""
        import threading
        
        def scenario_runner():
            info("\n[Scenario Engine] Started. Monitoring for End-of-Round triggers...\n")
            applied_bw = set()
            applied_traffic = set()
            
            # Watch for the creation of client_stats_round_X.json
            log_dir = config.LOG_DIR
            
            while True:
                # 1. Switch Interface Throttling Scenarios for Controller Rerouting
                for round_trigger, scenarios in self.dynamic_switch_scenarios.items():
                    if round_trigger not in applied_traffic:
                        trigger_file = os.path.join(log_dir, f"client_stats_round_{round_trigger}.json")
                        if os.path.exists(trigger_file):
                            time.sleep(0.5)
                            info(f"\n[Scenario Engine] Triggering Link Throttle for Round {round_trigger}...\n")
                            for sw1_name, sw2_name, target_bw in scenarios:
                                try:
                                    sw1 = self.net.get(sw1_name)
                                    sw2 = self.net.get(sw2_name)
                                    conns = sw1.connectionsTo(sw2)
                                    if conns:
                                        intf1, intf2 = conns[0]
                                        intf1.config(bw=target_bw)
                                        intf2.config(bw=target_bw)
                                        intf1.params['bw'] = target_bw
                                        intf2.params['bw'] = target_bw
                                        info(f"    [Link] Dropped {sw1_name}-{sw2_name} to {target_bw} Mbps\n")
                                except Exception as e:
                                    info(f"    [Error] Failed applying throttle on {sw1_name}-{sw2_name}: {e}\n")
                            
                            applied_traffic.add(round_trigger)
                            # Export the updated link speeds so background routing scripts see the change immediately
                            self.export_topology_json()

                # 2. Link BW adjustment scenarios
                for client_idx, transitions in self.dynamic_bw_scenarios.items():
                    for target_bw, target_lat, trigger_round in transitions:
                        if (client_idx, trigger_round) not in applied_bw:
                            trigger_file = os.path.join(log_dir, f"client_stats_round_{trigger_round}.json")
                            if os.path.exists(trigger_file):
                                # Give the logger a tiny moment to flush, then apply change immediately
                                time.sleep(0.5)
                                client_node = self.clients[client_idx]
                                client_name = config.CLIENT_NAMES[client_idx]
                                
                                info(f"\n[Scenario Engine] Triggering scenario for {client_name}: BW -> {target_bw}Mbps, LAT -> {target_lat}ms (Round {trigger_round} ended)\nmininet> ")
                                
                                try:
                                    # Config the interface dynamically (TCIntf)
                                    client_node.defaultIntf().config(bw=target_bw, delay=f"{target_lat}ms")
                                    
                                    # Manually update the param dictionary so export_topology_json sees it
                                    client_node.defaultIntf().params['bw'] = target_bw
                                    
                                    # Update the switch side of the link's params as well just in case
                                    if client_node.defaultIntf().link:
                                        client_node.defaultIntf().link.intf1.params['bw'] = target_bw
                                        client_node.defaultIntf().link.intf2.params['bw'] = target_bw
                                    
                                    # Write new BW to tmp file so client_app.py picks it up without iperf
                                    with open(f"/tmp/client_{client_name}_bw.txt", "w") as f:
                                        f.write(str(target_bw))
                                        
                                    applied_bw.add((client_idx, trigger_round))
                                    
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
            if self.non_interactive:
                info("\n*** Non-interactive mode: Starting ServerApp immediately...\n")
                self.run_server_app()
            else:
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
    parser.add_argument(
        '--non-interactive',
        action='store_true',
        help='Run training automatically and exit without CLI'
    )
    
    args = parser.parse_args()
    
    # Set logging level
    setLogLevel('info')
    
    # Create and run topology
    topology = FlowerTopology(test_only=args.test_only, non_interactive=args.non_interactive)
    topology.run()


if __name__ == '__main__':
    # Check if running as root
    import os
    if os.geteuid() != 0:
        print("ERROR: This script must be run as root (use sudo)")
        sys.exit(1)
    
    main()
