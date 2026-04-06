"""flower-distributed: A Flower / PyTorch app."""

import torch
from flwr.app import ArrayRecord, ConfigRecord, Context
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg

import os
from flower_distributed.task import get_model
from flower_distributed.metrics_plotter import MetricsPlotter

# Create ServerApp
app = ServerApp()


@app.main()
def main(grid: Grid, context: Context) -> None:
    """Main entry point for the ServerApp."""

    # Read run config
    fraction_train: float = context.run_config["fraction-train"]
    num_rounds: int = context.run_config["num-server-rounds"]
    lr: float = context.run_config["lr"]

    # Load global model
    model_name = os.environ.get("FLOCK_MODEL")
    if not model_name:
        raise ValueError("FLOCK_MODEL environment variable must be set!")
    
    print(f"\n🚀 FL SERVER: Loading global model [{model_name}]...")
    
    global_model = get_model(model_name)
    actual_class_name = type(global_model).__name__
    print(f"\n🚀 FL SERVER: Successfully instantiated PyTorch Model: [ {actual_class_name} ]...")
    
    arrays = ArrayRecord(global_model.state_dict())

    # Initialize the external metrics plotter
    plotter = MetricsPlotter()

    # Initialize FedAvg strategy
    strategy = FedAvg(
        fraction_train=1.0,
        fraction_evaluate=1.0, 
        evaluate_metrics_aggr_fn=plotter.aggregate_evaluate_metrics
    )

    # Start strategy, run FedAvg round by round manually to track timing
    import time
    
    # We'll use the initial parameters for the first round
    current_arrays = arrays
    
    print(f"\n{'='*60}")
    print(f"Starting Federated Learning: {num_rounds} rounds")
    print(f"{'='*60}\n")
    
    total_start_time = time.time()
    
    # Run the full federated learning session in one go.
    # Running round-by-round in a manual loop can cause the SuperLink to 
    # disconnect and reconnect nodes too frequently, causing timeouts.
    result = strategy.start(
        grid=grid,
        initial_arrays=current_arrays,
        train_config=ConfigRecord({"lr": lr}),
        num_rounds=num_rounds, 
    )
        
    total_duration = time.time() - total_start_time
    print(f"{'='*60}")
    print(f"FL training finished in {total_duration:.2f} seconds")
    print(f"{'='*60}\n")

    # Save final model to disk
    print("\nSaving final model to disk...")
    state_dict = result.arrays.to_torch_state_dict()
    torch.save(state_dict, "final_model.pt")
