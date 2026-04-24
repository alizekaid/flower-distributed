#!/usr/bin/env python3
"""
Cgroup-Aware Load Injector
===========================
Targets the actual systemd-run cgroup of each flwr-supernode process,
so stress runs INSIDE the client's CPU quota and memory budget.
This correctly stresses only the targeted client's resource partition.
"""
import time
import json
import os
import subprocess
import argparse

# Maps client names to their partition-id (as set in mininet_topology.py)
CLIENT_PARTITION_MAP = {
    "c1": 0, "c2": 1, "c3": 2, "c4": 3,
    "c5": 4, "c6": 5, "c7": 6, "c8": 7,
}

def get_latest_round(log_dir="logs"):
    os.makedirs(log_dir, exist_ok=True)
    files = os.listdir(log_dir)
    rounds = [int(f.split('_')[-1].split('.')[0]) for f in files if f.startswith("client_stats_round_")]
    return max(rounds) if rounds else 0

def find_client_cgroup(client_name):
    """Find the systemd scope cgroup path for a specific client's supernode."""
    partition_id = CLIENT_PARTITION_MAP.get(client_name)
    if partition_id is None:
        print(f"  [!] Unknown client name: {client_name}")
        return None, None

    try:
        # Find the supernode process running with this partition-id
        result = subprocess.check_output(
            f"pgrep -f 'partition-id={partition_id} num-partitions'",
            shell=True
        ).decode().strip()
        pids = result.split('\n')
        pid = pids[0]

        # Read the cgroup path from procfs
        with open(f"/proc/{pid}/cgroup") as f:
            cgroup_path = f.read().strip().split(':')[-1]

        print(f"  [+] {client_name} -> PID={pid}, cgroup={cgroup_path}")
        return pid, cgroup_path

    except subprocess.CalledProcessError:
        print(f"  [!] Could not find supernode process for {client_name} (partition-id={partition_id})")
        return None, None
    except Exception as e:
        print(f"  [!] Error finding cgroup for {client_name}: {e}")
        return None, None

def inject_load_into_cgroup(client_name, cgroup_path, duration=120):
    """
    Inject CPU stress into a specific cgroup by moving a worker process
    into the cgroup's process list. This consumes the client's CPU quota.
    """
    cgroup_procs_file = f"/sys/fs/cgroup{cgroup_path}/cgroup.procs"

    # Python one-liner: move self into cgroup, then spin CPU
    stress_script = (
        f"import os,time\n"
        f"open('{cgroup_procs_file}','w').write(str(os.getpid()))\n"
        f"print('[STRESS] Started in cgroup for {client_name}')\n"
        f"t=time.time()\n"
        f"while time.time()-t<{duration}:\n"
        f"    x=sum(range(100000))\n"
    )

    proc = subprocess.Popen(
        ['sudo', 'python3', '-c', stress_script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    print(f"  [+] Stress process PID={proc.pid} injected into {client_name}'s cgroup")
    return proc

def get_strongest_clients(stats_file, count=3):
    """Read previous round stats and return the top N clients by free RAM."""
    try:
        with open(stats_file, 'r') as f:
            stats = json.load(f)
        # Sort by ram_available_mb descending (most free RAM = strongest)
        sorted_clients = sorted(
            stats.items(),
            key=lambda x: float(x[1].get("ram_available_mb", 0)),
            reverse=True
        )
        return [x[0] for x in sorted_clients[:count]]
    except Exception as e:
        print(f"[!] Could not parse stats: {e}. Using defaults.")
        return ["c8", "c7", "c6"]

def main():
    parser = argparse.ArgumentParser(description="Cgroup-Aware Load Injector")
    parser.add_argument("--trigger-round", type=int, default=3,
                        help="Round after which to inject load")
    parser.add_argument("--target-count", type=int, default=3,
                        help="Number of strongest clients to stress")
    parser.add_argument("--duration", type=int, default=120,
                        help="How long (seconds) to run the stress load")
    args = parser.parse_args()

    print(f"[LOAD INJECTOR] Waiting for Round {args.trigger_round} to complete...")

    current_round = 0
    while current_round < args.trigger_round:
        current_round = get_latest_round()
        if current_round < args.trigger_round:
            time.sleep(2)

    print(f"[LOAD INJECTOR] Round {args.trigger_round} detected!")

    # Identify the strongest clients from the last round's telemetry
    stats_file = f"logs/client_stats_round_{args.trigger_round}.json"
    targets = get_strongest_clients(stats_file, count=args.target_count)
    print(f"[LOAD INJECTOR] Targeting top {args.target_count} clients: {targets}")

    # Find their cgroups and inject load
    procs = []
    for client in targets:
        pid, cgroup_path = find_client_cgroup(client)
        if cgroup_path:
            proc = inject_load_into_cgroup(client, cgroup_path, duration=args.duration)
            procs.append(proc)
        else:
            print(f"  [!] Skipping {client} — cgroup not found")

    print(f"\n[LOAD INJECTOR] All stress processes started. They will run for {args.duration}s.")
    print("[LOAD INJECTOR] Watch the next round's [ACTIVE] client list to verify selection shift!")

if __name__ == "__main__":
    main()
