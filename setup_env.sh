#!/bin/bash
set -e

echo "Cleaning up old environment..."
rm -rf flwr-env

echo "Creating Python 3.11 virtual environment..."
python3.11 -m venv flwr-env
source flwr-env/bin/activate

echo "Upgrading pip and installing build tools..."
pip install --upgrade pip setuptools wheel

echo "Downgrading setuptools for Ryu compatibility..."
pip install "setuptools<60"

echo "Installing Ryu (with no build isolation)..."
pip install ryu --no-build-isolation

echo "Installing Flower Distributed..."
pip install -e flower-distributed/

echo "Installing Torch and Torchvision..."
pip install torch torchvision

echo "Setup complete! Please run 'source flwr-env/bin/activate' to start working."
