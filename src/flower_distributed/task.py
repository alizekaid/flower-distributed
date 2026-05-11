"""flower-distributed: A Flower / PyTorch app."""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset, random_split
from torchvision.datasets import CIFAR10
from torchvision.transforms import Compose, Normalize, ToTensor
import torchvision.models as models
import os
import json
import gc

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
        # CIFAR-10 OPTIMIZATION: Preserve spatial resolution on 32x32 images
        # The standard first layer uses stride=2, which aggressive for small images.
        net.features[0][0].stride = (1, 1)
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
_dataloader_cache = {}  # Cache (trainloader, testloader) per partition

pytorch_transforms = Compose([ToTensor(), Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])

def load_data(partition_id: int, num_partitions: int):
    """Load a local partition of CIFAR-10 data with Tiered Non-IID logic."""
    global _global_dataset, _dataloader_cache

    # 0. Check memory cache first
    cache_key = (partition_id, num_partitions)
    if cache_key in _dataloader_cache:
        print(f"♻️  [Data] Memory Cache HIT for partition {partition_id}")
        return _dataloader_cache[cache_key]

    dataset_root = os.getenv("CIFAR10_DATASET_ROOT", "/home/cihat/Downloads/flower-distributed_article1/flower-distributed/data/cifar10")

    # NEW: 0.1 Check disk cache to survive process restarts
    cache_dir = os.path.join(os.path.dirname(dataset_root), "cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"partition_{partition_id}_{num_partitions}.pt")
    
    partition_indices = None
    if os.path.exists(cache_path):
        print(f"💾 [Data] Disk Cache HIT for partition {partition_id}. Loading indices...")
        partition_indices = torch.load(cache_path, weights_only=False)
    else:
        print(f"📁 [Data] Cache MISS for partition {partition_id}. Calculating TIER logic...")

    if _global_dataset is None:
        _global_dataset = CIFAR10(root=dataset_root, train=True, download=False, transform=pytorch_transforms)
    dataset = _global_dataset

    if partition_indices is None:
        # 1. Group indices by label (OPTIMIZED: Using .targets directly to skip transforms)
        label_indices = {i: [] for i in range(10)}
        for idx, label in enumerate(dataset.targets):
            label_indices[label].append(idx)
        
        # 2. Shuffle indices within each label for determinism
        import random
        for i in range(10):
            random.seed(42)
            random.shuffle(label_indices[i])

        partition_indices = []
        
        # 3. EXACT CLIENT DATA DISTRIBUTION (Unique volumes + mix of IID/Skewed)
        allocations = {
            # HIGH VOLUME (Perfect IID)
            0: {i: 1250 for i in range(10)}, # c1: 12500 total
            1: {i: 1100 for i in range(10)}, # c2: 11000 total
            2: {i: 900 for i in range(10)},  # c3: 9000 total
            
            # MEDIUM VOLUME (Mixed IID / Skewed)
            6: {i: 200 for i in range(10)}, # c7: 2000 total (Perfect IID)
            7: {6: 950, 7: 950}, # c8: 1900 total (Skewed)
            
            # LOW VOLUME (Different Skews & small IID)
            3: {0: 850, 1: 850}, # c4: 1700 total (Skewed)
            9: {8: 750, 9: 750}, # c10: 1500 total (Skewed)
            4: {2: 650, 3: 650}, # c5: 1300 total (Skewed)
            8: {i: 100 for i in range(10)}, # c9: 1000 total (Perfect IID)
            5: {4: 400, 5: 400}, # c6: 800 total (Skewed)
        }
        
        # Sequentially slice indices to absolutely guarantee zero overlaps
        class_offsets = {i: 0 for i in range(10)}

        # We must build the offsets for *all* lower partitions before we slice for the current one
        for pid in range(partition_id):
            client_req = allocations[pid]
            for cls_idx, count in client_req.items():
                class_offsets[cls_idx] += count
                
        # Now slice for the current partition
        my_req = allocations[partition_id]
        for cls_idx, count in my_req.items():
            start_idx = class_offsets[cls_idx]
            partition_indices.extend(label_indices[cls_idx][start_idx : start_idx + count])

        # Save to disk for next time
        torch.save(partition_indices, cache_path)
        print(f"✅ [Data] Partition {partition_id} saved to disk cache.")

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
    
    # NEW: 4. Save metadata to disk for instant telemetry reports
    meta_path = cache_path.replace(".pt", "_meta.json")
    
    # Calculate distribution robustly
    indices = range(len(train_subset))
    curr_subset = train_subset
    while hasattr(curr_subset, 'dataset') and hasattr(curr_subset, 'indices'):
        indices = [curr_subset.indices[i] for i in indices]
        curr_subset = curr_subset.dataset
    
    distribution = {}
    for idx in indices:
        label = curr_subset.targets[idx]
        distribution[str(label)] = distribution.get(str(label), 0) + 1
        
    metadata = {
        "item_count": len(train_subset),
        "iid_distribution": distribution
    }
    with open(meta_path, "w") as f:
        json.dump(metadata, f)
    print(f"📊 [Data] Metadata for partition {partition_id} saved to disk.")

    # Cache the loaders before returning
    _dataloader_cache[cache_key] = (trainloader, testloader)
    return trainloader, testloader


def get_client_metadata(partition_id: int, num_partitions: int):
    """Bypass 341MB dataset load by reading cached metadata from disk."""
    dataset_root = os.getenv("CIFAR10_DATASET_ROOT", "/home/cihat/Downloads/flower-distributed_article1/flower-distributed/data/cifar10")
    cache_dir = os.path.join(os.path.dirname(dataset_root), "cache")
    meta_path = os.path.join(cache_dir, f"partition_{partition_id}_{num_partitions}_meta.json")
    
    if os.path.exists(meta_path):
        print(f"🚀 [Data] Metadata Cache HIT for partition {partition_id}. Bypassing 341MB dataset load!")
        with open(meta_path, "r") as f:
            return json.load(f)
    
    # Fallback: Load data normally to generate the metadata
    print(f"⚠️  [Data] Metadata Cache MISS for partition {partition_id}. Initializing full load...")
    train_loader, _ = load_data(partition_id, num_partitions)
    
    # The load_data call above already saved the meta file, so we can just read it now
    with open(meta_path, "r") as f:
        return json.load(f)


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
