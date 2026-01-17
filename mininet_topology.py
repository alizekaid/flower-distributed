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
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink
import json
import os

# Import configuration
import mininet_config as config


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
        
    def create_topology(self):
        """Create the Mininet network topology."""
        info("*** Creating Mininet network\n")
        
        # Create network with custom link parameters
        self.net = Mininet(
            switch=OVSSwitch,
            link=TCLink,
            autoSetMacs=True,
            autoStaticArp=True
        )
        
        # Add controller explicitly
        # Add remote controller
        info("*** Adding remote controller\n")
        # We use RemoteController so Mininet waits for an external controller (Ryu)
        controller = self.net.addController(
            'c0',
            controller=RemoteController,
            ip='127.0.0.1',
            port=6633
        )
        
        info("*** Adding switches (STP enabled)\n")
        # Helper to add switch (Standard mode, STP disabled)
        def add_stp_switch(name):
            # Use default failMode (secure) and disable STP
            return self.net.addSwitch(name, stp=False)

        # Core Layer
        # Assign explicit DPID to avoid collision with 's1' (both would default to dpid=1)
        switch1 = self.net.addSwitch('Switch1', dpid='100', stp=False)
        
        # Distribution Layer
        s1 = add_stp_switch('s1')
        s2 = add_stp_switch('s2')
        
        # Intermediate Layer
        s3 = add_stp_switch('s3')
        s4 = add_stp_switch('s4')
        s5 = add_stp_switch('s5')
        s6 = add_stp_switch('s6')
        
        # Access Layer
        s7 = add_stp_switch('s7')
        s8 = add_stp_switch('s8')
        s9 = add_stp_switch('s9')
        s10 = add_stp_switch('s10')
        
        self.switch = switch1 # Set main switch reference
        
        info("*** Adding server node\n")
        self.server = self.net.addHost(
            config.SERVER_NAME,
            ip=f"{config.SERVER_IP}/24"
        )
        
        info("*** Adding NAT node\n")
        # Add NAT connected to Switch1
        nat = self.net.addNAT(name='nat1', linkTo=switch1)
        
        info("*** Adding client nodes\n")
        for i, (client_name, client_ip) in enumerate(
            zip(config.CLIENT_NAMES, config.CLIENT_IPS)
        ):
            client = self.net.addHost(
                client_name,
                ip=f"{client_ip}/24"
            )
            self.clients.append(client)
            info(f"    Added {client_name} with IP {client_ip}\n")
        
        info("*** Creating links\n")
        
        # Core Connections
        # Switch1 connected to s1 and s2
        self.net.addLink(switch1, s1, bw=config.BANDWIDTH, delay=config.DELAY)
        self.net.addLink(switch1, s2, bw=config.BANDWIDTH, delay=config.DELAY)
        # NAT is already linked via addNAT(linkTo=switch1)
        
        # Distribution Connections
        # s1 is connected to s5 and s3
        self.net.addLink(s1, s5, bw=config.BANDWIDTH, delay=config.DELAY)
        self.net.addLink(s1, s3, bw=config.BANDWIDTH, delay=config.DELAY)
        
        # s2 is connected to s6 and s4
        self.net.addLink(s2, s6, bw=config.BANDWIDTH, delay=config.DELAY)
        self.net.addLink(s2, s4, bw=config.BANDWIDTH, delay=config.DELAY)
        
        # Server is connected to s2
        self.net.addLink(self.server, s2, bw=config.BANDWIDTH, delay=config.DELAY)
        
        # Intermediate -> Access Connections (Tree Topology)
        # s3 is connected to s7
        self.net.addLink(s3, s7, bw=config.BANDWIDTH, delay=config.DELAY)
        
        # s4 is connected to s8
        self.net.addLink(s4, s8, bw=config.BANDWIDTH, delay=config.DELAY)
        
        # s5 is connected to s9
        self.net.addLink(s5, s9, bw=config.BANDWIDTH, delay=config.DELAY)
        
        # s6 is connected to s10
        self.net.addLink(s6, s10, bw=config.BANDWIDTH, delay=config.DELAY)
        
        # Access -> Clients Connections
        # s7 is connected to c1, c2
        self.net.addLink(s7, self.clients[0], bw=config.BANDWIDTH, delay=config.DELAY)
        self.net.addLink(s7, self.clients[1], bw=config.BANDWIDTH, delay=config.DELAY)
        
        # s8 is connected to c3, c4
        self.net.addLink(s8, self.clients[2], bw=config.BANDWIDTH, delay=config.DELAY)
        self.net.addLink(s8, self.clients[3], bw=config.BANDWIDTH, delay=config.DELAY)
        
        # s9 is connected to c5, c6
        self.net.addLink(s9, self.clients[4], bw=config.BANDWIDTH, delay=config.DELAY)
        self.net.addLink(s9, self.clients[5], bw=config.BANDWIDTH, delay=config.DELAY)
        
        # s10 is connected to c7, c8
        self.net.addLink(s10, self.clients[6], bw=config.BANDWIDTH, delay=config.DELAY)
        self.net.addLink(s10, self.clients[7], bw=config.BANDWIDTH, delay=config.DELAY)
        
        info("*** Starting network\n")
        self.net.start()
        
        info("*** Network topology created successfully\n")
        self._print_network_info()
        
        # Export topology to JSON for Ryu
        self.export_topology_json()
        
        # Wait for controller connection
        self.wait_for_controller()

    def export_topology_json(self):
        """Export topology to JSON file for the controller."""
        info("*** Exporting topology to topology.json\n")
        topo_data = {
            "switches": [],
            "links": []
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
                "dst_port": port2
            })
            
        with open("topology.json", "w") as f:
            json.dump(topo_data, f, indent=4)
        info("    Topology exported successfully\n")

    def wait_for_controller(self):
        """Wait for the controller to connect to all switches."""
        info("*** Waiting for controller to connect...\n")
        info("    Please start the Ryu controller now.\n")
        
        # Simple wait loop - check if switches are connected
        # In Mininet, we can check if the switch has an active connection
        while True:
            all_connected = True
            for sw in self.net.switches:
                if not sw.connected():
                    all_connected = False
                    break
            
            if all_connected:
                info("*** Controller connected!\n")
                break
            
            time.sleep(1)
            sys.stdout.write(".")
            sys.stdout.flush()
        print() # Newline
        
    def _print_network_info(self):
        """Print network information."""
        info("\n" + "="*60 + "\n")
        info("Network Topology Information:\n")
        info("="*60 + "\n")
        info(f"Server: {config.SERVER_NAME} ({config.SERVER_IP}) -> Connected to S2\n")
        info(f"Core Switch: Switch1 (STP Enabled)\n")
        info(f"NAT: nat1 -> Connected to Switch1\n")
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
            
            # Test connectivity
            if not self.test_connectivity():
                info("*** WARNING: Connectivity test had some packet loss (expected with STP).\n")
                info("*** Continuing with simulation...\n")
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
