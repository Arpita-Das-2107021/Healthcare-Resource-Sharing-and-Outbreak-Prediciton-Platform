"""
run_all.py — Full Integrated Pipeline
======================================
Runs all steps in correct order automatically.

Folder Structure Expected:
  - model2/run_all.py
  - model2/data/generate_bangladesh_data.py
  - model2/utils/build_graph.py
  - model2/models/optimize_goa.py
  - model2/models/train_stgnn_pytorch.py
  - model2/models/predict.py
  - model2/models/marl_dispatcher.py
"""

import subprocess, sys, os

BASE = os.path.dirname(os.path.abspath(__file__))

steps = [
    ("Step 1 — Generate Bangladesh Data",
     [sys.executable, os.path.join(BASE, "data", "generate_bangladesh_data.py")]),

    ("Step 2 — Build Pharmacy Graph",
     [sys.executable, os.path.join(BASE, "utils", "build_graph.py")]),

    ("Step 3 — GOA: Find Optimal Hyperparameters",
     [sys.executable, os.path.join(BASE, "models", "optimize_goa.py")]),

    ("Step 4 — Train ST-GNN Model",
     [sys.executable, os.path.join(BASE, "models", "train_stgnn_pytorch.py")]),

    ("Step 5 — Run Inference (saved model → predictions.csv)",
     [sys.executable, os.path.join(BASE, "models", "predict.py")]),

    ("Step 6 — Logistics Dispatcher",
     [sys.executable, os.path.join(BASE, "models", "marl_dispatcher.py")]),
]

for label, cmd in steps:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}\n")

    result = subprocess.run(cmd, cwd=BASE)

    if result.returncode != 0:
        print(f"\n✗ Failed at: {label}")
        print(f"  Please check the error above to fix the issue in {cmd[-1]}")
        sys.exit(1)

print("\n"+"="*60)
print("  Full pipeline complete!")
print()
print("  Output files generated:")
print("    data/best_params.json          <- GOA optimal hyperparameters")
print("    data/social_media_hashtags.csv <- hashtag outbreak signals")
print("    models/stgnn_model.pt          <- trained ST-GNN model weights")
print("    data/predictions.csv           <- outbreak predictions")
print("    data/inventory_requests.csv    <- peer-to-peer medicine requests")
print()
print("  To predict with new data in the future:")
print("    1. Replace CSV files in data/")
print("    2. python utils/build_graph.py")
print("    3. python models/predict.py")
print("="*60)
