#!/bin/bash

# Enable job control
set -m

# Clean up previous runs
sudo mn -c

# Start Mininet in the background
echo "Starting Mininet Topology..."
sudo python3 mininet_topology.py &
MININET_PID=$!

# Wait for Mininet to create the topology file
echo "Waiting for Mininet to initialize and export topology..."
while [ ! -f topology.json ]; do
    sleep 1
done
echo "Topology file found!"

# Give Mininet a moment to reach the "wait_for_controller" state
sleep 2

# Start Ryu Controller
echo "Starting Ryu Controller..."
# Use absolute path to ryu-manager in the virtual environment
/home/alizekaid/Desktop/Flower_distributed/flwr-env/bin/ryu-manager flower_controller.py &
RYU_PID=$!

# Bring Mininet to foreground so CLI works
echo "Bringing Mininet to foreground..."
fg %1

# Cleanup Ryu when Mininet exits
kill $RYU_PID
