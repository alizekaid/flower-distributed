"""
Configuration file for Mininet-Flower integration.
Contains network topology parameters, paths, and FL settings.
"""

# Network Configuration
SERVER_IP = "10.0.0.1"
CLIENT_IPS = [
    "10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5", 
    "10.0.0.6", "10.0.0.7", "10.0.0.8", "10.0.0.9"
]
SWITCH_NAME = "s1"
SERVER_NAME = "h1"
CLIENT_NAMES = [
    "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"
]

# Flower Configuration
FLEET_API_PORT = 9092   # Port for SuperNodes (Clients)
EXEC_API_PORT = 9093    # Port for flwr run (Management)
SUPERLINK_PORT = FLEET_API_PORT # Legacy alias for clients
NUM_CLIENTS = 8

# Paths
VENV_PATH = "./flwr-env"
FLOWER_APP_PATH = "./flower-distributed"
PYTHON_BIN = f"{VENV_PATH}/bin/python3"
FLOWER_SUPERLINK_BIN = f"{VENV_PATH}/bin/flower-superlink"
FLOWER_SUPERNODE_BIN = f"{VENV_PATH}/bin/flower-supernode"
FLWR_RUN_BIN = f"{VENV_PATH}/bin/flwr"

# Network Settings
SERVER_BW = 100  # Mbps for h1 connection
CLIENT_BW = 10   # Mbps for c1-c8 connections
SWITCH_BW = 50   # Mbps for inter-switch connections
DELAY = "5ms"    # Network delay

# Logging
LOG_DIR = "/tmp/flower_mininet_logs"
SERVER_LOG = f"{LOG_DIR}/server.log"
CLIENT_LOG_PREFIX = f"{LOG_DIR}/client"

# Hugging Face Cache
HF_CACHE_DIR = "/home/alizekaid/.cache/huggingface/datasets"

# Dataset Configuration
DATASET_ROOT = "./data/cifar10"
