"""flower-distributed: A Flower / PyTorch app."""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset, random_split
from torchvision.datasets import CIFAR10
from torchvision.transforms import Compose, Normalize, ToTensor


class Net(nn.Module):
    """Model (simple CNN adapted from 'PyTorch: A 60 Minute Blitz')"""

    def __init__(self):
        super(Net, self).__init__()
        self.conv1 = nn.Conv2d(3, 6, 5)
        self.pool = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(6, 16, 5)
        self.fc1 = nn.Linear(16 * 5 * 5, 120)
        self.fc2 = nn.Linear(120, 84)
        self.fc3 = nn.Linear(84, 10)

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = x.view(-1, 16 * 5 * 5)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        return self.fc3(x)


_global_dataset = None  # Cache CIFAR-10 dataset across calls in this process

pytorch_transforms = Compose([ToTensor(), Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])

def load_data(partition_id: int, num_partitions: int):
    """Load a local partition of CIFAR-10 data (no Hugging Face required).

    This implementation uses `torchvision.datasets.CIFAR10` and partitions
    the training set into `num_partitions` equally sized shards, one per client.
    Each client then splits its shard into an 80/20 train/validation split.
    """
    global _global_dataset

    # Use mounted dataset path (set via environment variable or default to shared location)
    import os
    dataset_root = os.getenv("CIFAR10_DATASET_ROOT", "/home/alizekaid/Desktop/Flower_distributed/data/cifar10")

    # Lazily create and cache the underlying CIFAR-10 training dataset
    if _global_dataset is None:
        _global_dataset = CIFAR10(
            root=dataset_root,
            train=True,
            download=False,  # Disable download - dataset should be pre-mounted
            transform=pytorch_transforms,
        )

    dataset = _global_dataset
    num_samples = len(dataset)
    shard_size = num_samples // num_partitions

    if shard_size == 0:
        raise ValueError(
            f"Number of partitions ({num_partitions}) is larger than the number of "
            f"samples in the dataset ({num_samples})."
        )

    start = partition_id * shard_size
    end = start + shard_size if partition_id < num_partitions - 1 else num_samples

    client_subset = Subset(dataset, list(range(start, end)))

    # Split client data: 80% train, 20% validation
    train_size = int(0.8 * len(client_subset))
    val_size = len(client_subset) - train_size
    train_subset, val_subset = random_split(
        client_subset,
        lengths=[train_size, val_size],
        generator=torch.Generator().manual_seed(42 + partition_id),
    )

    trainloader = DataLoader(train_subset, batch_size=32, shuffle=True)
    testloader = DataLoader(val_subset, batch_size=32)
    return trainloader, testloader


def train(net, trainloader, epochs, lr, device):
    """Train the model on the training set."""
    net.to(device)  # move model to GPU if available
    criterion = torch.nn.CrossEntropyLoss().to(device)
    optimizer = torch.optim.Adam(net.parameters(), lr=lr)
    net.train()
    running_loss = 0.0
    for _ in range(epochs):
        for images, labels in trainloader:
            images = images.to(device)
            labels = labels.to(device)
            optimizer.zero_grad()
            loss = criterion(net(images), labels)
            loss.backward()
            optimizer.step()
            running_loss += loss.item()
    avg_trainloss = running_loss / len(trainloader)
    return avg_trainloss


def test(net, testloader, device):
    """Validate the model on the test set."""
    net.to(device)
    net.eval()
    criterion = torch.nn.CrossEntropyLoss()
    correct, loss = 0, 0.0
    with torch.no_grad():
        for images, labels in testloader:
            images = images.to(device)
            labels = labels.to(device)
            outputs = net(images)
            loss += criterion(outputs, labels).item()
            correct += (torch.max(outputs.data, 1)[1] == labels).sum().item()
    accuracy = correct / len(testloader.dataset)
    loss = loss / len(testloader)
    return loss, accuracy
