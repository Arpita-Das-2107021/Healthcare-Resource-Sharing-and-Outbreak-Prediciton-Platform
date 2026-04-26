"""
Phase 3 — ST-GNN Training (Disease Outbreak Prediction)
Implements a simplified Spatiotemporal GNN inspired by:
  - Paper 3 (DGO-ST-GNN): stacked ST-Conv blocks
  - Paper 2 (Evolve-DGN): evolving GCN with LSTM
  - Paper 1 (VRE GNN):    temporal graph sequences

Architecture:
  Input: 7-day sequence of facility graph snapshots
  → GraphConv layers (spatial)
  → GRU (temporal)
  → Fully connected → outbreak probability per facility-node

Requirements: pip install torch torch-geometric numpy
"""

import json
import os
import math
import random
import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# We implement a pure-numpy version so it runs WITHOUT a GPU.
# When you're ready for real training, swap in the PyTorch version below.
# ─────────────────────────────────────────────────────────────────────────────

DATA_PATH   = os.path.join(os.path.dirname(__file__), "../data/graph_dataset.json")
SEQUENCE_LEN = 7          # days of history
NUM_FEATURES = 5          # disease-group sales features
HIDDEN_DIM   = 32         # GNN hidden size (increase to 128 for real training)
EPOCHS       = 30
LR           = 0.01
random.seed(42)
np.random.seed(42)

# ── Load dataset ──────────────────────────────────────────────────────────────
def load_dataset():
    with open(DATA_PATH) as f:
        data = json.load(f)
    return data

def make_sequences(data, seq_len=SEQUENCE_LEN):
    """
    Slide a window of seq_len days → predict label on day seq_len.
    Returns list of (X, y, adj) tuples:
      X   : (seq_len, num_nodes, num_features) numpy array
      y   : (num_nodes,) binary labels
      adj : (num_nodes, num_nodes) adjacency matrix
    """
    num_nodes = len(data[0]["node_ids"])
    # Build adjacency matrix (static)
    adj = np.zeros((num_nodes, num_nodes), dtype=np.float32)
    for e in data[0]["edges"]:
        i, j = e["from_idx"], e["to_idx"]
        w = 1.0 / (e["distance_km"] + 1e-6)   # weight = inverse distance
        adj[i][j] = w
        adj[j][i] = w
    # Row-normalise
    row_sum = adj.sum(axis=1, keepdims=True) + 1e-9
    adj_norm = adj / row_sum

    sequences = []
    for t in range(seq_len, len(data)):
        X = np.array(
            [data[t - seq_len + s]["node_feats"] for s in range(seq_len)],
            dtype=np.float32
        )  # (seq_len, num_nodes, num_features)
        y = np.array(data[t]["node_labels"], dtype=np.float32)
        sequences.append((X, y, adj_norm))
    return sequences

# ── Simplified GCN layer (numpy) ──────────────────────────────────────────────
def gcn_forward(X, adj, W):
    """
    X   : (num_nodes, in_features)
    adj : (num_nodes, num_nodes)
    W   : (in_features, out_features)
    Returns: ReLU(adj @ X @ W)
    """
    h = adj @ X @ W
    return np.maximum(h, 0)   # ReLU

def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -10, 10)))

def bce_loss(pred, target, eps=1e-7):
    pred = np.clip(pred, eps, 1 - eps)
    return -np.mean(target * np.log(pred) + (1 - target) * np.log(1 - pred))

# ── Simple ST-GNN (GCN + mean pooling over time) ─────────────────────────────
class SimpleSTGNN:
    """
    Simplified ST-GNN for testing the pipeline.
    Replace with PyTorch version (see below) for real training.
    """
    def __init__(self, in_features, hidden, out_features=1):
        scale = 0.1
        self.W1 = np.random.randn(in_features, hidden).astype(np.float32) * scale
        self.W2 = np.random.randn(hidden, hidden).astype(np.float32) * scale
        self.Wout = np.random.randn(hidden, out_features).astype(np.float32) * scale
        self.b = np.zeros(out_features, dtype=np.float32)

    def forward(self, X_seq, adj):
        """
        X_seq : (seq_len, num_nodes, features)
        Returns: (num_nodes,) outbreak probabilities
        """
        embeddings = []
        for t in range(X_seq.shape[0]):
            h = gcn_forward(X_seq[t], adj, self.W1)   # spatial pass 1
            h = gcn_forward(h, adj, self.W2)           # spatial pass 2
            embeddings.append(h)
        # Temporal: mean over sequence (simplified; replace with GRU)
        h_temporal = np.mean(embeddings, axis=0)       # (num_nodes, hidden)
        out = h_temporal @ self.Wout + self.b          # (num_nodes, 1)
        return sigmoid(out.squeeze())                   # (num_nodes,)

    def train_step(self, X_seq, y, adj, lr=LR):
        # Forward
        pred = self.forward(X_seq, adj)
        loss = bce_loss(pred, y)
        # Numerical gradient (for demo; use autograd in PyTorch)
        eps  = 1e-4
        for W in [self.W1, self.W2, self.Wout]:
            grad = np.zeros_like(W)
            for idx in np.ndindex(W.shape):
                W[idx] += eps
                loss_p = bce_loss(self.forward(X_seq, adj), y)
                W[idx] -= 2*eps
                loss_m = bce_loss(self.forward(X_seq, adj), y)
                W[idx] += eps
                grad[idx] = (loss_p - loss_m) / (2 * eps)
            W -= lr * grad
        return loss

# ── Training loop ─────────────────────────────────────────────────────────────
def train():
    print("Loading graph dataset...")
    data      = load_dataset()
    sequences = make_sequences(data)
    num_nodes = len(data[0]["node_ids"])
    node_ids  = data[0]["node_ids"]

    print(f"  {len(sequences)} training sequences  |  {num_nodes} nodes")

    # Split 80/20
    split     = int(0.8 * len(sequences))
    train_seq = sequences[:split]
    test_seq  = sequences[split:]

    model = SimpleSTGNN(in_features=NUM_FEATURES, hidden=HIDDEN_DIM)

    print(f"\nTraining for {EPOCHS} epochs...")
    print("─" * 45)
    for epoch in range(1, EPOCHS + 1):
        random.shuffle(train_seq)
        total_loss = 0
        for X, y, adj in train_seq:
            loss = model.train_step(X, y, adj, lr=LR)
            total_loss += loss

        if epoch % 5 == 0 or epoch == 1:
            # Evaluate on test set
            all_pred, all_true = [], []
            for X, y, adj in test_seq:
                pred = model.forward(X, adj)
                all_pred.extend(pred.tolist())
                all_true.extend(y.tolist())
            all_pred = np.array(all_pred)
            all_true = np.array(all_true)
            preds_bin = (all_pred > 0.5).astype(float)
            acc = (preds_bin == all_true).mean()
            tp  = ((preds_bin == 1) & (all_true == 1)).sum()
            fp  = ((preds_bin == 1) & (all_true == 0)).sum()
            fn  = ((preds_bin == 0) & (all_true == 1)).sum()
            prec = tp / (tp + fp + 1e-9)
            rec  = tp / (tp + fn + 1e-9)
            f1   = 2 * prec * rec / (prec + rec + 1e-9)
            avg_loss = total_loss / len(train_seq)
            print(f"  Epoch {epoch:3d}  loss={avg_loss:.4f}  "
                  f"acc={acc:.3f}  prec={prec:.3f}  rec={rec:.3f}  F1={f1:.3f}")

    print("─" * 45)
    print("\n✓ Training complete!")

    # ── Show predictions for the last day ────────────────────────────────────
    print("\nPredictions for the LAST snapshot:")
    print(f"{'Facility':<12} {'Upazila':<20} {'P(outbreak)':<14} {'True label'}")
    print("─" * 60)

    last_seq = test_seq[-1]
    X, y, adj = last_seq
    pred = model.forward(X, adj)

    # Load facility info for display
    import csv
    facilities = {}
    ph_csv = os.path.join(os.path.dirname(__file__), "../data/facilities.csv")
    with open(ph_csv) as f:
        for row in csv.DictReader(f):
            facilities[row["facility_id"]] = row

    for i, ph_id in enumerate(node_ids):
        ph    = facilities.get(ph_id, {})
        label = "⚠ OUTBREAK" if y[i] == 1 else "Normal"
        flag  = "  ← ALERT" if pred[i] > 0.5 and y[i] == 1 else (
                "  ← FALSE ALARM" if pred[i] > 0.5 and y[i] == 0 else "")
        print(f"  {ph_id:<12} {ph.get('upazila',''):<20} {pred[i]:.3f}          {label}{flag}")

    return model, sequences, node_ids

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    train()
    print("\n" + "═"*55)
    print("  Next step: run  python models/marl_dispatcher.py")
    print("═"*55)


# ══════════════════════════════════════════════════════════════════════════════
# PYTORCH VERSION (Phase 3 real model — uncomment when you have GPU/PyTorch)
# ══════════════════════════════════════════════════════════════════════════════
"""
import torch
import torch.nn as nn
from torch_geometric.nn import GCNConv

class STGNN(nn.Module):
    '''
    Spatiotemporal GNN (Paper 3 architecture):
      - GCNConv layers for spatial feature extraction
      - GRU for temporal dependencies across the 7-day window
      - Output: binary classification per node
    '''
    def __init__(self, in_features, hidden, out=1):
        super().__init__()
        self.gcn1 = GCNConv(in_features, hidden)
        self.gcn2 = GCNConv(hidden, hidden)
        self.gru  = nn.GRU(hidden, hidden, batch_first=True)
        self.fc   = nn.Linear(hidden, out)

    def forward(self, x_seq, edge_index):
        # x_seq: (seq_len, num_nodes, in_features)
        seq_embeds = []
        for t in range(x_seq.shape[0]):
            h = torch.relu(self.gcn1(x_seq[t], edge_index))
            h = torch.relu(self.gcn2(h, edge_index))
            seq_embeds.append(h.unsqueeze(0))  # (1, num_nodes, hidden)

        h_seq = torch.cat(seq_embeds, dim=0).permute(1, 0, 2)  # (nodes, seq, hidden)
        _, h_n = self.gru(h_seq)                                # h_n: (1, nodes, hidden)
        out = torch.sigmoid(self.fc(h_n.squeeze(0)))            # (nodes, 1)
        return out.squeeze()

# Training loop with PyTorch:
# model = STGNN(NUM_FEATURES, 128)
# optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
# criterion = nn.BCELoss()
# for epoch in range(100):
#     for X, y, adj in train_loader:
#         optimizer.zero_grad()
#         pred = model(X, edge_index)
#         loss = criterion(pred, y)
#         loss.backward()
#         optimizer.step()
"""


