"""flower-distributed: A Flower / PyTorch app."""

import torch
from flwr.app import ArrayRecord, Context, Message, MetricRecord, RecordDict, ConfigRecord
from flwr.clientapp import ClientApp



# SAFETY GOVERNOR: Restrict each client to 1 CPU thread.
# This ensures the OS can always process the Flower heartbeats.
torch.set_num_threads(1)

import os
import time
import random
import psutil
import subprocess
import json
from flower_distributed.task import get_model, load_data, get_client_metadata
from flower_distributed.task import test as test_fn
from flower_distributed.task import train as train_fn

# Flower ClientApp
app = ClientApp()


@app.train()
def train(msg: Message, context: Context):
    """Train the model on local data."""

    # Load the model and initialize it with the received weights
    model_name = os.environ.get("FLOCK_MODEL")
    if not model_name:
        raise ValueError("FLOCK_MODEL environment variable must be set!")
    model = get_model(model_name)
    model.load_state_dict(msg.content["arrays"].to_torch_state_dict())
    
    # GPU VERIFICATION: Automatically use CUDA since your GTX 1050 is active!
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    
    print(f"🚀 [Client] Training on device: {device.type.upper() if device.type == 'cuda' else 'CPU'}")

    # Load the data
    partition_id = context.node_config["partition-id"]
    num_partitions = context.node_config["num-partitions"]
    trainloader, _ = load_data(partition_id, num_partitions)

    # Call the training function
    train_loss = train_fn(
        model,
        trainloader,
        context.run_config["local-epochs"],
        msg.content["config"]["lr"],
        device,
    )

    # Construct and return reply Message
    model_record = ArrayRecord(model.state_dict())
    
    # AGGRESSIVE MEMORY CLEANUP: Free model and VRAM before returning
    # This prevents memory accumulation that causes Round 3 crashes on 8GB RAM.
    del model
    import gc
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    metrics = {
        "train_loss": train_loss,
        "num-examples": len(trainloader.dataset),
    }
    metric_record = MetricRecord(metrics)
    content = RecordDict({"arrays": model_record, "metrics": metric_record})
    return Message(content=content, reply_to=msg)


@app.evaluate()
def evaluate(msg: Message, context: Context):
    """Evaluate the model on local data."""

    # Load the model and initialize it with the received weights
    model_name = os.environ.get("FLOCK_MODEL")
    if not model_name:
        raise ValueError("FLOCK_MODEL environment variable must be set!")
    model = get_model(model_name)
    model.load_state_dict(msg.content["arrays"].to_torch_state_dict())
    
    # GPU VERIFICATION: Automatically use CUDA since your GTX 1050 is active!
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    
    print(f"📊 [Client] Evaluating on device: {device.type.upper() if device.type == 'cuda' else 'CPU'}")

    # Load the data
    partition_id = context.node_config["partition-id"]
    num_partitions = context.node_config["num-partitions"]
    _, valloader = load_data(partition_id, num_partitions)

    # Call the evaluation function
    eval_loss, eval_acc = test_fn(
        model,
        valloader,
        device,
    )

    # Construct and return reply Message
    
    # AGGRESSIVE MEMORY CLEANUP: Free model and VRAM before returning
    del model
    import gc
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    metrics = {
        "client_id": partition_id + 1,
        "eval_loss": eval_loss,
        "eval_acc": eval_acc,
        "num-examples": len(valloader.dataset),
    }
    metric_record = MetricRecord(metrics)
    content = RecordDict({"metrics": metric_record})
    return Message(content=content, reply_to=msg)


# Global cache for telemetry to avoid O(N) dataset scanning every round
_cached_label_distribution = None

def build_telemetry_msg(msg: Message, context: Context):
    """Build system telemetry with local data quality (Cached O(1))."""
    global _cached_label_distribution
    import psutil
    # 1. Memory: Use static capacity (from env var) minus real per-process usage
    # RAM_LIMIT_MB = the capacity "budget" assigned to this client by the topology
    # rss (Resident Set Size) = actual physical RAM consumed by this process + children
    ram_limit_mb = float(os.environ.get("RAM_LIMIT_MB", 0))
    if ram_limit_mb > 0:
        # Measure THIS process's real physical memory footprint
        this_proc = psutil.Process(os.getpid())
        used_mb = this_proc.memory_info().rss / (1024 * 1024)
        # Include child processes (model, data loaders spawned by Flower)
        for child in this_proc.children(recursive=True):
            try:
                used_mb += child.memory_info().rss / (1024 * 1024)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        ram_available_mb = max(0.0, ram_limit_mb - used_mb)
        ram_percent = min(100.0, (used_mb / ram_limit_mb) * 100)
    else:
        # Fallback for real deployment: use system-wide available RAM
        print(f"⚠️  [Telemetry] RAM_LIMIT_MB not set. Falling back to system-wide metrics.")
        vm = psutil.virtual_memory()
        ram_available_mb = vm.available / (1024 * 1024)
        ram_percent = vm.percent

    # 2. CPU: Read from the explicitly pinned CPU core via psutil
    # Each client is pinned to a specific core (c8 on 14, c7 on 12, etc.)
    # We read exactly that core's load, which accurately reflects this client's partitioned load.
    core_id_str = os.environ.get("CPU_CORE_ID")
    if core_id_str is not None:
        try:
            core_id = int(core_id_str)
            # percpu=True returns a list of CPU %, one for each core.
            # We index into it using our specific core ID.
            all_cores = psutil.cpu_percent(interval=0.1, percpu=True)
            if core_id < len(all_cores):
                cpu_usage = all_cores[core_id]
            else:
                cpu_usage = psutil.cpu_percent(interval=0.1)
        except Exception:
            print(f"⚠️  [Telemetry] Failed to read Core {core_id} load. Falling back to system average.")
            cpu_usage = psutil.cpu_percent(interval=0.1)
    else:
        # Fallback: system-wide psutil
        print(f"⚠️  [Telemetry] CPU_CORE_ID not set. Falling back to system average.")
        cpu_usage = psutil.cpu_percent(interval=0.1)

    partition_id = context.node_config["partition-id"]
    num_partitions = context.node_config["num-partitions"]
    
    # Dynamic Network Probes
    # 1. Latency: Live ping to the server
    try:
        server_ip = os.environ.get("SERVER_ADDRESS", "10.0.0.1").split(':')[0]
        # Run a single ping and parse the result
        ping_res = subprocess.check_output(f"ping -c 1 -W 1 {server_ip} | grep 'time='", shell=True).decode()
        # Extract time=XX.X ms
        lat_val = ping_res.split('time=')[1].split(' ')[0]
        latency_ms = f"{lat_val}ms"
    except Exception:
        # Fallback to static env if ping fails (e.g. server down)
        print(f"⚠️  [Telemetry] Live ping probe failed. Falling back to static LINK_LATENCY.")
        latency_ms = os.environ.get("LINK_LATENCY", "10ms")

    # Map partition-id (0-7) to human-readable strings (c1-c8)
    client_name = f"c{partition_id + 1}"

    # 2. Bandwidth: For now, we still use the 'assigned' BW as the capacity limit,
    # but in a truly dynamic system we would run an iperf test.
    # We dynamically read from Scenario Engine to simulate iperf without overhead
    dynamic_bw_file = f"/tmp/client_{client_name}_bw.txt"
    if os.path.exists(dynamic_bw_file):
        try:
            with open(dynamic_bw_file, "r") as f:
                bw_mbps = f.read().strip()
        except:
            bw_mbps = os.environ.get("LINK_BW", "15")
    else:
        bw_mbps = os.environ.get("LINK_BW", "15")
    
    # INSTANT CACHE RETRIEVAL (Bypassing PyTorch entirely)
    metadata = get_client_metadata(partition_id, num_partitions)
    distribution = metadata["iid_distribution"]
    item_count = metadata["item_count"]
    
    config_data = {
        "client_name": client_name,
        "cpu_percent": float(cpu_usage),
        "cpu_quota": float(os.environ.get("CPU_QUOTA", 100)),
        "ram_percent": float(ram_percent),
        "ram_available_mb": float(ram_available_mb),
        "bw_mbps": bw_mbps,
        "latency_ms": latency_ms,
        "iid_distribution": json.dumps(distribution) if isinstance(distribution, dict) else distribution,
        "item_count": int(item_count),
    }
    return Message(content=RecordDict({"telemetry": ConfigRecord(config_data)}), reply_to=msg)


@app.query()
def get_properties_default(msg: Message, context: Context):
    return build_telemetry_msg(msg, context)

@app.query("get_properties")
def get_properties_named(msg: Message, context: Context):
    return build_telemetry_msg(msg, context)
