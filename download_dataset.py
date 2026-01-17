import os
from torchvision.datasets import CIFAR10
from torchvision.transforms import ToTensor

def download_cifar10():
    # Use the same path as task.py expects
    dataset_root = os.getenv("CIFAR10_DATASET_ROOT", "/home/alizekaid/Desktop/flower-distributed/data/cifar10")
    
    print(f"Downloading CIFAR-10 dataset to {dataset_root}...")
    
    # Download Train set
    CIFAR10(root=dataset_root, train=True, download=True, transform=ToTensor())
    
    # Download Test set
    CIFAR10(root=dataset_root, train=False, download=True, transform=ToTensor())
    
    print("Download complete.")

if __name__ == "__main__":
    download_cifar10()

