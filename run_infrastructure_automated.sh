#!/bin/bash

# Enable job control
set -m

# Clean up previous runs
sudo mn -c
mkdir -p logs
sudo rm -f logs/client_stats_round_*.json
sudo rm -f /tmp/client_*_bw.txt

# Set PYTHONPATH to include project root
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Check for auto mode
AUTO_MODE=false
if [[ "$*" == *"--auto"* ]]; then
    AUTO_MODE=true
    echo "🤖 [AutoMode] Enabled. System will run until 10 rounds are finished and then exit."
fi

# Check for requested model
if [ -z "$1" ]; then
    echo "ERROR: You must specify a model (e.g., ./run_infrastructure.sh simple_cnn)"
    exit 1
fi
export FLOCK_MODEL=$1

# 1. Start Ryu Controller in the background
echo "Starting Ryu Controller (BW-Aware)..."
# Use absolute path to ryu-manager in the virtual environment
PYTHONPATH=$PYTHONPATH ./flwr-env/bin/ryu-manager network/controllers/bw_aware_controller.py > logs/ryu.log 2>&1 &
RYU_PID=$!

# 2. Start Mininet 
if [ "$AUTO_MODE" = true ]; then
    echo "Starting Mininet Topology in NON-INTERACTIVE mode..."
    sudo -E PYTHONPATH=$PYTHONPATH python3 network/topology/mininet_topology.py --non-interactive
    
    echo "Training complete. Cleaning up..."
    kill $RYU_PID
    sudo mn -c
else
    echo "Starting Mininet Topology with model $FLOCK_MODEL..."
    sudo -E PYTHONPATH=$PYTHONPATH python3 network/topology/mininet_topology.py
    # If not in auto mode, we expect mininet_topology to drop to CLI or exit
    kill $RYU_PID
fi
