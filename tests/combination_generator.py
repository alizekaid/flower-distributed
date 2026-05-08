import json
import os
from itertools import product

def generate():
    values = [i / 10 for i in range(1, 11)]  # 0.1 → 1.0

    valid_combos = []
    
    # 1. Add all combinations where each weight is at least 0.1
    for cpu, ram, bw, lat, dq in product(values, repeat=5):
        if round(cpu + ram + bw + lat + dq, 5) == 1.0:
            valid_combos.append({
                "w_cpu": cpu, "w_ram": ram, "w_bw": bw, "w_lat": lat, "w_dq": dq
            })

    # 2. Add the 5 "pure" strategies (one-hot combinations)
    pure_strategies = [
        {"w_cpu": 1.0, "w_ram": 0.0, "w_bw": 0.0, "w_lat": 0.0, "w_dq": 0.0},
        {"w_cpu": 0.0, "w_ram": 1.0, "w_bw": 0.0, "w_lat": 0.0, "w_dq": 0.0},
        {"w_cpu": 0.0, "w_ram": 0.0, "w_bw": 1.0, "w_lat": 0.0, "w_dq": 0.0},
        {"w_cpu": 0.0, "w_ram": 0.0, "w_bw": 0.0, "w_lat": 1.0, "w_dq": 0.0},
        {"w_cpu": 0.0, "w_ram": 0.0, "w_bw": 0.0, "w_lat": 0.0, "w_dq": 1.0},
    ]
    
    for pure in pure_strategies:
        # Avoid duplication if (0.1, 0.1, 0.1, 0.1, 0.6) logic overlaps, though it won't here
        if pure not in valid_combos:
            valid_combos.append(pure)

    output_path = os.path.join(os.path.dirname(__file__), "all_combinations_grid.json")
    with open(output_path, "w") as f:
        json.dump(valid_combos, f, indent=4)

    print(f"✅ Successfully generated {len(valid_combos)} weight combinations in {output_path}")

if __name__ == "__main__":
    generate()
