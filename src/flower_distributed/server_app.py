"""flower-distributed: A Flower / PyTorch app."""

import torch
from flwr.app import ArrayRecord, ConfigRecord, Context
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg
from flwr.common import ArrayRecord, ConfigRecord, FitIns, GetPropertiesIns, Message, RecordDict, MessageType
import random
import json

import os
from flower_distributed.task import get_model
from flower_distributed.metrics_plotter import MetricsPlotter

class CustomFedAvg(FedAvg):
    def configure_train(
        self, server_round: int, arrays: ArrayRecord, config: ConfigRecord, grid: Grid
    ) -> list[Message]:
        """Configure the next round of training. Custom Client Selection entrypoint."""
        print(f"\n[CustomFedAvg] Round {server_round}: Triggering Custom Client Selection via grid poll...")
        
        try:
            available_node_ids = list(grid.get_node_ids())
        except Exception as e:
            print(f"[CustomFedAvg] Warning: Failed to fetch grid node IDs securely: {e}")
            return []
            
        # Determine minimum clients needed based on configuration
        if len(available_node_ids) < self.min_available_nodes:
            print(f"[CustomFedAvg] Waiting for {self.min_available_nodes} clients. Currently have {len(available_node_ids)}.")
            return []
            
        print(f"[CustomFedAvg] Dynamically polling {len(available_node_ids)} connected clients for telemetry...")
        
        # Build distinct pseudo-properties poll
        query_msgs = []
        for node_id in available_node_ids:
            msg = Message(
                content=RecordDict({}),
                message_type="query.get_properties",
                dst_node_id=node_id,
            )
            query_msgs.append(msg)
            
        client_stats = {}
        try:
            # Poll each connected client using the out-of-band send_and_receive engine
            replies = grid.send_and_receive(query_msgs, timeout=15.0)
            for reply in replies:
                node_id = reply.metadata.src_node_id
                if reply.has_error():
                    print(f"[CustomFedAvg] Node {node_id} reply error: {reply.error.reason}")
                    continue
                if "telemetry" in reply.content:
                    telemetry_record = reply.content["telemetry"]
                    client_stats[node_id] = {k: v for k, v in telemetry_record.items()}
                else:
                    client_stats[node_id] = {"error": "no telemetry framework exposed"}
        except Exception as e:
            print(f"[CustomFedAvg] Failed to execute Grid query: {e}")
                
        # Export the snapshot locally using Semantic Client Names (c1-c8)
        stats_file = f"logs/client_stats_round_{server_round}.json"
        
        if not os.path.exists("logs"):
            os.makedirs("logs")
            
        export_stats = {}
        for nid, stats in client_stats.items():
            # Retrieve 'client_name' if available, otherwise fallback to the Node ID
            alias = stats.get("client_name", str(nid))
            export_stats[alias] = stats
            
        with open(stats_file, "w") as f:
            json.dump(export_stats, f, indent=4)
            
        print(f"[CustomFedAvg] Successfully dumped system telemetry to {stats_file}")
            
        # === RESOURCE-AWARE SELECTION LOGIC ===
        # 1. Filter out clients that broke or returned errors during the polling
        valid_clients = []
        for cid in available_node_ids:
            stats = client_stats.get(cid, {})
            if "error" not in stats and "cpu_percent" in stats:
                valid_clients.append(cid)
                
        # 2. Sort the valid clients securely by their hardware availability 
        # (Lower combined CPU and RAM percentage means more processing power available)
        # We append a random.random() tie-breaker because all Mininet nodes share one physical kernel 
        # and report IDENTICAL 12GB ram loads via psutil! This forces "tied" identical nodes to rotate evenly.
        valid_clients.sort(
            key=lambda cid: (client_stats[cid]["cpu_percent"] + client_stats[cid]["ram_percent"], random.random())
        )
        
        # 3. Dynamic Aggressive Target: Set how many optimal clients to pick each round (e.g., 3, 4, or 5)
        target_client_count = 3 
        
        if len(valid_clients) >= target_client_count:
            # Extract the Top N highest performing clients natively!
            sampled_cids = valid_clients[:target_client_count]
            print(f"\n[CustomFedAvg] Harvested the Top {target_client_count} superior-capability clients!")
        elif len(valid_clients) > 0:
            # Use whoever is available if less than target
            sampled_cids = valid_clients
            print(f"\n[CustomFedAvg] Extracted all {len(valid_clients)} capable clients.")
        else:
            # Absolute fallback in case telemetry fails entirely (safeguard FL network logic)
            fallback_size = min(target_client_count, len(available_node_ids))
            sampled_cids = random.sample(available_node_ids, fallback_size)
            print(f"\n[CustomFedAvg] Telemetry unavailable. Booting random fallback selection.")
            
        # Explicitly log the active vs sleeping nodes to console using Semantic Names!
        for node in sampled_cids:
            alias = client_stats.get(node, {}).get("client_name", str(node))
            print(f"  [ACTIVE] -> Sending FIT instructions to Client: {alias}")
        print(f"  [ASLEEP] -> {len(available_node_ids) - len(sampled_cids)} clients remain connected but actively sleeping this round.\n")
            
        # Securely build the precise messages exclusively targeting our sampled nodes
        config["server-round"] = server_round
        record = RecordDict(
            {self.arrayrecord_key: arrays, self.configrecord_key: config}
        )
        return list(self._construct_messages(record, sampled_cids, MessageType.TRAIN))

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

    # Initialize Custom strategy for client selection
    strategy = CustomFedAvg(
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
