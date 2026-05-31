import os
import time
import matplotlib.pyplot as plt

class MetricsPlotter:
    """
    Standalone visualizer for Federated Learning metrics.
    Aggregates client evaluation metrics and automatically plots
    Loss and Accuracy over time after every round.
    """
    
    def __init__(self, output_dir=None):
        if output_dir is None:
            # Default to 'logs' directory in the project root
            # Assume we're running from the project root
            self.output_dir = os.path.abspath("logs")
        else:
            self.output_dir = os.path.abspath(output_dir)
            
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Track historical metrics over rounds
        self.history = {
            "round": [],
            "loss": [],
            "accuracy": [],
            "round_time": [],
            "per_client_loss": {}, # client_name -> [loss1, loss2, ...]
            "per_client_accuracy": {}, # client_name -> [acc1, acc2, ...]
            "per_client_bw": {},       # client_name -> [bw1, bw2, ...]
            "per_client_lat": {},      # client_name -> [lat1, lat2, ...]
            "per_client_cpu": {},      # client_name -> [cpu1, cpu2, ...]
            "per_client_ram": {},      # client_name -> [ram1, ram2, ...]
            "per_client_iid": {},      # client_name -> [iid1, iid2, ...]
            "per_client_volume": {}    # client_name -> [vol1, vol2, ...]
        }
        
        # Round timing: reset at init and after every round
        self._start_time = time.time()
        self._round_start_time = self._start_time
        
        # Dedicated round timing log file
        self._timing_log_path = os.path.join(self.output_dir, 'round_times.log')
        with open(self._timing_log_path, 'w') as f:
            f.write("round,duration_s,loss,accuracy\n")
        
    def aggregate_evaluate_metrics(self, records, weighting_metric_name: str):
        """
        Custom wrapper for Flower Next's aggregate_metricrecords.
        It runs the default aggregation, extracts the results, plots them, and returns them.
        """
        from flwr.serverapp.strategy.strategy_utils import aggregate_metricrecords
        from flwr.common import MetricRecord
        
        if not records:
            return MetricRecord()
            
        # 1. Run standard Flower aggregation for the global average
        aggregated_metrics = aggregate_metricrecords(records, weighting_metric_name)
        
        # 2. Extract values (clients return 'eval_loss' and 'eval_acc')
        loss = float(aggregated_metrics.get("eval_loss", 0.0))
        acc = float(aggregated_metrics.get("eval_acc", 0.0))
        
        # 3. Compute round duration
        round_num = len(self.history["round"]) + 1
        round_duration = time.time() - self._round_start_time
        
        # 4. Save to global history
        cumulative_time = time.time() - self._start_time
        self.history["round"].append(round_num)
        self.history["loss"].append(loss)
        self.history["accuracy"].append(acc)
        self.history["round_time"].append(cumulative_time)

        # 5. Extract and Save Per-Client Metrics
        current_round_loss = {}
        current_round_acc = {}
        
        for record in records:
            # DEBUG Round 1: Record keys: ['metrics']
            # This confirms that Flower is nesting our metrics under a 'metrics' key.
            
            target_record = record
            if "metrics" in record:
                # Some flwr versions wrap the MetricRecord inside another Record
                target_record = record["metrics"]
            
            # Try to get client_id
            try:
                # Direct indexing is safer for Record types in some flwr versions
                cid = int(target_record["client_id"])
                client_name = f"c{cid}"
                c_loss = float(target_record["eval_loss"])
                c_acc = float(target_record["eval_acc"])
                
                # Save the latest value for this client in this round
                current_round_loss[client_name] = c_loss
                current_round_acc[client_name] = c_acc
                
            except (KeyError, ValueError, TypeError) as e:
                # print(f"DEBUG: Failed to extract from record: {e}")
                continue

        # Now update the persistent history with exactly one value per client
        # First, ensure all clients seen so far are initialized for this round
        all_clients = set(self.history["per_client_loss"].keys()) | set(current_round_loss.keys())
        
        for name in all_clients:
            if name not in self.history["per_client_loss"]:
                # New client discovered: pad previous rounds with 0
                self.history["per_client_loss"][name] = [0.0] * (round_num - 1)
                self.history["per_client_accuracy"][name] = [0.0] * (round_num - 1)
            
            # Append the value from this round (or 0.0 if the client missed this round)
            self.history["per_client_loss"][name].append(current_round_loss.get(name, 0.0))
            self.history["per_client_accuracy"][name].append(current_round_acc.get(name, 0.0))
        
        # 6. Print per-round summary
        print(f"\n{'='*55}")
        print(f"  ⏱  Round {round_num:>2d} completed in {round_duration:.2f}s")
        print(f"       Loss: {loss:.4f}   Accuracy: {acc:.4f}")
        print(f"{'='*55}\n")
        
        # 7. Write timing to dedicated log file
        with open(self._timing_log_path, 'a') as f:
            f.write(f"{round_num},{round_duration:.4f},{loss:.6f},{acc:.6f}\n")
        
        # 8. Reset timer for next round
        self._round_start_time = time.time()
        
        # 9. Plot everything
        self.plot()
        
        return aggregated_metrics
        
    def record_telemetry(self, round_num, export_stats):
        """
        Records system telemetry (BW, Lat, CPU, RAM, IID) for all clients in this round.
        Called from the strategy.
        """
        all_clients = set(self.history["per_client_bw"].keys()) | set(export_stats.keys())
        
        for name in all_clients:
            stats = export_stats.get(name, {})
            
            # Extract raw values from telemetry
            # We use 0.0 as fallback if the client is 'sleeping' this round
            try:
                bw = float(str(stats.get("bw_mbps", 0)).replace('Mbps','').replace('mbps','').strip())
                lat = float(str(stats.get("latency_ms", 0)).replace('ms','').strip())
                cpu = float(stats.get("cpu_percent", 0))
                ram = float(stats.get("ram_available_mb", 0))
            except:
                bw, lat, cpu, ram = 0.0, 0.0, 0.0, 0.0

            # Initialize history for new clients
            if name not in self.history["per_client_bw"]:
                for key in ["per_client_bw", "per_client_lat", "per_client_cpu", "per_client_ram", "per_client_iid", "per_client_volume"]:
                    self.history[key][name] = [0.0] * (round_num - 1)
            
            # Extract IID Score
            iid = float(stats.get("iid_score_raw", 0.0))
            
            # Append values
            self.history["per_client_bw"][name].append(bw)
            self.history["per_client_lat"][name].append(lat)
            self.history["per_client_cpu"][name].append(cpu)
            self.history["per_client_ram"][name].append(ram)
            self.history["per_client_iid"][name].append(iid)
            self.history["per_client_volume"][name].append(float(stats.get("item_count", 0)))

    def plot(self):
        """Generates and saves a modern 4x2-chart PNG of Global, Per-Client, and System metrics."""
        rounds = self.history["round"]
        if not rounds: return

        # Large detailed dashboard
        plt.figure(figsize=(15, 30))
        markers = ['o', 's', 'D', '^', 'v', '<', '>', 'p', '*', 'h']
        
        # Determine the clients to plot (those that have at least some data)
        client_names = sorted(self.history["per_client_loss"].keys())
        
        # Helper to avoid code duplication for per-client subplots
        def plot_client_lines(subplot_pos, title, ylabel, history_key, annotate=False):
            plt.subplot(5, 2, subplot_pos)
            for i, name in enumerate(client_names):
                data = self.history[history_key].get(name, [0.0] * len(rounds))
                # Ensure data length matches rounds (pad if needed)
                if len(data) < len(rounds):
                    data += [0.0] * (len(rounds) - len(data))
                
                plt.plot(rounds, data[:len(rounds)], 
                         label=name, marker=markers[i % len(markers)], alpha=0.8)
                
                # Annotate values above points if requested
                if annotate:
                    for r, v in zip(rounds, data[:len(rounds)]):
                        if v > 0: # Only plot non-zero/active values to reduce clutter
                            plt.annotate(f"{v:.1f}", (r, v), textcoords="offset points", 
                                         xytext=(0,5), ha='center', fontsize=7, alpha=0.6)
            
            plt.title(title, fontsize=12, fontweight='bold')
            plt.xlabel('Round')
            plt.ylabel(ylabel)
            plt.grid(True, linestyle='--', alpha=0.7)
            plt.legend(ncol=2, fontsize='small')

        # Row 1: Global Validation (Weighted Avg)
        plt.subplot(5, 2, 1)
        plt.plot(rounds, self.history["loss"], marker='o', linewidth=3, color='black', label='Weighted Avg')
        
        # Annotate time above points
        for r, l, t in zip(rounds, self.history["loss"], self.history["round_time"]):
            plt.annotate(f"{t:.1f}s", (r, l), textcoords="offset points", xytext=(0,10), ha='center', fontsize=9, fontweight='bold', color='darkred')
            
        plt.title('Global Validation Loss', fontsize=12, fontweight='bold')
        plt.xlabel('Round')
        plt.ylabel('Loss')
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.legend()

        plt.subplot(5, 2, 2)
        plt.plot(rounds, self.history["accuracy"], marker='o', linewidth=3, color='black', label='Weighted Avg')
        
        # Annotate time above points
        for r, a, t in zip(rounds, self.history["accuracy"], self.history["round_time"]):
            plt.annotate(f"{t:.1f}s", (r, a), textcoords="offset points", xytext=(0,10), ha='center', fontsize=9, fontweight='bold', color='darkred')

        plt.title('Global Validation Accuracy', fontsize=12, fontweight='bold')
        plt.xlabel('Round')
        plt.ylabel('Accuracy')
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.legend()

        # Row 2: Per-Client FL Metrics
        plot_client_lines(3, 'Per-Client Evaluation Loss', 'Loss', 'per_client_loss', annotate=False)
        plot_client_lines(4, 'Per-Client Evaluation Accuracy', 'Accuracy', 'per_client_accuracy', annotate=False)

        # Row 3: Network Resources
        plot_client_lines(5, 'Client Link Bandwidth', 'Mbps', 'per_client_bw', annotate=True)
        plot_client_lines(6, 'Client Link Latency', 'ms', 'per_client_lat', annotate=True)

        # Row 4: Hardware Resources
        plot_client_lines(7, 'Client CPU Usage', 'Percent (%)', 'per_client_cpu', annotate=True)
        plot_client_lines(8, 'Client RAM Availability', 'MB', 'per_client_ram', annotate=True)

        # Row 5: Data Diversity & Quantity
        plot_client_lines(9, 'Per-Client Distribution Balance (Entropy)', 'Score', 'per_client_iid', annotate=True)
        
        # New 10th Subplot: 2D ML Distribution (Quantity vs Quality)
        plt.subplot(5, 2, 10)
        max_v = 5200 # Minimum default limit
        for i, name in enumerate(client_names):
            h_data = self.history["per_client_iid"].get(name, [0.0])
            v_data = self.history["per_client_volume"].get(name, [0.0])
            if h_data and v_data:
                # Plot the latest point for each client
                last_h = h_data[-1]
                last_v = v_data[-1]
                if last_v > max_v:
                    max_v = last_v
                plt.scatter(last_v, last_h, label=name, marker=markers[i % len(markers)], s=100)
                plt.annotate(name, (last_v, last_h), textcoords="offset points", xytext=(0,10), ha='center')
        
        plt.title('ML Distribution: Quality vs Quantity', fontsize=12, fontweight='bold')
        plt.xlabel('Volume (Sample Count)')
        plt.ylabel('Balance (Entropy)')
        plt.grid(True, linestyle='--', alpha=0.3)
        
        # Fixed Axis Limits to keep quadrants consistent
        x_max = max_v * 1.1
        plt.xlim(-100, x_max)
        plt.ylim(-0.1, 2.6)
        
        # Bold Quadrant Dividers
        x_div = 2500
        y_div = 1.15
        plt.axhline(y=y_div, color='red', linestyle='--', alpha=0.3) 
        plt.axvline(x=x_div, color='red', linestyle='--', alpha=0.3) 
        
        # Quadrant Labels (Using data coordinates for correct positioning relative to dividers)
        # Centers for left/right and top/bottom
        x_left = (-100 + x_div) / 2
        x_right = (x_div + x_max) / 2
        y_top = (y_div + 2.6) / 2
        y_bottom = (-0.1 + y_div) / 2

        plt.text(x_right, y_top, "ELITE", fontsize=11, color='darkgreen', alpha=0.5, fontweight='bold', ha='center')
        plt.text(x_left, y_top, "FALSE CHAMP", fontsize=11, color='darkorange', alpha=0.5, fontweight='bold', ha='center')
        plt.text(x_right, y_bottom, "BIG & SKEWED", fontsize=11, color='darkred', alpha=0.5, fontweight='bold', ha='center')
        plt.text(x_left, y_bottom, "WEAK", fontsize=11, color='gray', alpha=0.5, fontweight='bold', ha='center')

        plt.tight_layout()
        
        # Save transparently over the old file
        output_path = os.path.join(self.output_dir, 'training_metrics.png')
        plt.savefig(output_path, dpi=150)
        plt.close()
        
        print(f"📊 [MetricsPlotter] Detailed 2x2 Summary saved to {output_path}")
