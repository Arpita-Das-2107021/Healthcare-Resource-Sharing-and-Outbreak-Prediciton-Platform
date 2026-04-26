"""
train_stgnn_pytorch.py — ST-GNN Training with GOA + Social Media
=================================================================
Upgrades:
  1. Automatically loads best hyperparameters from GOA (best_params.json)
  2. After prediction: confirms outbreaks using TWO-FACTOR Verification (Sales + Social)
  3. Safely downgrades massive sales spikes if the internet is quiet.

RUN ORDER:
  python models/optimize_goa.py         ← Step 1: find best params
  python models/train_stgnn_pytorch.py  ← Step 2: train with those params

OR just run: python run_all.py  (does everything automatically)
"""

import json, csv, os, random
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

SEED = 42
random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)

BASE     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "../data")

# ── Default hyperparameters ───────────────────────────────────────────────────
SEQ_LEN      = 7
NUM_FEATURES = 5
HIDDEN_DIM   = 64
EPOCHS       = 500
LR           = 0.005
DROPOUT      = 0.3
THRESHOLD       = 0.5    # binary classification cutoff for metrics (F1, accuracy)
ALERT_THRESHOLD = 0.60   # minimum model_prob to trigger two-factor alert check
WATCH           = 0.35
BATCH_SIZE      = 16

SIGNAL_GROUPS = {
    "Fever/Flu":0, "Diarrhea":1, "Respiratory":2, "Allergy/Fever":3, "Normal":4
}

# ── Step 1: Load GOA optimized parameters ────────────────────────────────────
PARAMS_PATH = os.path.join(DATA_DIR, "best_params.json")
if os.path.exists(PARAMS_PATH):
    with open(PARAMS_PATH) as f:
        goa_params = json.load(f)
    LR         = goa_params.get("LR",         LR)
    HIDDEN_DIM = goa_params.get("HIDDEN_DIM", HIDDEN_DIM)
    DROPOUT    = goa_params.get("DROPOUT",    DROPOUT)
    BATCH_SIZE = goa_params.get("BATCH_SIZE", BATCH_SIZE)
    print(f"\n  ⚡ GOA Optimized Params loaded:")
    print(f"     LR={LR:.5f}  HIDDEN_DIM={HIDDEN_DIM}  "
          f"DROPOUT={DROPOUT:.3f}  BATCH_SIZE={BATCH_SIZE}  "
          f"(fitness={goa_params.get('goa_fitness',0):.4f})")
else:
    print(f"\n  ℹ  No GOA params found — using defaults")
    print(f"     LR={LR}  HIDDEN_DIM={HIDDEN_DIM}  DROPOUT={DROPOUT}")

# ── Disease detection from sales features ────────────────────────────────────
def detect_disease(ph_idx, recent_snapshots):
    """Detect dominant disease by anomaly score (deviation from network mean).
    Absolute normalized sums are misleading because features with no outbreak
    still normalize to ~1.0 (their own baseline = feat_max). Using deviation
    from the network mean isolates genuine local spikes above the global level.
    """
    idx_to_disease = {v:k for k,v in SIGNAL_GROUPS.items()}
    n_nodes = len(recent_snapshots[0]["node_feats"])
    ph_sums  = [0.0] * NUM_FEATURES
    net_sums = [0.0] * NUM_FEATURES
    for snap in recent_snapshots:
        for fi in range(NUM_FEATURES):
            ph_sums[fi]  += snap["node_feats"][ph_idx][fi]
            net_sums[fi] += sum(snap["node_feats"][nid][fi] for nid in range(n_nodes))
    net_mean = [net_sums[fi] / n_nodes for fi in range(NUM_FEATURES)]
    scores   = [(ph_sums[fi] - net_mean[fi]) / (net_mean[fi] + 1e-9)
                for fi in range(NUM_FEATURES)]
    scores[4] = -1  # ignore Normal
    best = scores.index(max(scores))
    return idx_to_disease.get(best, "Unknown") if max(scores) > 0.05 else "Unknown"

# ── Model ─────────────────────────────────────────────────────────────────────
class GCNLayer(nn.Module):
    def __init__(self, in_f, out_f):
        super().__init__()
        self.linear = nn.Linear(in_f, out_f, bias=True)
        nn.init.xavier_uniform_(self.linear.weight)
    def forward(self, x, adj):
        return F.elu(self.linear(adj @ x))

class STGNN(nn.Module):
    def __init__(self, in_features, hidden, dropout=DROPOUT):
        super().__init__()
        self.gcn1 = GCNLayer(in_features, hidden)
        self.gcn2 = GCNLayer(hidden, hidden)
        self.gru  = nn.GRU(hidden, hidden, batch_first=True,
                            num_layers=2, dropout=dropout)
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

# ── Data loading ──────────────────────────────────────────────────────────────
def load_data():
    graph_path = os.path.join(DATA_DIR,"graph_dataset.json")
    with open(graph_path) as f: data = json.load(f)
    num_nodes = len(data[0]["node_ids"])

    adj = torch.zeros(num_nodes, num_nodes)
    for e in data[0]["edges"]:
        i,j = e["from_idx"],e["to_idx"]
        w   = 1.0/(e["distance_km"]+1e-6)
        adj[i][j]=w; adj[j][i]=w
    adj += torch.eye(num_nodes)
    adj  = adj/adj.sum(1,keepdim=True).clamp(min=1e-9)

    seqs = []
    for t in range(SEQ_LEN, len(data)):
        X = torch.tensor([data[t-SEQ_LEN+s]["node_feats"] for s in range(SEQ_LEN)],
                          dtype=torch.float32)
        y = torch.tensor(data[t]["node_labels"], dtype=torch.float32)
        seqs.append((X,y))

    return seqs, adj, data[0]["node_ids"], data

def compute_metrics(pred, true):
    pb  = (pred>=THRESHOLD).float()
    acc = (pb==true).float().mean().item()
    tp  = ((pb==1)&(true==1)).float().sum().item()
    fp  = ((pb==1)&(true==0)).float().sum().item()
    fn  = ((pb==0)&(true==1)).float().sum().item()
    pr  = tp/(tp+fp+1e-9); re=tp/(tp+fn+1e-9)
    return acc, pr, re, 2*pr*re/(pr+re+1e-9)

# ── Training ──────────────────────────────────────────────────────────────────
def train():
    print("\n"+"="*65)
    print("  Phase 3+4 — ST-GNN Training")
    print("  Disease Outbreak Prediction")
    print("="*65)

    seqs, adj, node_ids, all_data = load_data()
    num_nodes = len(node_ids)
    split      = int(0.8*len(seqs))
    tr, te     = seqs[:split], seqs[split:]

    all_labels = torch.cat([y for _,y in tr])
    pos = all_labels.sum().item(); neg = len(all_labels)-pos
    pos_weight = torch.tensor([neg/(pos+1e-9)])

    print(f"\n  Facilities : {num_nodes}")
    print(f"  Train seqs : {len(tr)}  |  Test seqs: {len(te)}")
    print(f"  Outbreak % : {100*pos/len(all_labels):.1f}%")
    print(f"  LR         : {LR}  |  Hidden: {HIDDEN_DIM}  |  Dropout: {DROPOUT}")

    model     = STGNN(NUM_FEATURES, HIDDEN_DIM, DROPOUT)
    optimizer = torch.optim.Adam(model.parameters(), lr=LR, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=50, gamma=0.85)

    print(f"  Params     : {sum(p.numel() for p in model.parameters()):,}")
    print(f"\n  Training {EPOCHS} epochs...")
    print("-"*65)
    print(f"  {'Epoch':>5}  {'Loss':>7}  {'Acc':>6}  {'Prec':>6}  {'Rec':>6}  {'F1':>6}")
    print("-"*65)

    best_f1=0; best_state=None

    for epoch in range(1,EPOCHS+1):
        model.train(); total_loss=0; random.shuffle(tr)
        optimizer.zero_grad()
        for bi,(X,y) in enumerate(tr):
            pred = model(X,adj)
            w    = torch.where(y==1,pos_weight.expand_as(y),torch.ones_like(y))
            loss = F.binary_cross_entropy(pred,y,weight=w) / BATCH_SIZE
            loss.backward()
            total_loss += loss.item() * BATCH_SIZE
            if (bi+1) % BATCH_SIZE == 0 or bi == len(tr)-1:
                nn.utils.clip_grad_norm_(model.parameters(),1.0)
                optimizer.step()
                optimizer.zero_grad()
        scheduler.step()

        if epoch%10==0 or epoch==1:
            model.eval()
            ap,at=[],[]
            with torch.no_grad():
                for X,y in te:
                    ap.append(model(X,adj)); at.append(y)
            ap=torch.cat(ap); at=torch.cat(at)
            acc,pr,re,f1 = compute_metrics(ap,at)
            avg = total_loss/len(tr)
            if f1>best_f1: best_f1=f1; best_state={k:v.clone() for k,v in model.state_dict().items()}
            print(f"  {epoch:>5}  {avg:>7.4f}  {acc:>6.3f}  {pr:>6.3f}  {re:>6.3f}  {f1:>6.3f}")

    print("-"*65)
    print(f"\n  Best F1: {best_f1:.3f}")
    if best_state: model.load_state_dict(best_state)

    # ── Final predictions setup ────────────────────────────────────────────────
    facilities = {}
    with open(os.path.join(DATA_DIR,"facilities.csv")) as f:
        for row in csv.DictReader(f): facilities[row["facility_id"]]=row

    model.eval()
    last_X, last_y = te[-1]
    recent_snaps   = all_data[-SEQ_LEN:]
    with torch.no_grad(): preds = model(last_X,adj)

    # ── Social media confirmation ──────────────────────────────────────────────
    print("\n  Loading social media hashtag scores...")
    try:
        from social_media_analyzer import load_hashtag_scores, get_combined_confidence
        snap_dates    = {snap["date"] for snap in recent_snaps}
        social_scores = load_hashtag_scores(target_dates=snap_dates)
        use_social    = True
        print("  ✓ Social media data loaded")
    except Exception as e:
        print(f"  ⚠ Social media not available: {e}")
        social_scores = {}
        use_social    = False

    print("\n"+"="*80)
    print("  Final Predictions — ST-GNN + Social Media Confirmation")
    print("="*80)
    if use_social:
        print(f"  {'ID':<8} {'Upazila':<20} {'Model%':>7} {'Social':>7} "
              f"{'Final%':>7}  {'Disease':<18} Confirmation")
    else:
        print(f"  {'ID':<8} {'Upazila':<20} {'Risk%':>7}  {'Status':<18} Disease")
    print("-"*80)

    alerts=watches=0
    results=[]
    for i,ph_id in enumerate(node_ids):
        ph      = facilities.get(ph_id,{})
        prob    = preds[i].item()
        upazila = ph.get("upazila","")
        disease = detect_disease(i, recent_snaps) if prob>=WATCH else "—"
        true    = last_y[i].item()

        # Extract raw social score safely if available
        if use_social and prob >= WATCH and disease != "—":
            _, sscore, _ = get_combined_confidence(prob, upazila, disease, social_scores)
        else:
            sscore = 0.0

        # --- TWO-FACTOR VERIFICATION LOGIC ---
        model_prob   = float(prob)
        social_score = float(sscore)

        if model_prob >= ALERT_THRESHOLD:
            if social_score >= 0.10:
                status     = "⚠  OUTBREAK RISK"
                csv_status = "ALERT"
                final      = model_prob
                conf       = "(Confirmed by Social Media)"
                alerts    += 1
            else:
                # Sales spike with no social signal — likely a false alarm
                status     = "✓  Normal"
                csv_status = "Normal"
                final      = model_prob
                conf       = "(Sales Spike Only - No Social Signal)"
                disease    = "—"
        elif model_prob >= WATCH:
            status     = "⚡ WATCH"
            csv_status = "WATCH"
            final      = model_prob
            conf       = "(Elevated Risk - Monitor)"
            watches   += 1
        else:
            status     = "✓  Normal"
            csv_status = "Normal"
            final      = model_prob
            conf       = "(Normal)"
            disease    = "—"

        true_str = "(Outbreak)" if true==1 else "(Normal)"
        if use_social:
            print(f"  {ph_id:<8} {upazila:<20} {prob*100:>6.1f}% "
                  f"{sscore:>7.3f} {final*100:>6.1f}%  "
                  f"{disease:<18} {conf} {true_str}")
        else:
            print(f"  {ph_id:<8} {upazila:<20} {prob*100:>6.1f}%  "
                  f"{status:<18} {disease} {true_str}")

        results.append({
            "facility_id":    ph_id,
            "upazila":        upazila,
            "model_prob":     round(prob,4),
            "social_score":   round(sscore,4),
            "final_confidence": round(final,4),
            "status":         csv_status,
            "likely_disease": disease,
            "social_confirmation": conf,
        })

    print("-"*80)
    print(f"\n  ⚠  {alerts} OUTBREAK RISK  |  ⚡ {watches} WATCH  "
          f"|  ✓ {len(node_ids)-alerts-watches} Normal")

    # Save model
    save_path = os.path.join(BASE,"stgnn_model.pt")
    torch.save({"model_state":model.state_dict(),"node_ids":node_ids,
                "config":{"in_features":NUM_FEATURES,"hidden_dim":HIDDEN_DIM,
                           "dropout":DROPOUT,"best_f1":best_f1}}, save_path)
    print(f"\n  ✓ Model saved → {save_path}")

    # Save predictions
    if not results:
        print("  ⚠ No results to save.")
        return
    pred_path = os.path.join(DATA_DIR,"predictions.csv")
    with open(pred_path,"w",newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader(); w.writerows(results)
    print(f"  ✓ Predictions saved → {pred_path}")

    print("\n"+"="*65)
    print("  Phase 3+4 Complete!")
    print("="*65)

if __name__=="__main__":
    train()

