import os
import sys

# Ensure the paths are correct
sys.path.append(os.path.join(os.getcwd(), "src"))

from flower_distributed.task import load_data, get_client_metadata

print("🚀 Pregenerating PyTorch Data and Caching for all 10 Clients...")
print("This will execute the heavy dataset preparation once so the FL server launches instantly.")

# Force load and cache for all 10 clients
for i in range(10):
    try:
        # get_client_metadata triggers a dataset generation if cache is missing
        print(f"Preparing storage for Client {i+1}/10...")
        meta = get_client_metadata(i, 10)
        print(f"✅ Cached Client {i+1}: {meta['item_count']} files.")
    except Exception as e:
        print(f"❌ Error preparing Client {i+1}: {e}")

print("🎉 Setup Complete. All data is cached. The Federated System will now load instantly.")
