from network_managers import NetworkManager
import json
import os

def test_managers():
    # Create a dummy topology file for testing
    dummy_topo = {
        "switches": [
            {"dpid": "0000000000000001", "name": "s1"},
            {"dpid": "0000000000000002", "name": "s2"}
        ],
        "links": [
            {"src": "s1", "dst": "s2", "src_port": "s1-eth1", "dst_port": "s2-eth1"},
            {"src": "s1", "dst": "c1", "src_port": "s1-eth2", "dst_port": "c1-eth0"}
        ],
        "hosts": [
            {"name": "c1", "mac": "00:00:00:00:00:01", "ip": "10.0.0.1", "switch": "s1", "port": 2}
        ]
    }
    
    with open("test_topology.json", "w") as f:
        json.dump(dummy_topo, f)
    
    manager = NetworkManager(topology_file="test_topology.json")
    
    print("--- Host Manager Test ---")
    host = manager.host_manager.get_host("00:00:00:00:00:01")
    print(f"Host c1: {host}")
    assert host is not None
    assert host.name == "c1"
    
    print("\n--- Switch Manager Test ---")
    switch = manager.switch_manager.SwitchDict["s1"]
    print(f"Switch s1: {switch}")
    assert switch is not None
    assert switch.port_to_host[2] == "c1"
    
    print("\n--- Path Manager Test ---")
    paths = manager.get_all_possible_paths()
    print(f"Paths between s1 and s2: {paths.get(('s1', 's2'))}")
    assert ['s1', 's2'] in paths.get(('s1', 's2'))
    
    print("\nAll tests passed!")
    
    # Cleanup
    os.remove("test_topology.json")

if __name__ == "__main__":
    test_managers()
