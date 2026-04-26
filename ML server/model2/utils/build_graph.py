"""
Phase 2 — Graph Network Builder
Builds a pharmacy network graph where:
  - Each pharmacy = node
  - Edge between two pharmacies if distance < EDGE_RADIUS_KM
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
EDGE_RADIUS_KM = 2.5   # connect pharmacies within 2.5 km
SEQUENCE_LEN   = 7      # days of history per graph snapshot
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
def load_pharmacies():
    """Load pharmacies.csv; falls back to facilities.csv for backward compat."""
    ph_path = os.path.join(DATA_DIR, "pharmacies.csv")
    fa_path = os.path.join(DATA_DIR, "facilities.csv")

    if os.path.exists(ph_path):
        id_col = "pharmacy_id"
        path = ph_path
    else:
        id_col = "facility_id"
        path = fa_path

    pharmacies = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            pharmacies[row[id_col]] = {
                "name":    row.get("name", row[id_col]),
                "upazila": row["upazila"],
                "lat":     float(row["lat"]),
                "lon":     float(row["lon"]),
            }
    return pharmacies

def load_medicines_map():
    """Returns {medicine_name: signal_group_index}"""
    med_map = {}
    path = os.path.join(DATA_DIR, "medicines.csv")
    if not os.path.exists(path):
        return med_map
    with open(path) as f:
        for row in csv.DictReader(f):
            group = row.get("signals_disease", "Normal")
            med_map[row["medicine_name"]] = SIGNAL_GROUPS.get(group, 4)
    return med_map

def load_sales():
    """Returns {date: {pharmacy_id: [feature_values per disease group]}}"""
    sales = defaultdict(lambda: defaultdict(lambda: [0.0]*NUM_FEATURES))
    med_map = load_medicines_map()
    path = os.path.join(DATA_DIR, "sales.csv")
    with open(path) as f:
        for row in csv.DictReader(f):
            # Accept either pharmacy_id or facility_id column
            ph_id = row.get("pharmacy_id") or row.get("facility_id", "")
            feat_idx = med_map.get(row["medicine_name"], 4)
            sales[row["date"]][ph_id][feat_idx] += float(row["quantity_sold"])
    return sales

# ── Build static graph edges ──────────────────────────────────────────────────
def build_edges(pharmacies):
    """
    Returns list of edge dicts for all pharmacy pairs within EDGE_RADIUS_KM.
    Nodes are indexed by sorted pharmacy_id order.
    """
    ph_ids = sorted(pharmacies.keys())
    edges  = []
    for i, id_a in enumerate(ph_ids):
        for j, id_b in enumerate(ph_ids):
            if j <= i:
                continue
            dist = haversine_km(
                pharmacies[id_a]["lat"], pharmacies[id_a]["lon"],
                pharmacies[id_b]["lat"], pharmacies[id_b]["lon"]
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
        "node_ids":   [pharmacy_id, ...],
        "node_feats": [[f0..f4], ...],   # one 5-feature row per pharmacy
        "edges":      [{from_idx, to_idx, dist}],
        "label":      None
    }
    Note: num_days=60 uses first 60 of 90 raw days; remaining 30 act as holdout.
    """
    pharmacies = load_pharmacies()
    sales      = load_sales()
    ph_ids, edges = build_edges(pharmacies)

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
def attach_labels(snapshots, pharmacies):
    """
    Reads outbreaks_ground_truth.csv and marks each snapshot+pharmacy
    with 1 (outbreak active) or 0 (normal).
    Returns snapshots with node_labels list added.
    """
    gt_path = os.path.join(DATA_DIR, "outbreaks_ground_truth.csv")
    if not os.path.exists(gt_path):
        for snap in snapshots:
            snap["node_labels"] = [0] * len(snap["node_ids"])
        return snapshots

    outbreaks = []
    with open(gt_path) as f:
        for row in csv.DictReader(f):
            outbreaks.append({
                "start": int(row["start_day"]),
                "end":   int(row["end_day"]),
                "upazilas": set(row["upazilas"].split("|")),
            })

    for snap in snapshots:
        labels = []
        for ph_id in snap["node_ids"]:
            upazila = pharmacies[ph_id]["upazila"]
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
    out_path = os.path.join(DATA_DIR, "graph_dataset.json")
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
    print("\n" + "="*55)
    print("  Graph Network Summary")
    print("="*55)
    print(f"  Nodes (pharmacies) : {len(ph_ids)}")
    print(f"  Edges (connections): {len(edges)}")
    print(f"  Graph snapshots    : {len(snapshots)} days")
    print(f"  Node features      : {NUM_FEATURES}  (one per disease group)")
    print(f"  Sequence length    : {SEQUENCE_LEN} days")
    print(f"  Edge radius        : {EDGE_RADIUS_KM} km")
    print()
    outbreak_days = sum(1 for s in snapshots for l in s.get("node_labels",[]) if l==1)
    total_slots   = sum(len(s["node_ids"]) for s in snapshots)
    if total_slots:
        print(f"  Outbreak-flagged   : {outbreak_days}/{total_slots} node-days "
              f"({100*outbreak_days/total_slots:.1f}%)")
    print("="*55)
    print("\n  Sample edges (first 5):")
    for e in edges[:5]:
        print(f"    {e['from_id']} -- {e['to_id']}  ({e['distance_km']} km)")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Building pharmacy graph network...")
    pharmacies        = load_pharmacies()
    ph_ids, edges     = build_edges(pharmacies)
    snapshots         = build_graph_sequence(num_days=60)
    snapshots         = attach_labels(snapshots, pharmacies)
    out_path          = save_graph_dataset(snapshots)
    print_summary(snapshots, ph_ids, edges)
    print(f"\nGraph dataset saved -> {out_path}")
    print("\nNext step: run  python models/optimize_goa.py  (then train_stgnn_pytorch.py)")
