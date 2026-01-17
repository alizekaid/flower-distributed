# Setup Instructions for Flower Distributed Environment

This guide will help you set up the complete environment for running the Flower Federated Learning system with Mininet.

## Prerequisites

You need to install the following system packages:

### 1. Install Python Virtual Environment Support

```bash
sudo apt update
sudo apt install -y python3.12-venv
```

### 2. Install Mininet (if not already installed)

```bash
sudo apt install -y mininet
```

### 3. Install Open vSwitch (if not already installed)

```bash
sudo apt install -y openvswitch-switch
```

## Environment Setup

### 4. Create Python Virtual Environment

```bash
cd /home/alizekaid/Desktop/flower-distributed
python3 -m venv flwr-env
```

### 5. Activate Virtual Environment and Install Dependencies

```bash
source flwr-env/bin/activate
pip install --upgrade pip
pip install -e flower-distributed/
pip install ryu
```

### 6. Download CIFAR-10 Dataset

```bash
# Still in the activated virtual environment
python3 download_dataset.py
```

This will download the CIFAR-10 dataset to `/home/alizekaid/Desktop/flower-distributed/data/cifar10/`

## Verification

### 7. Verify Installation

Check that all required binaries are installed:

```bash
# Check Flower tools
ls -la flwr-env/bin/flower-*
ls -la flwr-env/bin/flwr
ls -la flwr-env/bin/ryu-manager

# Check dataset
ls -la data/cifar10/cifar-10-batches-py/
```

### 8. Test the Infrastructure

```bash
sudo bash run_infrastructure.sh
```

## Troubleshooting

- **Permission errors**: Make sure to use `sudo` when running `run_infrastructure.sh` as Mininet requires root privileges
- **Controller connection issues**: The Ryu controller should start automatically. Check logs if there are connection issues
- **Dataset not found**: Make sure you ran `python3 download_dataset.py` with the virtual environment activated
- **Path errors**: All paths have been corrected to use `flower-distributed` (lowercase)

## Quick Start Commands

```bash
# One-time setup (run these in order)
sudo apt update
sudo apt install -y python3.12-venv mininet openvswitch-switch
cd /home/alizekaid/Desktop/flower-distributed
python3 -m venv flwr-env
source flwr-env/bin/activate
pip install --upgrade pip
pip install -e flower-distributed/
pip install ryu
python3 download_dataset.py
deactivate

# Run the infrastructure
sudo bash run_infrastructure.sh
```
