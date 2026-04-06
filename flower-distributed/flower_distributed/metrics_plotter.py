import os
import time
import matplotlib.pyplot as plt

class MetricsPlotter:
    """
    Standalone visualizer for Federated Learning metrics.
    Aggregates client evaluation metrics and automatically plots
    Loss and Accuracy over time after every round.
    """
    
    def __init__(self, output_dir="/tmp/flower_mininet_logs/"):
        self.output_dir = output_dir
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Track historical metrics over rounds
        self.history = {
            "round": [],
            "loss": [],
            "accuracy": [],
            "round_time": []
        }
        
        # Round timing: reset at init and after every round
        self._round_start_time = time.time()
        
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
            
        # 1. Run standard Flower aggregation
        aggregated_metrics = aggregate_metricrecords(records, weighting_metric_name)
        
        # 2. Extract values (clients return 'eval_loss' and 'eval_acc')
        loss = float(aggregated_metrics.get("eval_loss", 0.0))
        acc = float(aggregated_metrics.get("eval_acc", 0.0))
        
        # 3. Compute round duration
        round_num = len(self.history["round"]) + 1
        round_duration = time.time() - self._round_start_time
        
        # 4. Save to history
        self.history["round"].append(round_num)
        self.history["loss"].append(loss)
        self.history["accuracy"].append(acc)
        self.history["round_time"].append(round_duration)
        
        # 5. Print per-round summary
        print(f"\n{'='*55}")
        print(f"  ⏱  Round {round_num:>2d} completed in {round_duration:.2f}s")
        print(f"       Loss: {loss:.4f}   Accuracy: {acc:.4f}")
        print(f"{'='*55}\n")
        
        # 6. Write timing to dedicated log file
        with open(self._timing_log_path, 'a') as f:
            f.write(f"{round_num},{round_duration:.4f},{loss:.6f},{acc:.6f}\n")
        
        # 7. Reset timer for next round
        self._round_start_time = time.time()
        
        # 8. Plot
        self.plot()
        
        return aggregated_metrics
        
    def plot(self):
        """Generates and saves a modern double-chart PNG of Loss and Accuracy."""
        # Build cumulative time x-axis: time at which each round finished
        cumulative_times = []
        total = 0.0
        for t in self.history["round_time"]:
            total += t
            cumulative_times.append(round(total, 2))

        plt.figure(figsize=(12, 5))
        
        # Plot 1: Validation Loss
        plt.subplot(1, 2, 1)
        plt.plot(cumulative_times, self.history["loss"], marker='o', linewidth=2, color='red')
        plt.title('Validation Loss over Time')
        plt.xlabel('Time (s)')
        plt.ylabel('Loss')
        plt.grid(True, linestyle='--', alpha=0.7)
        
        # Plot 2: Validation Accuracy
        plt.subplot(1, 2, 2)
        plt.plot(cumulative_times, self.history["accuracy"], marker='o', linewidth=2, color='blue')
        plt.title('Validation Accuracy over Time')
        plt.xlabel('Time (s)')
        plt.ylabel('Accuracy')
        plt.grid(True, linestyle='--', alpha=0.7)
        
        plt.tight_layout()
        
        # Save transparently over the old file
        output_path = os.path.join(self.output_dir, 'training_metrics.png')
        plt.savefig(output_path, dpi=150)
        plt.close()
        
        print(f"📊 [MetricsPlotter] Graph updated and saved to {output_path}")
