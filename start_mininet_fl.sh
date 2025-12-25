#!/bin/bash
# Startup script for Mininet-Flower Federated Learning
# This script checks prerequisites and launches the Mininet topology

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$SCRIPT_DIR/flwr-env"
FLOWER_APP_PATH="$SCRIPT_DIR/flower-distributed"
TOPOLOGY_SCRIPT="$SCRIPT_DIR/mininet_topology.py"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Mininet-Flower Federated Learning Setup${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Function to print error and exit
error_exit() {
    echo -e "${RED}ERROR: $1${NC}" >&2
    exit 1
}

# Function to print success
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

# Function to print warning
print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

# Check if running with --check-only flag
CHECK_ONLY=false
TEST_ONLY=false

for arg in "$@"; do
    case $arg in
        --check-only)
            CHECK_ONLY=true
            shift
            ;;
        --test-only)
            TEST_ONLY=true
            shift
            ;;
        *)
            ;;
    esac
done

echo "Checking prerequisites..."
echo ""

# 1. Check if running as root
if [ "$EUID" -ne 0 ] && [ "$CHECK_ONLY" = false ]; then
    error_exit "This script must be run as root. Use: sudo bash $0"
fi

if [ "$CHECK_ONLY" = false ]; then
    print_success "Running with root privileges"
fi

# 2. Check if Mininet is installed
if ! command -v mn &> /dev/null; then
    error_exit "Mininet is not installed. Install it with: sudo apt-get install mininet"
fi
print_success "Mininet is installed"

# 3. Check if Python virtual environment exists
if [ ! -d "$VENV_PATH" ]; then
    error_exit "Virtual environment not found at $VENV_PATH"
fi
print_success "Virtual environment found"

# 4. Check if Flower is installed in venv
if [ ! -f "$VENV_PATH/bin/flwr" ]; then
    error_exit "Flower is not installed in virtual environment. Run: source flwr-env/bin/activate && pip install flwr"
fi
print_success "Flower is installed"

# 5. Check if Flower app exists
if [ ! -d "$FLOWER_APP_PATH" ]; then
    error_exit "Flower app not found at $FLOWER_APP_PATH"
fi
print_success "Flower app found"

# 6. Check if Flower app is installed
echo "Checking if Flower app is installed..."
source "$VENV_PATH/bin/activate"

if ! python3 -c "import flower_distributed" 2>/dev/null; then
    print_warning "Flower app not installed. Installing now..."
    cd "$FLOWER_APP_PATH"
    pip install -e . || error_exit "Failed to install Flower app"
    cd "$SCRIPT_DIR"
    print_success "Flower app installed"
else
    print_success "Flower app is installed"
fi

# 7. Check if topology script exists
if [ ! -f "$TOPOLOGY_SCRIPT" ]; then
    error_exit "Topology script not found at $TOPOLOGY_SCRIPT"
fi
print_success "Topology script found"

echo ""
echo -e "${GREEN}All prerequisites satisfied!${NC}"
echo ""

# If check-only mode, exit here
if [ "$CHECK_ONLY" = true ]; then
    echo "Check complete. System is ready to run Mininet-Flower."
    exit 0
fi

# Download dataset if needed
echo "Checking/Downloading dataset..."
if ! "$VENV_PATH/bin/python3" "$SCRIPT_DIR/download_dataset.py"; then
    error_exit "Failed to download dataset"
fi
print_success "Dataset ready"

# Clean up any existing Mininet processes
echo "Cleaning up any existing Mininet processes..."
sudo mn -c &> /dev/null || true
print_success "Cleanup complete"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Starting Mininet Topology${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Launch Mininet topology
if [ "$TEST_ONLY" = true ]; then
    echo "Running in TEST-ONLY mode (connectivity test only)..."
    sudo -E python3 "$TOPOLOGY_SCRIPT" --test-only
else
    echo "Launching Mininet with Flower FL..."
    echo ""
    echo "Once the network starts, you can:"
    echo "  - Use Mininet CLI commands (pingall, net, dump, etc.)"
    echo "  - Check logs in /tmp/flower_mininet_logs/"
    echo "  - Run 'server flwr run $FLOWER_APP_PATH' to start FL training"
    echo ""
    echo "Press Ctrl+C in the Mininet CLI to stop the network"
    echo ""
    
    sudo -E python3 "$TOPOLOGY_SCRIPT"
fi

echo ""
echo -e "${GREEN}Mininet topology stopped${NC}"
echo ""
