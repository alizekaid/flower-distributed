#!/usr/bin/env python3
import json
import logging
from network_managers import NetworkManager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("DeepTest")

def deep_test():
    topo_file = "topology.json"
    logger.info("Starting Deep Test using %s", topo_file)
    
    try:
        manager = NetworkManager(topo_file)
    except Exception as e:
        logger.error("Failed to initialize NetworkManager: %s", e)
        return

    # 1. Host Manager Test
    logger.info("--- 1. HOST MANAGER VERIFICATION ---")
    hosts = manager.host_manager.HostDict
    logger.info("Found %d hosts in topology.", len(hosts))
    
    expected_hosts = ["server", "client1", "client2", "client3", "client4", "client5", "client6", "client7", "client8"]
    found_host_names = [h.name for h in hosts.values()]
    
    for name in expected_hosts:
        if name in found_host_names:
            logger.info("  [PASS] Host %s found.", name)
        else:
            logger.error("  [FAIL] Host %s NOT found!", name)

    # 2. Switch Manager Test
    logger.info("\n--- 2. SWITCH MANAGER VERIFICATION ---")
    switches = manager.switch_manager.SwitchDict
    # Switches are stored by both name and DPID in SwitchDict
    unique_switches = set()
    for s in switches.values():
        unique_switches.add(s.name)
    
    logger.info("Found %d unique switches.", len(unique_switches))
    if "Switch1" in unique_switches:
        logger.info("  [PASS] Core switch 'Switch1' recognized.")
    else:
        logger.error("  [FAIL] Core switch 'Switch1' missing!")

    # 3. Path Manager Test (The "Critique" Verification)
    logger.info("\n--- 3. PATH MANAGER (DIJKSTRA) VERIFICATION ---")
    # client1 is on s7, client8 is on s10.
    # Path should be: client1 -> s7 -> s3 -> s1 -> Switch1 -> s2 -> s6 -> s10 -> client8
    # But path manager works switch-to-switch.
    # client1 connected to s7
    # client8 connected to s10
    
    paths = manager.get_all_possible_paths()
    path_c1_c8 = paths.get(('s7', 's10'))
    
    if path_c1_c8:
        logger.info("  [PASS] Path found between s7 (client1) and s10 (client8).")
        logger.info("  Calculated Path: %s", " -> ".join(path_c1_c8[0]))
        
        # Verify it goes through the core (Switch1)
        if "Switch1" in path_c1_c8[0]:
            logger.info("  [PASS] Path correctly routes through the Core Switch.")
        else:
            logger.warning("  [FAIL] Path BYPASSES the core switch! (Check topology)")

        # Verify get_path_with_ports (Proactive Port Logic)
        logger.info("\n--- 3.1 PROACTIVE PORT VERIFICATION ---")
        src_mac = "00:00:00:00:00:02" # client1
        dst_mac = "00:00:00:00:00:09" # client8
        proactive_path = manager.get_path_with_ports(src_mac, dst_mac)
        if proactive_path:
            logger.info("  [PASS] Proactive path calculated.")
            logger.info("  First Hop: Switch %d, In: %s, Out: %s", 
                        proactive_path[0]['dpid'], 
                        proactive_path[0]['in_port'], 
                        proactive_path[0]['out_port'])
            logger.info("  Last Hop: Switch %d, In: %s, Out: %s", 
                        proactive_path[-1]['dpid'], 
                        proactive_path[-1]['in_port'], 
                        proactive_path[-1]['out_port'])
        else:
            logger.error("  [FAIL] Proactive path calculation failed for client1 to client8!")
    else:
        logger.error("  [FAIL] NO PATH found between s7 and s10!")

    # 4. Global Path Coverage
    total_switches = list(unique_switches)
    reachable_pairs = 0
    total_pairs = 0
    for i in range(len(total_switches)):
        for j in range(i + 1, len(total_switches)):
            total_pairs += 1
            if paths.get((total_switches[i], total_switches[j])):
                reachable_pairs += 1
    
    logger.info("\n--- 4. TOPOLOGY CONNECTIVITY ---")
    logger.info("Reachable switch pairs: %d/%d", reachable_pairs, total_pairs)
    if reachable_pairs == total_pairs:
        logger.info("  [PASS] Network is fully connected.")
    else:
        logger.error("  [FAIL] Network segmentation detected!")

if __name__ == "__main__":
    deep_test()
