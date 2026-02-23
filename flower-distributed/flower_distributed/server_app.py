"""flower-distributed: A Flower / PyTorch app."""

import torch
from flwr.app import ArrayRecord, ConfigRecord, Context
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg

from flower_distributed.task import Net

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
    global_model = Net()
    arrays = ArrayRecord(global_model.state_dict())

    # Initialize FedAvg strategy
    strategy = FedAvg(fraction_train=fraction_train)

    # Start strategy, run FedAvg round by round manually to track timing
    import time
    
    # We'll use the initial parameters for the first round
    current_arrays = arrays
    
    print(f"\n{'='*60}")
    print(f"Starting Federated Learning: {num_rounds} rounds")
    print(f"{'='*60}\n")
    
    total_start_time = time.time()
    
    for r in range(1, num_rounds + 1):
        print(f"--- Round {r} starting ---")
        round_start_time = time.time()
        
        # Run a single round
        result = strategy.start(
            grid=grid,
            initial_arrays=current_arrays,
            train_config=ConfigRecord({"lr": lr}),
            num_rounds=1, # One round at a time
        )
        
        round_duration = time.time() - round_start_time
        current_arrays = result.arrays # Update parameters for next round
        
        print(f"✅ Round {r} completed in {round_duration:.2f} seconds\n")
        
    total_duration = time.time() - total_start_time
    print(f"{'='*60}")
    print(f"FL training finished in {total_duration:.2f} seconds")
    print(f"{'='*60}\n")

    # Save final model to disk
    print("\nSaving final model to disk...")
    state_dict = result.arrays.to_torch_state_dict()
    torch.save(state_dict, "final_model.pt")
