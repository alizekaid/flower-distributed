"""flower-distributed: A Flower / PyTorch app."""

import torch
from flwr.app import ArrayRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp



# SAFETY GOVERNOR: Restrict each client to 1 CPU thread.
# This ensures the OS can always process the Flower heartbeats.
torch.set_num_threads(1)

import os
import time
import random
from flower_distributed.task import get_model, load_data
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
        "eval_loss": eval_loss,
        "eval_acc": eval_acc,
        "num-examples": len(valloader.dataset),
    }
    metric_record = MetricRecord(metrics)
    content = RecordDict({"metrics": metric_record})
    return Message(content=content, reply_to=msg)
