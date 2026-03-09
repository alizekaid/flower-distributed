import json
import os

class StatsManager:
    """
    External module to provide network statistics and metadata.
    Designed to be shared across multiple SDN controllers.
    """
    def __init__(self, topology_file="topology.json", usage_file="usage_stats.json"):
        self.topology_file = topology_file
        self.usage_file = usage_file
        self.links = {}
        self.load_stats()
        self.load_usage()

    def load_stats(self):
        """Load link metadata from the topology file."""
        if not os.path.exists(self.topology_file):
            return False

        try:
            with open(self.topology_file, 'r') as f:
                data = json.load(f)
                for link in data.get('links', []):
                    # Store bidirectional link data
                    pair = tuple(sorted([link['src'], link['dst']]))
                    self.links[pair] = {
                        'bw': link.get('bw'),
                        'delay': link.get('delay'),
                        'usage': 0.0  # Mbps
                    }
            return True
        except Exception:
            return False

    def save_usage(self):
        """Save current usage data to a file for other processes to read."""
        usage_data = {}
        for pair, metadata in self.links.items():
            key = f"{pair[0]}|{pair[1]}"
            usage_data[key] = metadata.get('usage', 0.0)
        
        try:
            abs_path = os.path.abspath(self.usage_file)
            with open(abs_path, 'w') as f:
                json.dump(usage_data, f)
            # Only print if there is actual non-zero usage to avoid spam
            has_usage = any(v > 0 for v in usage_data.values())
            if has_usage:
                print(f"DEBUG: Saved non-zero usage stats to {abs_path}")
            return True
        except Exception as e:
            print(f"ERROR: Failed to save usage stats: {e}")
            return False

    def load_usage(self):
        """Load usage data from the shared file."""
        if not os.path.exists(self.usage_file):
            return False
        
        try:
            with open(self.usage_file, 'r') as f:
                usage_data = json.load(f)
                for key, usage in usage_data.items():
                    node_a, node_b = key.split('|')
                    pair = tuple(sorted([node_a, node_b]))
                    if pair in self.links:
                        self.links[pair]['usage'] = usage
            return True
        except Exception:
            return False

    def update_usage(self, node_a, node_b, mbps):
        """Update the current usage of a link."""
        pair = tuple(sorted([node_a, node_b]))
        if pair in self.links:
            self.links[pair]['usage'] = mbps

    def get_available_bandwidth(self, node_a, node_b):
        """Return the available bandwidth (Capacity - Usage)."""
        pair = tuple(sorted([node_a, node_b]))
        link_info = self.links.get(pair)
        if not link_info or link_info['bw'] is None:
            return None
        return max(0.0, float(link_info['bw']) - link_info['usage'])

    def get_link_capacity(self, node_a, node_b):
        """Return the bandwidth (Mbps) between two nodes."""
        pair = tuple(sorted([node_a, node_b]))
        link_info = self.links.get(pair)
        return link_info['bw'] if link_info else None

    def get_link_delay(self, node_a, node_b):
        """Return the delay string (e.g., '5ms') between two nodes."""
        pair = tuple(sorted([node_a, node_b]))
        link_info = self.links.get(pair)
        return link_info['delay'] if link_info else None

# Singleton-like instance for easy import
_provider = None

def get_stats_manager(topology_file="topology.json"):
    global _provider
    if _provider is None:
        # Use absolute path for cross-process sync
        base_dir = os.path.dirname(os.path.abspath(__file__))
        usage_file = os.path.join(base_dir, "usage_stats.json")
        _provider = StatsManager(topology_file, usage_file)
    return _provider
