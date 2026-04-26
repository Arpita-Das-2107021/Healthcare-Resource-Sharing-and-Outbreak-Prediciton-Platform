"""
predict.py — ST-GNN Inference (Run Pre-Trained Model)
=================================================================
Loads the saved stgnn_model.pt and makes live predictions on
the most recent 7 days of facility data. 
Applies Two-Factor Verification (Sales + Social).
"""

import json, csv, os
import torch
import torch.nn as nn
import torch.nn.functional as F

BASE     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "../data")

WATCH     = 0.35   # must match train_stgnn_pytorch.py
THRESHOLD = 0.60   # minimum model_prob to trigger two-factor check

# ── 1. Rebuild the Model Architecture ─────────────────────────────────────────
# The blueprint must match the saved brain exactly
class GCNLayer(nn.Module):
    def __init__(self, in_f, out_f):
        super().__init__()
        self.linear = nn.Linear(in_f, out_f, bias=True)
    def forward(self, x, adj):
        return F.elu(self.linear(adj @ x))

class STGNN(nn.Module):
    def __init__(self, in_features, hidden, dropout=0.3):
        super().__init__()
        self.gcn1 = GCNLayer(in_features, hidden)
        self.gcn2 = GCNLayer(hidden, hidden)
        self.gru  = nn.GRU(hidden, hidden, batch_first=True, num_layers=2, dropout=dropout)
        self.attn = nn.Linear(hidden, 1)
        self.drop = nn.Dropout(dropout)
        self.fc1  = nn.Linear(hidden, hidden//2)
        self.fc2  = nn.Linear(hidden//2, 1)
        self.bn1  = nn.BatchNorm1d(hidden)
        self.bn2  = nn.BatchNorm1d(hidden)

    def forward(self, x_seq, adj):
        seq_len, num_nodes, _ = x_seq.shape
        out = []
        for t in range(seq_len):
            h = self.bn1(self.gcn1(x_seq[t], adj))
            h = self.bn2(self.gcn2(self.drop(h), adj))
            out.append(h.unsqueeze(0))
        seq = torch.cat(out,0).permute(1,0,2)
        g,_ = self.gru(seq)
        ctx = (torch.softmax(self.attn(g),1)*g).sum(1)
        return torch.sigmoid(self.fc2(F.elu(self.fc1(self.drop(ctx))))).squeeze()

# ── Helper: Detect Disease from Features ──────────────────────────────────────
SIGNAL_GROUPS = {"Fever/Flu":0, "Diarrhea":1, "Respiratory":2, "Allergy/Fever":3, "Normal":4}

def detect_disease(ph_idx, recent_snapshots):
    """Detect dominant disease by anomaly score (deviation from network mean).
    Absolute normalized sums are misleading because features with no outbreak
    still normalize to ~1.0 (their own baseline = feat_max). Using deviation
    from the network mean isolates genuine local spikes above the global level.
    """
    idx_to_disease = {v:k for k,v in SIGNAL_GROUPS.items()}
    n_nodes = len(recent_snapshots[0]["node_feats"])
    ph_sums  = [0.0] * 5
    net_sums = [0.0] * 5
    for snap in recent_snapshots:
        for fi in range(5):
            ph_sums[fi]  += snap["node_feats"][ph_idx][fi]
            net_sums[fi] += sum(snap["node_feats"][nid][fi] for nid in range(n_nodes))
    net_mean = [net_sums[fi] / n_nodes for fi in range(5)]
    scores   = [(ph_sums[fi] - net_mean[fi]) / (net_mean[fi] + 1e-9)
                for fi in range(5)]
    scores[4] = -1  # ignore Normal
    best = scores.index(max(scores))
    return idx_to_disease.get(best, "Unknown") if max(scores) > 0.05 else "Unknown"

# ── Main Inference Engine ─────────────────────────────────────────────────────
def run_predictions():
    print("═"*80)
    print("  ST-GNN Inference Engine — Live Outbreak Detection")
    print("═"*80)

    # 1. Load the Saved Model
    model_path = os.path.join(BASE, "stgnn_model.pt")
    if not os.path.exists(model_path):
        print("  ⚠ No pre-trained model found! Run train_stgnn_pytorch.py first.")
        return

    checkpoint = torch.load(model_path, weights_only=False)
    config = checkpoint["config"]
    node_ids = checkpoint["node_ids"]
    
    model = STGNN(config["in_features"], config["hidden_dim"],
                  dropout=config.get("dropout", 0.3))
    model.load_state_dict(checkpoint["model_state"])
    model.eval() # Set to evaluation mode (locks the brain so it doesn't learn)
    print("  ✓ Pre-Trained AI Brain Loaded successfully.")

    # 2. Load the specific data we need to predict today
    with open(os.path.join(DATA_DIR,"graph_dataset.json")) as f: 
        data = json.load(f)
        
    num_nodes = len(node_ids)
    adj = torch.zeros(num_nodes, num_nodes)
    for e in data[0]["edges"]:
        i,j = e["from_idx"], e["to_idx"]
        w   = 1.0/(e["distance_km"]+1e-6)
        adj[i][j] = w; adj[j][i] = w
    adj += torch.eye(num_nodes)
    adj = adj / adj.sum(1, keepdim=True).clamp(min=1e-9)

    # Grab only the most recent 7 days of data
    recent_snaps = data[-7:]
    last_X = torch.tensor([snap["node_feats"] for snap in recent_snaps], dtype=torch.float32)

    # 3. Load Two-Factor Social Media Data aligned to the prediction window
    try:
        from social_media_analyzer import load_hashtag_scores, get_combined_confidence
        snap_dates    = {snap["date"] for snap in recent_snaps}
        social_scores = load_hashtag_scores(target_dates=snap_dates)
        use_social = True
        print("  ✓ Live Social Media Hashtags Loaded.")
    except Exception as e:
        social_scores = {}
        use_social = False

    facilities = {}
    with open(os.path.join(DATA_DIR,"facilities.csv")) as f:
        for row in csv.DictReader(f): facilities[row["facility_id"]] = row

    # 4. Make Predictions (Instantly)
    print("\n  Running Forward Pass...")
    with torch.no_grad():
        preds = model(last_X, adj)

    # 5. Apply Two-Factor Verification & Save
    results = []
    alerts  = 0
    watches = 0
    
    for i, ph_id in enumerate(node_ids):
        ph = facilities.get(ph_id, {})
        upazila = ph.get("upazila", "")
        
        prob = preds[i].item()
        disease = detect_disease(i, recent_snaps) if prob >= WATCH else "—"

        if use_social and prob >= WATCH and disease != "—":
            _, sscore, _ = get_combined_confidence(prob, upazila, disease, social_scores)
        else:
            sscore = 0.0

        model_prob = float(prob)
        social_score = float(sscore)
        
        # --- TWO-FACTOR VERIFICATION LOGIC ---
        if model_prob >= THRESHOLD:
            if social_score >= 0.10:
                status  = "ALERT"
                final   = model_prob
                conf    = "(Confirmed by Social Media)"
                alerts += 1
            else:
                status  = "Normal"
                final   = model_prob
                conf    = "(Sales Spike Only - No Social Signal)"
                disease = "—"
        elif model_prob >= WATCH:
            status   = "WATCH"
            final    = model_prob
            conf     = "(Elevated Risk - Monitor)"
            watches += 1
        else:
            status  = "Normal"
            final   = model_prob
            conf    = "(Normal)"
            disease = "—"

        results.append({
            "facility_id":    ph_id,
            "upazila":        upazila,
            "model_prob":     round(prob,4),
            "social_score":   round(sscore,4),
            "final_confidence": round(final,4),
            "status":         status,
            "likely_disease": disease,
            "social_confirmation": conf,
        })

    # Save to CSV
    if not results:
        print("  ⚠ No results to save — node_ids may be empty.")
        return
    pred_path = os.path.join(DATA_DIR, "predictions.csv")
    with open(pred_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
        
    print(f"\n  ⚠  {alerts} ALERT  |  ⚡ {watches} WATCH  |  ✓ {len(results)-alerts-watches} Normal")
    print(f"  ✓ Live predictions saved → {pred_path}")
    print("═"*80)

if __name__ == "__main__":
    run_predictions()

