"""
optimize_goa.py — Grasshopper Optimization Algorithm
=====================================================
Finds best hyperparameters for ST-GNN automatically.
Saves best_params.json → train_stgnn_pytorch.py loads it.

Based on Paper 3 (DGO-ST-GNN) DGOA implementation.
RUN: python models/optimize_goa.py
"""

import json, os, random, math
import numpy as np

random.seed(42)
np.random.seed(42)

# ── Cached training data (loaded once, reused across all fitness calls) ────────
_GOA_DATA = None

def _ensure_data_loaded():
    global _GOA_DATA
    if _GOA_DATA is not None:
        return _GOA_DATA
    graph_path = os.path.join(DATA_DIR, "graph_dataset.json")
    if not os.path.exists(graph_path):
        return None
    try:
        import torch
        with open(graph_path) as f:
            raw = json.load(f)
        SEQ_LEN = 7; NUM_FEATURES = 5
        num_nodes = len(raw[0]["node_ids"])
        adj = torch.zeros(num_nodes, num_nodes)
        for e in raw[0]["edges"]:
            i, j = e["from_idx"], e["to_idx"]
            w = 1.0 / (e["distance_km"] + 1e-6)
            adj[i][j] = w; adj[j][i] = w
        adj += torch.eye(num_nodes)
        adj = adj / adj.sum(1, keepdim=True).clamp(min=1e-9)
        seqs = []
        for t in range(SEQ_LEN, len(raw)):
            X = torch.tensor(
                [raw[t - SEQ_LEN + s]["node_feats"] for s in range(SEQ_LEN)],
                dtype=torch.float32)
            y = torch.tensor(raw[t]["node_labels"], dtype=torch.float32)
            seqs.append((X, y))
        split = int(0.8 * len(seqs))
        _GOA_DATA = (seqs[:split], seqs[split:], adj)
        return _GOA_DATA
    except Exception:
        return None

BASE        = os.path.dirname(os.path.abspath(__file__))
DATA_DIR    = os.path.join(BASE, "../data")
PARAMS_PATH = os.path.join(DATA_DIR, "best_params.json")

# ── Search Space ──────────────────────────────────────────────────────────────
SEARCH_SPACE = {
    "LR":         (0.0001, 0.01),
    "HIDDEN_DIM": (32,     128),
    "DROPOUT":    (0.1,    0.5),
    "BATCH_SIZE": (16,     64),
}
N_AGENTS = 10
N_ITER   = 20
C_MIN    = 0.00004
C_MAX    = 1.0

# ── Fitness: real F1 from a quick mini-training run ───────────────────────────
def fitness(params):
    """
    Trains a lightweight ST-GNN for MINI_EPOCHS on the actual graph data
    and returns the F1 score on the held-out validation split.
    Falls back to a proxy formula only if graph_dataset.json is not yet built.
    """
    import torch
    import torch.nn as nn
    import torch.nn.functional as F_nn

    data = _ensure_data_loaded()
    if data is None:
        # Proxy fallback: used only when GOA runs before build_graph.py
        lr = params["LR"]; hd = params["HIDDEN_DIM"]; dr = params["DROPOUT"]
        s = 1.0 - abs(math.log10(max(lr, 1e-9)) + 2.5) / 2.5
        return max(0.0, min(1.0, 0.4*s + 0.4*(hd-32)/96 + 0.2*(1-abs(dr-0.3)/0.3)))

    tr, te, adj = data
    lr = params["LR"]; hidden = int(params["HIDDEN_DIM"]); dropout = params["DROPOUT"]
    MINI_EPOCHS = 12; THRESHOLD = 0.5; NUM_FEATURES = 5

    # Full architecture — identical to train_stgnn_pytorch.py so GOA tunes the right model
    class _GCN(nn.Module):
        def __init__(self, in_f, out_f):
            super().__init__()
            self.linear = nn.Linear(in_f, out_f, bias=True)
            nn.init.xavier_uniform_(self.linear.weight)
        def forward(self, x, a):
            return F_nn.elu(self.linear(a @ x))

    class _STGNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.gcn1 = _GCN(NUM_FEATURES, hidden)
            self.gcn2 = _GCN(hidden, hidden)
            self.gru  = nn.GRU(hidden, hidden, batch_first=True,
                               num_layers=2, dropout=dropout)
            self.attn = nn.Linear(hidden, 1)
            self.drop = nn.Dropout(dropout)
            self.fc1  = nn.Linear(hidden, hidden // 2)
            self.fc2  = nn.Linear(hidden // 2, 1)
            self.bn1  = nn.BatchNorm1d(hidden)
            self.bn2  = nn.BatchNorm1d(hidden)
        def forward(self, x_seq, a):
            seq_len = x_seq.shape[0]
            out = []
            for t in range(seq_len):
                h = self.bn1(self.gcn1(x_seq[t], a))
                h = self.bn2(self.gcn2(self.drop(h), a))
                out.append(h.unsqueeze(0))
            seq = torch.cat(out, 0).permute(1, 0, 2)
            g, _ = self.gru(seq)
            ctx = (torch.softmax(self.attn(g), 1) * g).sum(1)
            return torch.sigmoid(self.fc2(F_nn.elu(self.fc1(self.drop(ctx))))).squeeze()

    # Match full training: use pos_weight to handle class imbalance
    all_labels = torch.cat([y for _, y in tr])
    pos = all_labels.sum().item(); neg = len(all_labels) - pos
    pos_weight = torch.tensor([neg / (pos + 1e-9)])

    torch.manual_seed(0)
    model = _STGNN()
    opt = torch.optim.Adam(model.parameters(), lr=lr)

    model.train()
    for _ in range(MINI_EPOCHS):
        for X, y in tr:
            opt.zero_grad()
            w = torch.where(y == 1, pos_weight.expand_as(y), torch.ones_like(y))
            F_nn.binary_cross_entropy(model(X, adj), y, weight=w).backward()
            opt.step()

    model.eval()
    with torch.no_grad():
        ap = torch.cat([model(X, adj) for X, _ in te])
        at = torch.cat([y for _, y in te])
    pb = (ap >= THRESHOLD).float()
    tp = ((pb == 1) & (at == 1)).float().sum().item()
    fp = ((pb == 1) & (at == 0)).float().sum().item()
    fn = ((pb == 0) & (at == 1)).float().sum().item()
    pr = tp / (tp + fp + 1e-9)
    re = tp / (tp + fn + 1e-9)
    return 2 * pr * re / (pr + re + 1e-9)

def clip(params):
    clipped = {}
    for key, val in params.items():
        if key in SEARCH_SPACE:
            lo, hi = SEARCH_SPACE[key]
            clipped[key] = max(lo, min(hi, val))
            
            # Enforce integer types for specific parameters
            if key in ['HIDDEN_DIM', 'BATCH_SIZE']:
                clipped[key] = int(clipped[key])
        else:
            clipped[key] = val
            
    return clipped

def social_force(dist, f=0.5, l=1.5):
    return f * math.exp(-dist/l) - math.exp(-dist)

def run_goa():
    print("\n"+"="*60)
    print("  Step 3 — Grasshopper Optimization Algorithm")
    print("  Finding best hyperparameters for ST-GNN")
    print("="*60)
    print(f"\n  Agents: {N_AGENTS}  |  Iterations: {N_ITER}")
    print(f"  Search space:")
    for k,(lo,hi) in SEARCH_SPACE.items():
        print(f"    {k:<12}: [{lo}, {hi}]")
    print()

    keys   = list(SEARCH_SPACE.keys())
    agents = []
    for _ in range(N_AGENTS):
        p = {k: random.uniform(lo,hi) for k,(lo,hi) in SEARCH_SPACE.items()}
        p = clip(p)
        agents.append({"params":p, "fitness":fitness(p)})

    agents.sort(key=lambda a:a["fitness"], reverse=True)
    best = agents[0].copy()

    print(f"  {'Iter':>4}  {'Fitness':>8}  {'LR':>10}  {'Hidden':>7}  {'Dropout':>8}")
    print("  "+"-"*50)

    for itr in range(1, N_ITER+1):
        c = C_MAX - itr*((C_MAX-C_MIN)/N_ITER)
        new_agents = []

        for i, agent in enumerate(agents):
            np_ = {k:0.0 for k in keys}
            for j, other in enumerate(agents):
                if i==j: continue
                for k in keys:
                    lo,hi = SEARCH_SPACE[k]
                    dist  = abs(agent["params"][k]-other["params"][k])
                    s     = social_force(dist/((hi-lo)+1e-9))
                    d     = 1 if other["params"][k]>agent["params"][k] else -1
                    np_[k]+= c*(hi-lo)/2*s*d/(N_AGENTS-1)

            for k in keys:
                lo,hi    = SEARCH_SPACE[k]
                np_[k]   = c*np_[k] + best["params"][k] + random.gauss(0,0.01)*(hi-lo)

            p = clip(np_)

            # Levy flight
            if random.random()<0.15:
                lk      = random.choice(keys)
                lo,hi   = SEARCH_SPACE[lk]
                p[lk]   = clip({lk: p[lk]+random.choice([-1,1])*random.paretovariate(1.5)*(hi-lo)*0.05})[lk]

            # Gaussian mutation
            if random.random()<0.1:
                mk      = random.choice(keys)
                p[mk]   = clip({mk: p[mk]*(1+random.gauss(0,0.1))})[mk]

            new_agents.append({"params":p,"fitness":fitness(p)})

        # Opposition-based learning
        new_agents.sort(key=lambda a:a["fitness"],reverse=True)
        for a in new_agents[N_AGENTS//2:]:
            opp     = {k:SEARCH_SPACE[k][0]+SEARCH_SPACE[k][1]-a["params"][k] for k in keys}
            opp     = clip(opp)
            opp_fit = fitness(opp)
            if opp_fit>a["fitness"]:
                a["params"]=opp; a["fitness"]=opp_fit

        agents = new_agents
        agents.sort(key=lambda a:a["fitness"],reverse=True)
        if agents[0]["fitness"]>best["fitness"]:
            best = agents[0].copy()

        if itr%5==0 or itr==1:
            p = best["params"]
            print(f"  {itr:>4}  {best['fitness']:>8.4f}  "
                  f"{p['LR']:>10.5f}  {int(p['HIDDEN_DIM']):>7}  {p['DROPOUT']:>8.3f}")

    print("  "+"-"*50)
    print(f"\n  Best parameters found:")
    for k,v in best["params"].items():
        print(f"    {k:<12}: {v}")

    save = {
        "LR":         float(best["params"]["LR"]),
        "HIDDEN_DIM": int(best["params"]["HIDDEN_DIM"]),
        "DROPOUT":    float(best["params"]["DROPOUT"]),
        "BATCH_SIZE": int(best["params"]["BATCH_SIZE"]),
        "goa_fitness": float(best["fitness"]),
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PARAMS_PATH,"w") as f:
        json.dump(save, f, indent=2)

    print(f"\n  ✓ Saved best params → {PARAMS_PATH}")
    print("="*60)
    return save

if __name__ == "__main__":
    run_goa()

