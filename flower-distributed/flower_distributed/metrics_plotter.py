import os
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
            "accuracy": []
        }
        
    def aggregate_evaluate_metrics(self, records, weighting_metric_name: str):
        """
        Custom wrapper for Flower Next's aggregate_metricrecords.
        It runs the default aggregation, extracts the results, plots them, and returns them.
        """
        from flwr.serverapp.strategy.strategy_utils import aggregate_metricrecords
        from flwr.common import MetricRecord
        import copy
        
        if not records:
            return MetricRecord()
            
        # 1. Run standard Flower aggregation
        aggregated_metrics = aggregate_metricrecords(records, weighting_metric_name)
        
        # 2. Extract values (clients return 'eval_loss' and 'eval_acc')
        loss = float(aggregated_metrics.get("eval_loss", 0.0))
        acc = float(aggregated_metrics.get("eval_acc", 0.0))
        
        # 3. Save to history
        self.history["round"].append(len(self.history["round"]) + 1)
        self.history["loss"].append(loss)
        self.history["accuracy"].append(acc)
        
        # 4. Plot
        self.plot()
        
        return aggregated_metrics
        
    def plot(self):
        """Generates and saves a modern double-chart PNG of Loss and Accuracy."""
        plt.figure(figsize=(12, 5))
        
        # Plot 1: Validation Loss
        plt.subplot(1, 2, 1)
        plt.plot(self.history["round"], self.history["loss"], marker='o', linewidth=2, color='red')
        plt.title('Validation Loss over Rounds')
        plt.xlabel('Federated Round')
        plt.ylabel('Loss')
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.gca().xaxis.set_major_locator(plt.MaxNLocator(integer=True)) # Force integer X-axis
        
        # Plot 2: Validation Accuracy
        plt.subplot(1, 2, 2)
        plt.plot(self.history["round"], self.history["accuracy"], marker='o', linewidth=2, color='blue')
        plt.title('Validation Accuracy over Rounds')
        plt.xlabel('Federated Round')
        plt.ylabel('Accuracy')
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.gca().xaxis.set_major_locator(plt.MaxNLocator(integer=True)) # Force integer X-axis
        
        plt.tight_layout()
        
        # Save transparently over the old file
        output_path = os.path.join(self.output_dir, 'training_metrics.png')
        plt.savefig(output_path, dpi=150)
        plt.close()
        
        print(f"📊 [MetricsPlotter] Graph updated and saved to {output_path}")
