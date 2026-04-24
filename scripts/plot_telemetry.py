import json
import os
import glob
import matplotlib.pyplot as plt
import re

def plot_telemetry(log_dir="logs"):
    # Find all client_stats_round_N.json files
    json_files = glob.glob(os.path.join(log_dir, "client_stats_round_*.json"))
    
    if not json_files:
        print(f"No telemetry logs found in {log_dir}")
        return

    # Sort files by round number
    def get_round_num(filename):
        match = re.search(r'round_(\d+)', filename)
        return int(match.group(1)) if match else 0
    
    json_files.sort(key=get_round_num)

    print(f"Processing {len(json_files)} telemetry snapshots...")

    for json_file in json_files:
        round_num = get_round_num(json_file)
        
        with open(json_file, 'r') as f:
            data = json.load(f)

        # Create a 1x2 subplot: Network and Resources
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))
        markers = ['o', 's', 'D', '^', 'v', '<', '>', 'p', '*', 'h']
        
        # 1. NETWORK PLOT (BW vs Latency)
        for i, (client_name, stats) in enumerate(data.items()):
            bw = float(stats.get("norm_bw", 0.5))
            lat = float(stats.get("norm_lat", 0.5))
            marker = markers[i % len(markers)]
            
            ax1.scatter(bw, lat, label=client_name, marker=marker, s=120, alpha=0.8)
            ax1.annotate(client_name, (bw, lat), textcoords="offset points", xytext=(5,5), fontsize=9)

        ax1.set_title(f"Network Performance Score (Round {round_num})", fontsize=12, fontweight='bold')
        ax1.set_xlabel("Bandwidth Score (Higher is Better)", fontsize=10)
        ax1.set_ylabel("Latency Score (Higher is Faster)", fontsize=10)
        ax1.set_xlim(-0.05, 1.05)
        ax1.set_ylim(-0.05, 1.05)
        ax1.grid(True, linestyle='--', alpha=0.3)

        # 2. RESOURCE PLOT (RAM vs CPU)
        for i, (client_name, stats) in enumerate(data.items()):
            ram = float(stats.get("norm_ram", 0.5))
            cpu = float(stats.get("norm_cpu", 0.5))
            marker = markers[i % len(markers)]
            
            ax2.scatter(ram, cpu, label=client_name, marker=marker, s=120, alpha=0.8)
            ax2.annotate(client_name, (ram, cpu), textcoords="offset points", xytext=(5,5), fontsize=9)

        ax2.set_title(f"Resource Availability Score (Round {round_num})", fontsize=12, fontweight='bold')
        ax2.set_xlabel("RAM Mobility Score (Higher is Better)", fontsize=10)
        ax2.set_ylabel("CPU Freshness Score (Higher is Idle)", fontsize=10)
        ax2.set_xlim(-0.05, 1.05)
        ax2.set_ylim(-0.05, 1.05)
        ax2.grid(True, linestyle='--', alpha=0.3)
        
        # Consolidate legend on the right
        handles, labels = ax1.get_legend_handles_labels()
        fig.legend(handles, labels, loc='center right', title="Clients")
        plt.subplots_adjust(right=0.9)

        output_path = os.path.join(log_dir, f"telemetry_round_{round_num}.png")
        plt.savefig(output_path, dpi=120)
        plt.close()
        print(f"  - Round {round_num} plot saved to {output_path}")

    print(f"✅ All telemetry plots generated successfully.")

if __name__ == "__main__":
    plot_telemetry()
