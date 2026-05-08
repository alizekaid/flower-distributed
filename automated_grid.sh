#!/bin/bash

# Configuration
RESULTS_FILE="grid_results.csv"
COMBINATIONS_FILE="tests/all_combinations_grid.json"
WEIGHTS_FILE="grid_weights.json"

# Safety Check
if [ ! -f "$COMBINATIONS_FILE" ]; then
    echo "ERROR: $COMBINATIONS_FILE not found. Run 'python3 tests/combination_generator.py' first."
    exit 1
fi

# Initialize Results File
echo "w_cpu,w_ram,w_bw,w_lat,w_dq,Duration_s,Final_Accuracy,Final_Loss" > "$RESULTS_FILE"

# Get combination count
TOTAL=$(jq '. | length' "$COMBINATIONS_FILE")
echo "🚀 Starting Grid Search: $TOTAL combinations..."

# Loop through each combination
for i in $(seq 0 $((TOTAL - 1))); do
    # 1. Extract combination and write to temporary weight file
    jq ".[$i]" "$COMBINATIONS_FILE" > "$WEIGHTS_FILE"
    
    # 2. Extract values for logging
    V_CPU=$(jq -r ".[$i].w_cpu" "$COMBINATIONS_FILE")
    V_RAM=$(jq -r ".[$i].w_ram" "$COMBINATIONS_FILE")
    V_BW=$(jq -r ".[$i].w_bw" "$COMBINATIONS_FILE")
    V_LAT=$(jq -r ".[$i].w_lat" "$COMBINATIONS_FILE")
    V_DQ=$(jq -r ".[$i].w_dq" "$COMBINATIONS_FILE")
    
    COMBO_STR="CPU:${V_CPU}_RAM:${V_RAM}_BW:${V_BW}_LAT:${V_LAT}_DQ:${V_DQ}"

    echo "--- [Run $((i+1))/$TOTAL] Testing: $COMBO_STR ---"

    # 3. Start Timer and Execute Simulation
    START_S=$SECONDS
    # We enforce non-interactive mode and redirect output
    bash ./run_infrastructure_automated.sh simple_cnn --auto > last_simulation.log 2>&1
    END_S=$SECONDS
    DURATION=$((END_S - START_S))

    # 4. Extract Final Metrics from Metric Logs
    # Log format: round,duration_s,loss,accuracy
    if [ -f "logs/round_times.log" ]; then
        LAST_LINE=$(tail -n 1 logs/round_times.log)
        LOSS=$(echo "$LAST_LINE" | cut -d',' -f3)
        ACC=$(echo "$LAST_LINE" | cut -d',' -f4)
    else
        LOSS="0.0"
        ACC="0.0"
    fi

    # 5. Record Result in CSV
    echo "$V_CPU,$V_RAM,$V_BW,$V_LAT,$V_DQ,$DURATION,$ACC,$LOSS" >> "$RESULTS_FILE"
    echo "Done. Time: ${DURATION}s | Acc: $ACC | Loss: $LOSS"
    
    # Optional: Brief pause to ensure Mininet cleanup finishes
    sleep 2
done

# Cleanup
rm "$WEIGHTS_FILE"
echo "✅ Grid Search Complete! Results saved to $RESULTS_FILE"
