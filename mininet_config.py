"""
Configuration file for Mininet-Flower integration.
Contains network topology parameters, paths, and FL settings.
"""

# Network Configuration
SERVER_IP = "10.0.0.1"
CLIENT_IPS = ["10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5"]
SWITCH_NAME = "s1"
SERVER_NAME = "server"
CLIENT_NAMES = ["client1", "client2", "client3", "client4"]

# Flower Configuration
SUPERLINK_PORT = 9092
NUM_CLIENTS = 4

# Paths
VENV_PATH = "/home/alizekaid/Desktop/Flower_distributed/flwr-env"
FLOWER_APP_PATH = "/home/alizekaid/Desktop/Flower_distributed/flower-distributed"
PYTHON_BIN = f"{VENV_PATH}/bin/python3"
FLOWER_SUPERLINK_BIN = f"{VENV_PATH}/bin/flower-superlink"
FLOWER_SUPERNODE_BIN = f"{VENV_PATH}/bin/flower-supernode"
FLWR_RUN_BIN = f"{VENV_PATH}/bin/flwr"

# Network Settings
BANDWIDTH = 100  # Mbps
DELAY = "5ms"    # Network delay

# Logging
LOG_DIR = "/tmp/flower_mininet_logs"
SERVER_LOG = f"{LOG_DIR}/server.log"
CLIENT_LOG_PREFIX = f"{LOG_DIR}/client"

# Hugging Face Cache
HF_CACHE_DIR = "/home/alizekaid/.cache/huggingface/datasets"

# Dataset Configuration
DATASET_ROOT = "/home/alizekaid/Desktop/Flower_distributed/data/cifar10"
