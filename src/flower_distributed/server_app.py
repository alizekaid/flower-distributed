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
from flower_distributed.utils import min_max_normalize

class CustomFedAvg(FedAvg):
    def __init__(self, plotter=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.plotter = plotter
        self._last_selected_node_ids = []

    def configure_train(
        self, server_round: int, arrays: ArrayRecord, config: ConfigRecord, grid: Grid
    ) -> list[Message]:
        """Configure the next round of training. Custom Client Selection entrypoint."""
        # SLEEP FOR SYNC: Give the Mininet Scenario Engine 2 seconds to apply link changes
        # from the previous round before we poll the new telemetry.
        import time
        time.sleep(2.0)
        
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
            # Poll with 60s timeout to allow resource-constrained clients (c1, c2) to respond
            replies = grid.send_and_receive(query_msgs, timeout=60.0)
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
                
        # === RESOURCE-AWARE SELECTION LOGIC ===
        # 1. Extract and Normalize ALL telemetry components
        raw_rams = [float(s.get("ram_available_mb", 0)) for s in client_stats.values() if "ram_available_mb" in s]
        raw_cpus = [float(s.get("cpu_percent", 100)) for s in client_stats.values() if "cpu_percent" in s]
        
        raw_bws = []
        raw_lats = []
        for s in client_stats.values():
            if "bw_mbps" in s and "latency_ms" in s:
                try:
                    bw = float(str(s["bw_mbps"]).replace('Mbps','').replace('mbps','').strip())
                    lat = float(str(s["latency_ms"]).replace('ms','').strip())
                    raw_bws.append(bw)
                    raw_lats.append(lat)
                except: continue

        # 1.5. Aggregate absolute CPU capacities for normalization
        raw_cpu_absolutes = []
        for s in client_stats.values():
            if "cpu_percent" in s:
                q = float(s.get("cpu_quota", 100))
                l = float(s["cpu_percent"])
                raw_cpu_absolutes.append(q * (1.0 - (l / 100.0)))
        
        # Compute min/max for normalization
        stats_meta = {
            "ram": (min(raw_rams), max(raw_rams)) if raw_rams else (0, 1),
            "cpu": (min(raw_cpu_absolutes), max(raw_cpu_absolutes)) if raw_cpu_absolutes else (0, 100),
            "bw": (min(raw_bws), max(raw_bws)) if raw_bws else (0, 1),
            "lat": (min(raw_lats), max(raw_lats)) if raw_lats else (0, 1),
        }

        # 2. Assign scores to each client
        for cid, stats in client_stats.items():
            if "error" in stats: continue
            
            # RAM score (Higher is better)
            stats["norm_ram"] = min_max_normalize(float(stats.get("ram_available_mb", 0)), *stats_meta["ram"])
            # CPU score (Capacity-Aware: Factor in Total Quota)
            quota = float(stats.get("cpu_quota", 100))
            load = float(stats.get("cpu_percent", 0))
            # Absolute Available Power = Quota * (1.0 - Load/100)
            # E.g. 100% quota at 10% load = 90.0 absolute.
            # E.g. 30% quota at 0% load = 30.0 absolute.
            stats["cpu_absolute"] = quota * (1.0 - (load / 100.0))
            stats["norm_cpu"] = min_max_normalize(stats["cpu_absolute"], *stats_meta["cpu"])
            
            # Network scores
            try:
                bw = float(str(stats.get("bw_mbps", 0)).replace('Mbps','').replace('mbps','').strip())
                lat = float(str(stats.get("latency_ms", 1000)).replace('ms','').strip())
                stats["norm_bw"] = min_max_normalize(bw, *stats_meta["bw"])
                stats["norm_lat"] = min_max_normalize(lat, *stats_meta["lat"], invert=True)
            except Exception as e:
                print(f"⚠️  [Scoring] Failed to parse network data for {alias}: {e}. Scoring 0.0.")
                stats["norm_bw"] = 0.0
                stats["norm_lat"] = 0.0

            # SELECTION STRATEGY LOGIC (Check local, then parent, then ENV)
            strategy = "composite"
            possible_paths = ["strategy.txt", "../strategy.txt", "src/strategy.txt"]
            
            found_file = False
            for p in possible_paths:
                if os.path.exists(p):
                    try:
                        with open(p, "r") as f:
                            strategy = f.read().strip().lower()
                            found_file = True
                            break
                    except: pass
            
            if not found_file:
                strategy = os.environ.get("SELECTION_STRATEGY", "composite").lower()

            if server_round == 1 and cid == list(client_stats.keys())[0]:
                print(f"\n[CustomFedAvg] 📂 Searching in: {os.getcwd()}")
                print(f"[CustomFedAvg] 🎯 ACTIVE SELECTION STRATEGY: {strategy.upper()}")
            
            if strategy == "bandwidth":
                stats["capability_score"] = float(stats["norm_bw"])
            elif strategy == "latency":
                stats["capability_score"] = float(stats["norm_lat"])
            elif strategy == "cpu":
                stats["capability_score"] = float(stats["norm_cpu"])
            elif strategy == "ram":
                stats["capability_score"] = float(stats["norm_ram"])
            else:
                # COMPOSITE CAPABILITY SCORE (Default)
                # Weights: RAM(20%), CPU(40%), BW(20%), Latency(20%)
                stats["capability_score"] = (
                    0.2 * float(stats["norm_ram"]) + 
                    0.4 * float(stats["norm_cpu"]) + 
                    0.2 * float(stats["norm_bw"]) + 
                    0.2 * float(stats["norm_lat"])
                )
            
            # Label the strategy in the stats for logging
            stats["selection_strategy"] = strategy

        # Export the snapshot locally using Semantic Client Names (c1-c8)
        stats_file = f"logs/client_stats_round_{server_round}.json"
        
        export_stats = {}
        for nid, stats in client_stats.items():
            alias = stats.get("client_name", str(nid))
            export_stats[alias] = stats
            
        with open(stats_file, "w") as f:
            json.dump(export_stats, f, indent=4)
            
        if self.plotter:
            self.plotter.record_telemetry(server_round, export_stats)
            
        print(f"[CustomFedAvg] Successfully dumped system telemetry to {stats_file}")
            
        # 3. Filter and Sort valid clients by Capability Score
        valid_clients = [cid for cid, s in client_stats.items() if "error" not in s and "capability_score" in s]
        
        valid_clients.sort(
            key=lambda cid: (float(client_stats[cid]["capability_score"]), random.random()),
            reverse=True
        )
        
        # 4. Dynamic Aggressive Target: Set how many optimal clients to pick each round (e.g., 5)
        target_client_count = 5 
        
        if len(valid_clients) >= target_client_count:
            sampled_cids = valid_clients[:target_client_count]
            print(f"\n[CustomFedAvg] Harvested the Top {target_client_count} superior-capability clients (Weighted Score)!")
        elif len(valid_clients) > 0:
            sampled_cids = valid_clients
            print(f"\n[CustomFedAvg] Extracted all {len(valid_clients)} capable clients.")
        else:
            fallback_size = min(target_client_count, len(available_node_ids))
            sampled_cids = random.sample(available_node_ids, fallback_size)
            print(f"\n[CustomFedAvg] Telemetry unavailable. Booting random fallback selection.")
            
        # Explicitly log the active vs sleeping nodes to console (Sorted by Score)
        print(f"\n[CustomFedAvg] Federated Fleet Ranking (Weighted Score):")
        for node in valid_clients:
            stats = client_stats.get(node, {})
            alias = stats.get("client_name", str(node))
            score = float(stats.get("capability_score", 0))
            n_ram = float(stats.get("norm_ram", 0))
            n_cpu = float(stats.get("norm_cpu", 0))
            n_bw = float(stats.get("norm_bw", 0))
            n_lat = float(stats.get("norm_lat", 0))
            
            status = "[SELECTED]" if node in sampled_cids else "[UNSELECTED]"
            print(f"  {status} -> Client: {alias} | Score: {score:.2f} ({strategy[:3]}) | RAM: {n_ram:.2f} CPU: {n_cpu:.2f} NET: {(n_bw+n_lat)/2:.2f}")
            
        print(f"  [SUMMARY] -> {len(sampled_cids)} clients selected for training, {len(valid_clients) - len(sampled_cids)} clients actively sleeping.\n")

            
        # Securely build the precise messages exclusively targeting our sampled nodes
        config["server-round"] = server_round
        record = RecordDict(
            {self.arrayrecord_key: arrays, self.configrecord_key: config}
        )
        
        # Store selected IDs for the evaluation phase in the same round
        self._last_selected_node_ids = sampled_cids
        
        return list(self._construct_messages(record, sampled_cids, MessageType.TRAIN))

    def configure_evaluate(
        self, server_round: int, arrays: ArrayRecord, config: ConfigRecord, grid: Grid
    ) -> list[Message]:
        """Configure the next round of evaluation. Reuses the training selection."""
        # Selection logic: Only evaluate the clients that participated in training
        # to ensure the 'unselected' ones stay completely idle.
        selected_ids = getattr(self, "_last_selected_node_ids", [])
        
        if not selected_ids:
            return super().configure_evaluate(server_round, arrays, config, grid)

        print(f"[CustomFedAvg] Round {server_round}: Limiting evaluation to the {len(selected_ids)} selected training nodes.")
        
        config["server-round"] = server_round
        record = RecordDict(
            {self.arrayrecord_key: arrays, self.configrecord_key: config}
        )
        return list(self._construct_messages(record, selected_ids, MessageType.EVALUATE))

# Create ServerApp
app = ServerApp()


@app.main()
def main(grid: Grid, context: Context) -> None:
    """Main entry point for the ServerApp."""
    os.makedirs("logs", exist_ok=True)
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

    # Initialize Custom strategy with the plotter
    strategy = CustomFedAvg(
        plotter=plotter,
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
