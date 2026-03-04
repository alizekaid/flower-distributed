#!/bin/bash

# Enable job control
set -m

# Clean up previous runs
sudo mn -c

# Set PYTHONPATH to include project root
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Start Mininet in the background
echo "Starting Mininet Topology..."
sudo PYTHONPATH=$PYTHONPATH python3 mininet_topology.py &
MININET_PID=$!

# Wait for Mininet to create the topology file
echo "Waiting for Mininet to initialize and export topology..."
while [ ! -f topology.json ]; do #|| [ ! -s topology.json ]; do
    sleep 1
done
echo "Topology file found!"

# Give Mininet a moment to reach the "wait_for_controller" state
sleep 2

# Start Ryu Controller
echo "Starting Ryu Controller..."
# Use absolute path to ryu-manager in the virtual environment
#PYTHONPATH=$PYTHONPATH /home/alizekaid/Desktop/flower-distributed/flwr-env/bin/ryu-manager flower_controller.py &
#PYTHONPATH=$PYTHONPATH /home/alizekaid/Desktop/flower-distributed/flwr-env/bin/ryu-manager traffic_aware_controller.py &
RYU_PID=$!

# Bring Mininet to foreground so CLI works
echo "Bringing Mininet to foreground..."
fg %1

# Cleanup Ryu when Mininet exits
kill $RYU_PID
