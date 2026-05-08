"""flower-distributed: A Flower / PyTorch app."""

import torch
from flwr.app import ArrayRecord, ConfigRecord, Context
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg
from flwr.common import ArrayRecord, ConfigRecord, FitIns, GetPropertiesIns, Message, RecordDict, MessageType
import random
import json
import os
import math
import time
from flower_distributed.task import get_model
from flower_distributed.metrics_plotter import MetricsPlotter
from flower_distributed.utils import min_max_normalize, calculate_dq_score

class CustomFedAvg(FedAvg):
    def __init__(self, plotter=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.plotter = plotter
        self._last_selected_node_ids = []
        self._selection_timing_log_path = os.path.join("logs", "client_selection_times.log")

    def _log_client_selection_timing(
        self,
        server_round,
        *,
        node_discovery_s=0.0,
        telemetry_poll_s=0.0,
        scoring_export_s=0.0,
        ranking_message_s=0.0,
        total_selection_s=0.0,
        available_count=0,
        valid_count=0,
        selected_count=0,
        status="ok",
    ):
        os.makedirs("logs", exist_ok=True)
        write_header = not os.path.exists(self._selection_timing_log_path)
        with open(self._selection_timing_log_path, "a") as f:
            if write_header:
                f.write(
                    "round,node_discovery_s,telemetry_poll_s,scoring_export_s,"
                    "ranking_message_s,total_selection_s,available_clients,"
                    "valid_clients,selected_clients,status\n"
                )
            f.write(
                f"{server_round},{node_discovery_s:.4f},{telemetry_poll_s:.4f},"
                f"{scoring_export_s:.4f},{ranking_message_s:.4f},"
                f"{total_selection_s:.4f},{available_count},{valid_count},"
                f"{selected_count},{status}\n"
            )

        print(
            f"[CustomFedAvg] Round {server_round}: Client selection timing -> "
            f"node_discovery={node_discovery_s:.3f}s, "
            f"telemetry_poll={telemetry_poll_s:.3f}s, "
            f"scoring_export={scoring_export_s:.3f}s, "
            f"ranking_message={ranking_message_s:.3f}s, "
            f"total={total_selection_s:.3f}s "
            f"(available={available_count}, valid={valid_count}, selected={selected_count}, status={status})"
        )

    def configure_train(
        self, server_round: int, arrays: ArrayRecord, config: ConfigRecord, grid: Grid
    ) -> list[Message]:
        """Configure the next round of training. Custom Client Selection entrypoint."""
        # SLEEP FOR SYNC: Give the Mininet Scenario Engine 2 seconds to apply link changes
        # from the previous round before we poll the new telemetry.
        time.sleep(2.0)
        
        selection_start = time.perf_counter()
        node_discovery_s = 0.0
        telemetry_poll_s = 0.0
        scoring_export_s = 0.0
        ranking_message_s = 0.0

        print(f"\n[CustomFedAvg] Round {server_round}: Triggering Custom Client Selection via grid poll...")
        
        try:
            phase_start = time.perf_counter()
            available_node_ids = list(grid.get_node_ids())
            node_discovery_s = time.perf_counter() - phase_start
        except Exception as e:
            print(f"[CustomFedAvg] Warning: Failed to fetch grid node IDs securely: {e}")
            self._log_client_selection_timing(
                server_round,
                node_discovery_s=time.perf_counter() - selection_start,
                total_selection_s=time.perf_counter() - selection_start,
                status="node_discovery_failed",
            )
            return []
            
        # Determine minimum clients needed based on configuration
        if len(available_node_ids) < self.min_available_nodes:
            print(f"[CustomFedAvg] Waiting for {self.min_available_nodes} clients. Currently have {len(available_node_ids)}.")
            self._log_client_selection_timing(
                server_round,
                node_discovery_s=node_discovery_s,
                total_selection_s=time.perf_counter() - selection_start,
                available_count=len(available_node_ids),
                status="insufficient_clients",
            )
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
        phase_start = time.perf_counter()
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
        telemetry_poll_s = time.perf_counter() - phase_start

        phase_start = time.perf_counter()
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
        
        # 1.6 Extract IID Scores & Data Volume
        raw_iid_scores = []
        raw_vols = []
        for s in client_stats.values():
            if "iid_distribution" in s:
                try:
                    dist = json.loads(s["iid_distribution"])
                    counts = list(dist.values())
                    if not counts:
                        raw_iid_scores.append(0.0)
                        s["iid_score_raw"] = 0.0
                        continue
                    # Score = Shannon Entropy
                    # H = -sum(p * log(p))
                    total = sum(counts)
                    entropy = 0.0
                    if total > 0:
                        for count in counts:
                            p = count / total
                            if p > 0:
                                entropy -= p * math.log(p)
                    
                    # Higher entropy = more uniform (max for 10 classes is log(10) ~= 2.3)
                    s["iid_score_raw"] = entropy
                    raw_iid_scores.append(entropy)
                except:
                    raw_iid_scores.append(0.0)
                    s["iid_score_raw"] = 0.0
            
            # Data Volume
            vol = float(s.get("item_count", 0))
            raw_vols.append(vol)
            s["item_count"] = vol

        # Compute min/max for normalization
        stats_meta = {
            "ram": (min(raw_rams), max(raw_rams)) if raw_rams else (0, 1),
            "cpu": (min(raw_cpu_absolutes), max(raw_cpu_absolutes)) if raw_cpu_absolutes else (0, 100),
            "bw": (min(raw_bws), max(raw_bws)) if raw_bws else (0, 1),
            "lat": (min(raw_lats), max(raw_lats)) if raw_lats else (0, 1),
            "iid": (min(raw_iid_scores), max(raw_iid_scores)) if raw_iid_scores else (0, 1),
            "vol": (min(raw_vols), max(raw_vols)) if raw_vols else (0, 1),
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
            stats["cpu_absolute"] = quota * (1.0 - (load / 100.0))
            stats["norm_cpu"] = min_max_normalize(stats["cpu_absolute"], *stats_meta["cpu"])
            
            # IID & Volume scores -> Unified Data Quality (DQ)
            stats["norm_iid"] = min_max_normalize(stats.get("iid_score_raw", 0.0), *stats_meta["iid"])
            stats["norm_vol"] = min_max_normalize(stats.get("item_count", 0.0), *stats_meta["vol"])
            
            # ELITE SCORE Calculation (Euclidean Distance to 1.0, 1.0)
            stats["norm_dq"] = calculate_dq_score(stats["norm_vol"], stats["norm_iid"])
            
            # Network scores
            try:
                bw = float(str(stats.get("bw_mbps", 0)).replace('Mbps','').replace('mbps','').strip())
                lat = float(str(stats.get("latency_ms", 1000)).replace('ms','').strip())
                stats["norm_bw"] = min_max_normalize(bw, *stats_meta["bw"])
                stats["norm_lat"] = min_max_normalize(lat, *stats_meta["lat"], invert=True)
            except Exception as e:
                alias = stats.get("client_name", str(cid))
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
            elif strategy == "dq":
                stats["capability_score"] = float(stats["norm_dq"])
            elif strategy == "grid":
                # GRID SEARCH STRATEGY: Load weights from temporary JSON file
                # Default (fallback) if grid file missing
                w_ram, w_cpu, w_bw, w_lat, w_dq = 0.05, 0.15, 0.10, 0.10, 0.6
                
                grid_file = "grid_weights.json"
                if os.path.exists(grid_file):
                    try:
                        with open(grid_file, "r") as f:
                            gw = json.load(f)
                            w_ram = float(gw.get("w_ram", w_ram))
                            w_cpu = float(gw.get("w_cpu", w_cpu))
                            w_bw = float(gw.get("w_bw", w_bw))
                            w_lat = float(gw.get("w_lat", w_lat))
                            w_dq = float(gw.get("w_dq", w_dq))
                            
                            if server_round == 1 and cid == list(client_stats.keys())[0]:
                                print(f"🎯 [GridSearch] Active Weights: CPU={w_cpu} RAM={w_ram} BW={w_bw} LAT={w_lat} DQ={w_dq}")
                    except Exception as e:
                        print(f"⚠️  [GridSearch] Failed to load weights from {grid_file}: {e}")
                else:
                    if server_round == 1 and cid == list(client_stats.keys())[0]:
                        print(f"⚠️  [GridSearch] Warning: {grid_file} not found. Using fallback weights.")

                stats["capability_score"] = (
                    w_ram * float(stats["norm_ram"]) + 
                    w_cpu * float(stats["norm_cpu"]) + 
                    w_bw * float(stats["norm_bw"]) + 
                    w_lat * float(stats["norm_lat"]) +
                    w_dq * float(stats["norm_dq"])
                )
            else:
                # COMPOSITE CAPABILITY SCORE (Default / Fixed)
                # Fixed Weights: RAM(20%), CPU(20%), BW(20%), Latency(20%), IID(20%)
                w_ram, w_cpu, w_bw, w_lat, w_dq = 0.1, 0.1, 0.1, 0.5, 0.2
                
                stats["capability_score"] = (
                    w_ram * float(stats["norm_ram"]) + 
                    w_cpu * float(stats["norm_cpu"]) + 
                    w_bw * float(stats["norm_bw"]) + 
                    w_lat * float(stats["norm_lat"]) +
                    w_dq * float(stats["norm_dq"])
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
        scoring_export_s = time.perf_counter() - phase_start
            
        phase_start = time.perf_counter()
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
            n_dq = float(stats.get("norm_dq", 0))
            
            status = "[SELECTED]" if node in sampled_cids else "[UNSELECTED]"
            print(f"  {status} -> Client: {alias} | Score: {score:.2f} ({strategy[:3]}) | RAM: {n_ram:.2f} CPU: {n_cpu:.2f} NET: {(n_bw+n_lat)/2:.2f} DQ: {n_dq:.2f}")
            
        print(f"  [SUMMARY] -> {len(sampled_cids)} clients selected for training, {len(valid_clients) - len(sampled_cids)} clients actively sleeping.\n")

            
        # Securely build the precise messages exclusively targeting our sampled nodes
        config["server-round"] = server_round
        record = RecordDict(
            {self.arrayrecord_key: arrays, self.configrecord_key: config}
        )
        
        # Store selected IDs for the evaluation phase in the same round
        self._last_selected_node_ids = sampled_cids
        messages = list(self._construct_messages(record, sampled_cids, MessageType.TRAIN))
        ranking_message_s = time.perf_counter() - phase_start
        total_selection_s = time.perf_counter() - selection_start

        self._log_client_selection_timing(
            server_round,
            node_discovery_s=node_discovery_s,
            telemetry_poll_s=telemetry_poll_s,
            scoring_export_s=scoring_export_s,
            ranking_message_s=ranking_message_s,
            total_selection_s=total_selection_s,
            available_count=len(available_node_ids),
            valid_count=len(valid_clients),
            selected_count=len(sampled_cids),
        )

        return messages

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
