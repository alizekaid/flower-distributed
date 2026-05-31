#!/usr/bin/env python3
import argparse
import sys
import subprocess
import json
import os

def run(cmd, suppress_errors=False):
    """Execute a shell command and return its output."""
    print(f"[Throttle] Executing: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0 and not suppress_errors:
        print(f"[Throttle] Command failed: {result.stderr.strip()}")
    return result

def main():
    parser = argparse.ArgumentParser(description="Link Throttler for Mininet/OVS")
    parser.add_argument("--links", required=True, help="Comma-separated links, e.g. s1-s2,c1-s7")
    parser.add_argument("--bandwidth", default="5M", help="Bandwidth limit, e.g. 5M, 10M")
    parser.add_argument("--reset", action="store_true", help="Reset links to full bandwidth")
    parser.add_argument("--topo", required=True, help="Path to topology.json")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.topo):
        print(f"Error: Topology file not found at {args.topo}")
        sys.exit(1)

    try:
        with open(args.topo, 'r') as f:
            topo = json.load(f)
    except Exception as e:
        print(f"Error: Failed to parse topology JSON: {e}")
        sys.exit(1)

    # Convert bandwidth string for tc (e.g. 5M -> 5mbit)
    bw_val = args.bandwidth.upper().replace('M', '').replace('K', '')
    try:
        bw_int = int(bw_val)
    except ValueError:
        bw_int = 5 # Default fallback
        
    bw_tc = f"{bw_val.lower()}mbit" if 'M' in args.bandwidth.upper() else f"{bw_val.lower()}kbit"
    if 'bit' not in args.bandwidth.lower() and 'm' not in args.bandwidth.lower() and 'k' not in args.bandwidth.lower():
        bw_tc = f"{args.bandwidth}mbit"

    requested_links = args.links.split(',')
    success_count = 0
    fail_count = 0
    
    for link_id in requested_links:
        # link_id is src-dst
        parts = link_id.split('-')
        if len(parts) != 2:
            print(f"Warning: Invalid link ID format '{link_id}'")
            fail_count += 1
            continue
            
        src, dst = parts
        
        # Find this link in topo
        found = False
        for l in topo.get('links', []):
            if (l['src'] == src and l['dst'] == dst) or (l['src'] == dst and l['dst'] == src):
                found = True
                ports = []
                if l['src'] == src:
                    ports = [l['src_port'], l['dst_port']]
                else:
                    ports = [l['dst_port'], l['src_port']]
                
                print(f"Throttling link {link_id} (ports: {', '.join(ports)})")
                
                # Update topology in memory
                if not args.reset:
                    l['bw'] = bw_int
                else:
                    l['bw'] = 30 # Default reset bandwidth
                
                link_success = False
                ports_attempted = 0
                ports_succeeded = 0
                
                for port in ports:
                    ports_attempted += 1
                    # Remove existing qdisc (don't print error if it doesn't exist)
                    run(f"sudo tc qdisc del dev {port} root", suppress_errors=True)
                    
                    if not args.reset:
                        # Add htb qdisc for rate limiting
                        cmd_qdisc = f"sudo tc qdisc add dev {port} root handle 1: htb default 10"
                        cmd_class = f"sudo tc class add dev {port} parent 1: classid 1:10 htb rate {bw_tc}"
                        
                        r1 = run(cmd_qdisc, suppress_errors=True)
                        r2 = run(cmd_class, suppress_errors=True)
                        
                        if r1.returncode == 0 and r2.returncode == 0:
                            ports_succeeded += 1
                            success_count += 1
                        else:
                            # If it failed, check if it's because it's in a namespace
                            stderr = r1.stderr if r1.returncode != 0 else r2.stderr
                            if stderr and ("Cannot find device" in stderr):
                                print(f"    [Note] Interface {port} skipped (not in root namespace).")
                            else:
                                print(f"    [Error] Failed to apply throttle to {port}: {stderr.strip()}")
                                fail_count += 1
                    else:
                        ports_succeeded += 1
                        success_count += 1
                
                # If at least one port succeeded (usually the switch side), we consider the link throttled
                if ports_succeeded > 0:
                    print(f"Successfully applied throttle to link {link_id} ({ports_succeeded}/{ports_attempted} ports)")
                    link_success = True
                elif ports_attempted > 0:
                    print(f"Warning: Could not apply throttle to any ports of link {link_id}")
                
                break
        
        if not found:
            print(f"Warning: Link {link_id} not found in topology.")
            fail_count += 1

    # Persist the updated topology so SDN controllers can react
    try:
        with open(args.topo, 'w') as f:
            json.dump(topo, f, indent=4)
        print(f"Updated topology file: {args.topo}")
    except Exception as e:
        print(f"Error: Failed to save updated topology: {e}")
        # This is a critical error, return failure
        sys.exit(1)

    print(f"Summary: {success_count} interfaces updated, {fail_count} failures recorded.")
    
    # We exit with 0 if at least one interface was updated OR if we only had "expected" skips
    # Basically, we only exit with 1 if there were actual hard failures that prevented ANY requested link from being throttled
    if success_count > 0:
        sys.exit(0)
    elif fail_count == 0:
        # No successes but no hard failures (only skips) - still return 0 to avoid front-end 500 error
        print("Note: No interfaces were modified (all likely inside namespaces), but no hard errors occurred.")
        sys.exit(0)
    else:
        # Only hard failures occurred
        print("Error: All throttle attempts failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
