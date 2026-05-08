import math

def min_max_normalize(value, min_val, max_val, invert=False):
    if max_val == min_val:
        return 0.5
    normalized = (value - min_val) / (max_val - min_val)
    if invert:
        return 1.0 - normalized
    return normalized

def calculate_dq_score(norm_vol, norm_iid):
    # Distance to (1, 1)
    distance = math.sqrt((norm_vol - 1)**2 + (norm_iid - 1)**2)
    # Max distance is sqrt(2) from (0,0) to (1,1)
    max_dist = math.sqrt(2)
    # Invert: Lower distance = Higher score
    return 1.0 - (distance / max_dist)