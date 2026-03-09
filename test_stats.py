#!/usr/bin/env python3
from stats_manager import get_stats_manager

def main():
    # Initialize the manager
    stats = get_stats_manager("topology.json")
    stats.load_usage()
    
    print("-" * 30)
    print("NETWORK STATISTICS CACHE")
    print("-" * 30)
    
    print("-" * 65)
    print(f"{'LINK':<20} | {'TOTAL':<10} | {'USAGE':<10} | {'AVAILABLE':<10}")
    print("-" * 65)
    
    # Iterate through all discovered links in the manager
    for pair, metadata in stats.links.items():
        src, dst = pair
        total = metadata.get('bw', 0)
        usage = metadata.get('usage', 0.0)
        avail = stats.get_available_bandwidth(src, dst)
        
        link_str = f"{src} <-> {dst}"
        print(f"{link_str:<20} | {str(total) + 'M':<10} | {usage:>.2f}M | {avail:>.2f}M")
    
    print("-" * 65)

if __name__ == "__main__":
    main()
