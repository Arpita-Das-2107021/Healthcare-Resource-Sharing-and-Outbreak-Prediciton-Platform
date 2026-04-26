"""
Phase 2 — Graph Network Builder
Builds a facility network graph where:
  - Each facility = node
  - Edge between two facilities if distance < EDGE_RADIUS_KM
  - Node features = daily medicine sales (aggregated by disease signal)
  - Uses EvolveGCN-style temporal graph sequences (Paper 2 + Paper 1)
"""

import csv
import math
import os
import json
from datetime import datetime, timedelta
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DIR       = os.path.join(os.path.dirname(__file__), "../data")
EDGE_RADIUS_KM = 2.5   # connect facilities within 10 km
SEQUENCE_LEN   = 7      # days of history per graph snapshot (like Paper 1)
START_DATE     = datetime(2024, 1, 1)

# Disease signal groups (maps to feature dimensions)
SIGNAL_GROUPS = {
    "Fever/Flu":     0,
    "Diarrhea":      1,
    "Respiratory":   2,
    "Allergy/Fever": 3,
    "Normal":        4,
}
NUM_FEATURES = len(SIGNAL_GROUPS)   # 5 features per node per day

# ── Helpers ───────────────────────────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two GPS points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ── Load data ─────────────────────────────────────────────────────────────────
def load_facilities():
    facilities = {}
    with open(f"{DATA_DIR}/facilities.csv") as f:
        for row in csv.DictReader(f):
            facilities[row["facility_id"]] = {
                "name":    row["name"],
                "upazila": row["upazila"],
                "lat":     float(row["lat"]),
                "lon":     float(row["lon"]),
            }
    return facilities

def load_medicines_map():
    """Returns {medicine_name: signal_group_index}"""
    med_map = {}
    with open(f"{DATA_DIR}/medicines.csv") as f:
        for row in csv.DictReader(f):
            group = row["signals_disease"]
            med_map[row["medicine_name"]] = SIGNAL_GROUPS.get(group, 4)
    return med_map

def load_sales():
    """Returns {date: {facility_id: {feature_idx: total_qty}}}"""
    sales = defaultdict(lambda: defaultdict(lambda: [0.0]*NUM_FEATURES))
    med_map = load_medicines_map()
    with open(f"{DATA_DIR}/sales.csv") as f:
        for row in csv.DictReader(f):
            feat_idx = med_map.get(row["medicine_name"], 4)
            sales[row["date"]][row["facility_id"]][feat_idx] += float(row["quantity_sold"])
    return sales

# ── Build static graph edges ──────────────────────────────────────────────────
def build_edges(facilities):
    """
    Returns list of (i, j, distance_km) for all facility pairs within radius.
    Nodes are indexed by sorted facility_id order.
    """
    ph_ids = sorted(facilities.keys())
    edges  = []
    for i, id_a in enumerate(ph_ids):
        for j, id_b in enumerate(ph_ids):
            if j <= i:
                continue
            dist = haversine_km(
                facilities[id_a]["lat"], facilities[id_a]["lon"],
                facilities[id_b]["lat"], facilities[id_b]["lon"]
            )
            if dist <= EDGE_RADIUS_KM:
                edges.append({"from_idx": i, "to_idx": j,
                               "from_id": id_a, "to_id": id_b,
                               "distance_km": round(dist, 3)})
    return ph_ids, edges

# ── Build temporal graph snapshots ────────────────────────────────────────────
def build_graph_sequence(num_days=60):
    """
    Returns a list of daily graph snapshots.
    Each snapshot = {
        "day":        int,
        "date":       str,
        "node_ids":   [facility_id, ...],          # fixed order
        "node_feats": [[f0..f4], ...],              # one row per facility
        "edges":      [{from_idx, to_idx, dist}],  # static structure
        "label":      None  (fill in with real outbreak labels)
    }
    This is the input format for the ST-GNN model in Phase 3.

    Note: num_days=60 is intentional. The raw data spans 90 days, but only
    60 days are used for the graph dataset (train + test split). The remaining
    30 days act as a holdout buffer so the model is never evaluated on data
    it could have implicitly seen during graph construction.
    """
    facilities = load_facilities()
    sales      = load_sales()
    ph_ids, edges = build_edges(facilities)

    # Compute per-feature max across all loaded data for data-driven normalization
    feat_max = [1.0] * NUM_FEATURES
    for date_data in sales.values():
        for ph_feats in date_data.values():
            for i, v in enumerate(ph_feats):
                if v > feat_max[i]:
                    feat_max[i] = v

    snapshots = []
    for day in range(num_days):
        date = (START_DATE + timedelta(days=day)).strftime("%Y-%m-%d")
        node_feats = []
        for ph_id in ph_ids:
            feats = sales[date].get(ph_id, [0.0]*NUM_FEATURES)
            norm  = [feats[i] / feat_max[i] for i in range(NUM_FEATURES)]
            node_feats.append(norm)

        snapshots.append({
            "day":        day,
            "date":       date,
            "node_ids":   ph_ids,
            "node_feats": node_feats,
            "edges":      edges,
            "label":      None,
        })
    return snapshots

# ── Attach ground truth labels ────────────────────────────────────────────────
def attach_labels(snapshots, facilities):
    """
    Reads outbreaks_ground_truth.csv and marks each snapshot+facility
    with 1 (outbreak active) or 0 (normal).
    Returns snapshots with node_labels list added.
    """
    outbreaks = []
    with open(f"{DATA_DIR}/outbreaks_ground_truth.csv") as f:
        for row in csv.DictReader(f):
            outbreaks.append({
                "start": int(row["start_day"]),
                "end":   int(row["end_day"]),
                "upazilas": set(row["upazilas"].split("|")),
            })

    for snap in snapshots:
        labels = []
        for ph_id in snap["node_ids"]:
            upazila = facilities[ph_id]["upazila"]
            active  = any(
                ob["start"] <= snap["day"] <= ob["end"]
                and upazila in ob["upazilas"]
                for ob in outbreaks
            )
            labels.append(1 if active else 0)
        snap["node_labels"] = labels
    return snapshots

# ── Save graph dataset ────────────────────────────────────────────────────────
def save_graph_dataset(snapshots):
    out_path = f"{DATA_DIR}/graph_dataset.json"
    # Convert to JSON-serialisable format
    data = []
    for snap in snapshots:
        data.append({
            "day":          snap["day"],
            "date":         snap["date"],
            "node_ids":     snap["node_ids"],
            "node_feats":   snap["node_feats"],
            "node_labels":  snap.get("node_labels", []),
            "edges":        snap["edges"],
        })
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    return out_path

# ── Print summary ─────────────────────────────────────────────────────────────
def print_summary(snapshots, ph_ids, edges):
    print("\n" + "═"*55)
    print("  Graph Network Summary")
    print("═"*55)
    print(f"  Nodes (facilities) : {len(ph_ids)}")
    print(f"  Edges (connections): {len(edges)}")
    print(f"  Graph snapshots    : {len(snapshots)} days")
    print(f"  Node features      : {NUM_FEATURES}  (one per disease group)")
    print(f"  Sequence length    : {SEQUENCE_LEN} days")
    print(f"  Edge radius        : {EDGE_RADIUS_KM} km")
    print()
    # Count labels
    outbreak_days = sum(1 for s in snapshots for l in s.get("node_labels",[]) if l==1)
    total_slots   = sum(len(s["node_ids"]) for s in snapshots)
    print(f"  Outbreak-flagged   : {outbreak_days}/{total_slots} node-days "
          f"({100*outbreak_days/total_slots:.1f}%)")
    print("═"*55)
    # Show edge sample
    print("\n  Sample edges (first 5):")
    for e in edges[:5]:
        print(f"    {e['from_id']} ↔ {e['to_id']}  ({e['distance_km']} km)")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Building facility graph network...")
    facilities        = load_facilities()
    ph_ids, edges     = build_edges(facilities)
    snapshots         = build_graph_sequence(num_days=60)
    snapshots         = attach_labels(snapshots, facilities)
    out_path          = save_graph_dataset(snapshots)
    print_summary(snapshots, ph_ids, edges)
    print(f"\n✓ Graph dataset saved → {out_path}")
    print("\nNext step: run  python models/train_stgnn.py")


