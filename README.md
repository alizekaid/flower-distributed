# Flower Distributed Learning: Beginner's Guide

This guide will walk you through setting up and running a Federated Learning system over an SDN-controlled network. Follow these steps carefully.

## 1. Initial Setup

1.  **Clone the project**:
    ```bash
    git clone https://github.com/alizekaid/flower-distributed.git
    cd flower-distributed
    ```

2.  **Create the Environment**:
    Run the setup script to create a virtual environment and install everything automatically.
    ```bash
    chmod +x setup_env.sh
    ./setup_env.sh
    ```

3.  **Enter the Environment**:
    You must do this every time you open a new terminal.
    ```bash
    source flwr-env/bin/activate
    ```

## 2. Configuration (Crucial Step)

Before running, you **must** update the folder paths to match your own computer.

1.  Open `mininet_config.py` and change the following lines (replace `/home/alizekaid/Desktop/` with your own path):
    *   `VENV_PATH`
    *   `FLOWER_APP_PATH`
    *   `HF_CACHE_DIR`
    *   `DATASET_ROOT`

## 3. Download the Data

Download the CIFAR-10 images required for training:
```bash
python download_dataset.py
```

## 4. Running the System

To start the network and the training processes:
```bash
sudo bash run_infrastructure.sh
```

## 5. Traffic Awareness (Optional)

By default, the system uses a standard L2 controller. However, you can enable the **Traffic-Aware Controller** to optimize network paths and improve FL training performance.

### How to Enable:
1.  Open `run_infrastructure.sh`.
2.  Switch the active controller:
    *   Add a `#` at the start of line 27 (disables simple controller).
    *   Remove the `#` from the start of line 28 (enables traffic-aware controller).

### Why use it?
The advanced controller monitors network traffic and uses **Dijkstra's Algorithm** to route Flower training packets through paths with the lowest latency and congestion, reducing packet loss during Federated Learning rounds.

## 6. Monitoring Progress (How to see the logs)

Since the system runs in the background, you won't see training progress in the main terminal. Open **two new terminals** and run these commands to watch what's happening:

*   **Watch the Server (Master)**:
    ```bash
    tail -f /tmp/flower_mininet_logs/server.log
    ```

*   **Watch a Client (Worker)**:
    ```bash
    tail -f /tmp/flower_mininet_logs/client_1.log
    ```

## Project Map
- `run_infrastructure.sh`: The "Start" button for the whole system.
- `traffic_aware_controller.py`: The "Smart" network brain (Dijkstra algorithm).
- `flower_controller.py`: The "Basic" network brain.
- `mininet_topology.py`: Defines how switches and computers are connected.

## Troubleshooting
If you see path errors, double-check your absolute paths in `mininet_config.py`.
