#!/usr/bin/env python3
"""
Mininet topology for Flower Federated Learning.
Creates a network with 1 server, 1 switch, and 4 clients.
Launches Flower SuperLink on server and SuperNodes on clients.
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
import mininet_config as config
from traffic_manager import TrafficManager, add_traffic_commands


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
        links = [
            ('core1', 's1'), ('core1', 's2'),
            ('s1', 's5'), ('s1', 's3'),
            ('s2', 's4'), ('s2', 's6'),
            ('s3', 's7'), ('s3', 's8'),
            ('s4', 's7'), ('s4', 's8'),
            ('s5', 's9'), ('s5', 's10'),
            ('s6', 's9'), ('s6', 's10')
        ]
        
        for src, dst in links:
            self.net.addLink(switches[src], switches[dst], bw=config.SWITCH_BW, delay=config.DELAY)
            
        info("*** Adding hosts (h1, c1-c8)...\n")
        self.server = self.net.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        
        # Clients c1-c8 with re-indexed IPs
        c1 = self.net.addHost('c1', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        c2 = self.net.addHost('c2', ip='10.0.0.3/24', mac='00:00:00:00:00:03')
        c3 = self.net.addHost('c3', ip='10.0.0.4/24', mac='00:00:00:00:00:04')
        c4 = self.net.addHost('c4', ip='10.0.0.5/24', mac='00:00:00:00:00:05')
        c5 = self.net.addHost('c5', ip='10.0.0.6/24', mac='00:00:00:00:00:06')
        c6 = self.net.addHost('c6', ip='10.0.0.7/24', mac='00:00:00:00:00:07')
        c7 = self.net.addHost('c7', ip='10.0.0.8/24', mac='00:00:00:00:00:08')
        c8 = self.net.addHost('c8', ip='10.0.0.9/24', mac='00:00:00:00:00:09')
        
        self.clients = [c1, c2, c3, c4, c5, c6, c7, c8]
        
        info("*** Connecting hosts to switches...\n")
        self.net.addLink(self.server, switches['s2'], bw=config.SERVER_BW, delay=config.DELAY)
        
        # c1, c2 to s7
        self.net.addLink(c1, switches['s7'], bw=config.CLIENT_BW, delay=config.DELAY)
        self.net.addLink(c2, switches['s7'], bw=config.CLIENT_BW, delay=config.DELAY)
        # c3, c4 to s8
        self.net.addLink(c3, switches['s8'], bw=config.CLIENT_BW, delay=config.DELAY)
        self.net.addLink(c4, switches['s8'], bw=config.CLIENT_BW, delay=config.DELAY)
        # c5, c6 to s9
        self.net.addLink(c5, switches['s9'], bw=config.CLIENT_BW, delay=config.DELAY)
        self.net.addLink(c6, switches['s9'], bw=config.CLIENT_BW, delay=config.DELAY)
        # c7, c8 to s10
        self.net.addLink(c7, switches['s10'], bw=config.CLIENT_BW, delay=config.DELAY)
        # Use allowMultiple for c8 to avoid redundancy if needed, but here simple replacement is safer
        self.net.addLink(c8, switches['s10'], bw=config.CLIENT_BW, delay=config.DELAY)
        
        info("*** IPv6 and Multicast noise suppression...\n")
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
            
        with open("topology.json", "w") as f:
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
        info(f"Server: {config.SERVER_NAME} ({config.SERVER_IP}) -> Connected to S5\n")
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
        info("*** Setting up log directory\n")
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
            
            cmd = (
                f"export PATH={venv_bin}:$PATH && "
                f"export HF_DATASETS_CACHE={config.HF_CACHE_DIR} && "
                f"export HF_DATASETS_OFFLINE=1 && "
                f"export CIFAR10_DATASET_ROOT={config.DATASET_ROOT} && "
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
            
            info("\n" + "="*60 + "\n")
            info("Flower Federated Learning Environment Ready!\n")
            info("="*60 + "\n")
            info(f"SuperLink running on {config.SERVER_IP}:{config.SUPERLINK_PORT}\n")
            info(f"{config.NUM_CLIENTS} SuperNodes connected\n")
            info("\nYou can now:\n")
            info(f"1. Run 'server {config.VENV_PATH}/bin/flwr run {config.FLOWER_APP_PATH}' to start training\n")
            info("2. Use Mininet CLI commands (pingall, net, dump, etc.)\n")
            info("3. Check logs in /tmp/flower_mininet_logs/\n")
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
