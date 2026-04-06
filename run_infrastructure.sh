#!/bin/bash

# Enable job control
set -m

# Clean up previous runs
sudo mn -c

# Set PYTHONPATH to include project root
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Check for requested model
if [ -z "$1" ]; then
    echo "ERROR: You must specify a model (e.g., ./run_infrastructure.sh simple_cnn | mobilenetv2 | densenet121)"
    exit 1
fi
export FLOCK_MODEL=$1

# Start Mininet in the background
echo "Starting Mininet Topology with model $FLOCK_MODEL..."
sudo -E PYTHONPATH=$PYTHONPATH python3 network/topology/mininet_topology.py &
MININET_PID=$!

# Wait for Mininet to create the topology file
echo "Waiting for Mininet to initialize and export topology..."
# Search in the new topology directory
while [ ! -f network/topology/topology.json ]; do
    sleep 1
done
echo "Topology file found!"

# Give Mininet a moment to reach the "wait_for_controller" state
sleep 2

# Start Ryu Controller
echo "Starting Ryu Controller..."
# Use absolute path to ryu-manager in the virtual environment
# PYTHONPATH=$PYTHONPATH ./flwr-env/bin/ryu-manager network/controllers/flower_controller.py &
# PYTHONPATH=$PYTHONPATH ./flwr-env/bin/ryu-manager network/controllers/traffic_aware_controller.py &
# PYTHONPATH=$PYTHONPATH ./flwr-env/bin/ryu-manager network/controllers/bw_aware_controller.py &
RYU_PID=$!

# Bring Mininet to foreground so CLI works
echo "Bringing Mininet to foreground..."
fg %1

# Cleanup Ryu when Mininet exits
kill $RYU_PID
