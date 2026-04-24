import json
import os
import glob
import matplotlib.pyplot as plt
import re

def plot_client_bandwidth(log_dir="../logs"):
    # Find all client_stats_round_N.json files
    json_files = glob.glob(os.path.join(log_dir, "client_stats_round_*.json"))
    
    if not json_files:
        # Fallback to local logs directory if ../logs is missing
        log_dir = "logs"
        json_files = glob.glob(os.path.join(log_dir, "client_stats_round_*.json"))
        if not json_files:
            print(f"No telemetry logs found in logs or ../logs")
            return

    # Sort files by round number
    def get_round_num(filename):
        match = re.search(r'round_(\d+)', filename)
        return int(match.group(1)) if match else 0
    
    json_files.sort(key=get_round_num)

    rounds = []
    # Using a dictionary to store lists of bandwidths per client
    # e.g., {'c1': [15.0, 15.0, 5.0], 'c2': [45.0, ...]}
    client_bw_history = {}

    # Initialize all known clients from the first few files to keep line colors consistent
    all_known_clients = set()
    for json_file in json_files:
        with open(json_file, 'r') as f:
            data = json.load(f)
            for cid in data.keys():
                all_known_clients.add(cid)

    for cid in all_known_clients:
        client_bw_history[cid] = []

    print(f"Processing {len(json_files)} rounds of bandwidth telemetry...")

    for json_file in json_files:
        round_num = get_round_num(json_file)
        rounds.append(round_num)
        
        with open(json_file, 'r') as f:
            data = json.load(f)

        for client_name in all_known_clients:
            stats = data.get(client_name, {})
            # Try to extract bandwidth
            bw_raw = stats.get("bw_mbps", None)
            
            if bw_raw is not None:
                try:
                    # Clean up the string to float (remove 'Mbps', 'mbps', etc)
                    bw_val = float(str(bw_raw).replace('Mbps','').replace('mbps','').strip())
                except ValueError:
                    bw_val = None
            else:
                bw_val = None
            
            # If a client didn't respond this round, carry over the last known value or use 0
            if bw_val is None:
                if len(client_bw_history[client_name]) > 0:
                    bw_val = client_bw_history[client_name][-1]
                else:
                    bw_val = 0.0
                    
            client_bw_history[client_name].append(bw_val)

    # Begin Plotting
    plt.figure(figsize=(12, 7))
    markers = ['o', 's', 'D', '^', 'v', '<', '>', 'p', '*', 'h']
    
    # Sort client names so the legend is organized (c1, c2, c3...)
    sorted_clients = sorted(list(all_known_clients), key=lambda x: int(re.search(r'\d+', x).group()) if re.search(r'\d+', x) else x)

    for i, client_name in enumerate(sorted_clients):
        bw_values = client_bw_history[client_name]
        marker = markers[i % len(markers)]
        plt.plot(rounds, bw_values, label=client_name, marker=marker, linewidth=2, markersize=8)

    plt.title("Dynamic Link Bandwidth per Client Across FL Rounds", fontsize=14, fontweight='bold')
    plt.xlabel("Server Round", fontsize=12)
    plt.ylabel("Link Bandwidth (Mbps)", fontsize=12)
    
    # Ensure x-axis shows only integer round numbers
    plt.xticks(rounds)
    
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', title="Clients")
    plt.tight_layout()

    output_path = os.path.join(log_dir, "client_bandwidth_history_plot.png")
    plt.savefig(output_path, dpi=120)
    plt.close()
    
    print(f"✅ Bandwidth history plot generated and saved to {output_path}")

if __name__ == "__main__":
    # If standard execution fails to find logs relative to the script directory, 
    # it will automatically check the fallback "logs" dir in the current working directory.
    project_root_logs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs"))
    plot_client_bandwidth(project_root_logs_dir)
