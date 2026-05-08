"""flower-distributed: A Flower / PyTorch app."""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset, random_split
from torchvision.datasets import CIFAR10
from torchvision.transforms import Compose, Normalize, ToTensor
import torchvision.models as models


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


def get_model(model_name: str):
    """Dynamically get the model architecture based on the provided name."""
    if model_name == "mobilenetv2":
        net = models.mobilenet_v2(weights=None)
        # Adapt classifier for CIFAR-10 (10 classes instead of 1000)
        net.classifier[1] = nn.Linear(net.last_channel, 10)
    elif model_name == "densenet121":
        net = models.densenet121(weights=None)
        # Adapt classifier for CIFAR-10
        net.classifier = nn.Linear(net.classifier.in_features, 10)
    elif model_name == "simple_cnn":
        net = Net()
    else:
        raise ValueError(f"Unknown or unspecified model name: '{model_name}'. Expected 'simple_cnn', 'mobilenetv2', or 'densenet121'.")
    return net


_global_dataset = None  # Cache CIFAR-10 dataset across calls in this process

pytorch_transforms = Compose([ToTensor(), Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])

def load_data(partition_id: int, num_partitions: int):
    """Load a local partition of CIFAR-10 data with Tiered Non-IID logic.
    
    Tiers:
    - 0, 1, 2: GOOD (High IID / Uniform)
    - 3, 4, 5: BAD  (Low IID / Highly Skewed)
    - 6, 7, 8, 9: NORMAL (Mixed / Residual)
    """
    global _global_dataset

    import os
    dataset_root = os.getenv("CIFAR10_DATASET_ROOT", "/home/alizekaid/Desktop/Flower_distributed/data/cifar10")

    if _global_dataset is None:
        _global_dataset = CIFAR10(root=dataset_root, train=True, download=False, transform=pytorch_transforms)

    dataset = _global_dataset
    
    # 1. Group indices by label
    label_indices = {i: [] for i in range(10)}
    for idx in range(len(dataset)):
        _, label = dataset[idx]
        label_indices[label].append(idx)
    
    # 2. Shuffle indices within each label for determinism
    import random
    for i in range(10):
        random.seed(42)
        random.shuffle(label_indices[i])

    partition_indices = []
    
    # 3. TIERED ALLOCATION LOGIC
    # Tier 1: GOOD CLIENTS (0, 1, 2) -> Varying volumes
    if partition_id < 3:
        # c1: 200/class (2000), c2: 400/class (4000), c3: 600/class (6000)
        per_class = [200, 400, 600][partition_id]
        for i in range(10):
            # We take from the beginning, but offsets ensure no overlap if needed
            start = sum([200, 400, 600][:partition_id])
            partition_indices.extend(label_indices[i][start : start + per_class])

    # Tier 2: BAD CLIENTS (3, 4, 5) -> Extreme Skew
    elif partition_id < 6:
        t2_id = partition_id - 3
        # Each client takes varying primary and secondary amounts
        # c4: (2500, 500) -> 3000, c5: (3500, 1500) -> 5000, c6: (2800, 1200) -> 4000
        p_count, s_count = [(2500, 500), (3500, 1500), (2800, 1200)][t2_id]
        p_cls = t2_id * 2
        s_cls = t2_id * 2 + 1
        
        # Primary: Start after Tier 1 (approx 200+400+600 = 1200 used)
        partition_indices.extend(label_indices[p_cls][1500 : 1500 + p_count])
        # Secondary: Start after Tier 1
        partition_indices.extend(label_indices[s_cls][1500 : 1500 + s_count])

        # Tier 3: NORMAL CLIENTS (6, 7, 8, 9) -> Remaining Samples
    else:
        remaining = []
        # Classes 0, 2, 4: All 5000 used by Good(1500) and Bad(3500)
        # Classes 1, 3, 5: 2000 left each (Indices 3000-5000)
        for i in [1, 3, 5]:
            remaining.extend(label_indices[i][3000:5000])
        # Classes 6, 7, 8, 9: 3500 left each (Indices 1500-5000)
        for i in range(6, 10):
            remaining.extend(label_indices[i][1500:5000])
            
        t3_id = partition_id - 6
        import random as py_random
        py_random.seed(42)  # Deterministic sharding
        py_random.shuffle(remaining)
        
        # Heterogeneous shard sizes for Tier 3
        shard_distributions = [1500, 3500, 5000, 2500] # Variable sizes for c7, c8, c9, c10
        shard_size = shard_distributions[t3_id]
        
        # Calculate cumulative start
        current_start = sum(shard_distributions[:t3_id])
        partition_indices = remaining[current_start : current_start + shard_size]

    client_subset = Subset(dataset, partition_indices)

    # Split client data: 80% train, 20% validation
    train_size = int(0.8 * len(client_subset))
    val_size = len(client_subset) - train_size
    train_subset, val_subset = random_split(
        client_subset,
        lengths=[train_size, val_size],
        generator=torch.Generator().manual_seed(42 + partition_id),
    )

    trainloader = DataLoader(train_subset, batch_size=64, shuffle=True)
    testloader = DataLoader(val_subset, batch_size=64)
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

    # AGGRESSIVE MEMORY CLEANUP: Clear VRAM and RAM after training
    import gc
    del net
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

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

    # AGGRESSIVE MEMORY CLEANUP: Clear VRAM and RAM after evaluation
    import gc
    del net
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return loss, accuracy
