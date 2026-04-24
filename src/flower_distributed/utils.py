def min_max_normalize(value, min_val, max_val, invert=False):
    if max_val == min_val:
        return 0.5
    normalized = (value - min_val) / (max_val - min_val)
    if invert:
        return 1.0 - normalized
    return normalized