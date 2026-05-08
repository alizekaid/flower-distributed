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

        # Create a 2x2 grid for: Network, Resources, and IID
        fig, axes = plt.subplots(2, 2, figsize=(16, 14))
        ax1, ax2, ax3 = axes[0, 0], axes[0, 1], axes[1, 0]
        # Enabled 4th slot for ML Strategy Analysis
        
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

        # 3. IID PLOT (Bar chart of Entropy)
        client_names = list(data.keys())
        iid_scores = [float(data[name].get("iid_score_raw", 0.0)) for name in client_names]
        colors = [plt.cm.viridis(i/len(client_names)) for i in range(len(client_names))]
        
        bars = ax3.bar(client_names, iid_scores, color=colors, alpha=0.7)
        ax3.set_title(f"Data Diversity Score - Entropy (Round {round_num})", fontsize=12, fontweight='bold')
        ax3.set_ylabel("Shannon Entropy (bits)")
        ax3.set_ylim(0, 2.5) # Max for 10 classes is ~2.3
        ax3.grid(True, axis='y', linestyle='--', alpha=0.3)
        
        # Add values on top of bars
        for bar in bars:
            height = bar.get_height()
            ax3.annotate(f'{height:.2f}',
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 3),  # 3 points vertical offset
                        textcoords="offset points",
                        ha='center', va='bottom', fontsize=8)
        
        # 4. ML DISTRIBUTION PLOT (Volume vs Balance Score)
        ax4 = axes[1, 1]
        for i, (client_name, stats) in enumerate(data.items()):
            vol = float(stats.get("norm_vol", 0.5))
            bal = float(stats.get("norm_iid", 0.5))
            marker = markers[i % len(markers)]
            
            ax4.scatter(vol, bal, label=client_name, marker=marker, s=120, alpha=0.8)
            ax4.annotate(client_name, (vol, bal), textcoords="offset points", xytext=(5,5), fontsize=9)

        ax4.set_title(f"ML Strategy Analysis (Round {round_num})", fontsize=12, fontweight='bold')
        ax4.set_xlabel("Data Volume Score (Higher is More Data)", fontsize=10)
        ax4.set_ylabel("Distribution Balance Score (Higher is IID)", fontsize=10)
        ax4.set_xlim(-0.05, 1.05)
        ax4.set_ylim(-0.05, 1.05)
        ax4.grid(True, linestyle='--', alpha=0.3)
        # Quadrant lines
        ax4.axhline(y=0.5, color='red', linestyle='--', alpha=0.3)
        ax4.axvline(x=0.5, color='red', linestyle='--', alpha=0.3)
        
        # Quadrant Labels (Using transAxes for perfect relative positioning)
        ax4.text(0.75, 0.85, "ELITE", transform=ax4.transAxes, fontsize=11, color='darkgreen', alpha=0.4, fontweight='bold', ha='center')
        ax4.text(0.25, 0.85, "FALSE CHAMP", transform=ax4.transAxes, fontsize=11, color='darkorange', alpha=0.4, fontweight='bold', ha='center')
        ax4.text(0.75, 0.15, "BIG & SKEWED", transform=ax4.transAxes, fontsize=11, color='darkred', alpha=0.4, fontweight='bold', ha='center')
        ax4.text(0.25, 0.15, "WEAK", transform=ax4.transAxes, fontsize=11, color='gray', alpha=0.4, fontweight='bold', ha='center')

        # Consolidate legend on the right
        handles, labels = ax1.get_legend_handles_labels()
        fig.legend(handles, labels, loc='center right', title="Clients")
        plt.subplots_adjust(right=0.9, hspace=0.3)

        output_path = os.path.join(log_dir, f"telemetry_round_{round_num}.png")
        plt.savefig(output_path, dpi=120)
        plt.close()
        print(f"  - Round {round_num} plot saved to {output_path}")

    print(f"✅ All telemetry plots generated successfully.")

if __name__ == "__main__":
    plot_telemetry()
