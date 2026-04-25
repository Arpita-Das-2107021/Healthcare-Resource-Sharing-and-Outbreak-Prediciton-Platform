# AI-Powered Epidemic Prediction & Logistics Dispatch System

## Overview

An end-to-end machine learning pipeline that predicts regional disease outbreaks across Bangladesh and autonomously manages emergency medical supply chains in response.

The system works in two modes:

- **Training mode** — runs once to build the model from scratch (GOA → ST-GNN → saved weights)
- **Daily inference mode** — runs every morning in under a second to predict today's outbreaks and dispatch medicine

---

## How It Works

### Stage 1 — Spatiotemporal Graph Neural Network (ST-GNN)
240 facilities across 8 divisions are treated as nodes in a weighted graph. Two facilities are connected by an edge if they are within **2.5 km** of each other, with edge weights inversely proportional to distance. Daily medicine sales (aggregated into 5 disease-signal features) are fed through the graph over a **7-day sliding window**.

The ST-GNN architecture:
- **2× GCN layers** with Xavier initialisation and BatchNorm — capture spatial disease spread between neighbouring facilities
- **2-layer GRU** — captures temporal trends over the 7-day window
- **Attention pooling** — weights each day's hidden state by learned importance
- **2-layer FC head** — outputs a per-facility outbreak probability (0–1)

### Stage 2 — Two-Factor Outbreak Verification
A high model probability alone is not enough to trigger an alert. The system requires **both** signals before dispatching medicine:

| Factor | Condition | Meaning |
|--------|-----------|---------|
| Factor 1 (ST-GNN) | `model_prob ≥ 0.85` | Significant sales spike detected |
| Factor 2 (Social Media) | `social_score ≥ 0.10` | Local population tweeting about illness |

If Factor 1 fires but Factor 2 does not → classified as **"Sales Spike Only"** (likely false alarm, no dispatch).

Three output tiers:
- `ALERT` — both factors confirmed → medicine dispatched immediately
- `WATCH` — model probability between 0.35–0.85 → flagged for monitoring
- `Normal` — below watch threshold

### Stage 3 — Grasshopper Optimization Algorithm (GOA)
Before training, GOA searches for the best hyperparameters (Learning Rate, Hidden Dimensions, Dropout, Batch Size). Each candidate is evaluated by running a real **12-epoch mini-training** on the actual graph data with the full model architecture and class-imbalance weighting — not a proxy formula. The best parameters are saved to `best_params.json` and loaded automatically by the trainer.

### Stage 4 — Peer-to-Peer Logistics Dispatcher
Alert facilities automatically request medicine from the nearest safe (Normal) facility using **Haversine distance**. The dispatcher:
- Sorts safe facilities by distance per alert zone
- Transfers stock greedily from closest to furthest until demand is met
- Reports supply coverage (e.g. `480/560 units (85.7%)`) and any unmet demand

---

## Folder Structure

```
final_outbreak_model/
│
├── data/                              # Data engine & pipeline outputs
│   ├── generate_bangladesh_data.py    # Generates all simulation data (run once)
│   ├── facilities.csv                 # 240 facility GPS coordinates (8 divisions)
│   ├── medicines.csv                  # Drug catalogue with disease signal mappings
│   ├── sales.csv                      # 90 days of simulated transaction logs
│   ├── outbreaks_ground_truth.csv     # Hidden simulation answers for training labels
│   ├── social_media_hashtags.csv      # Wide-format geotagged hashtag counts per day
│   ├── graph_dataset.json             # Temporal graph sequences (built by build_graph.py)
│   ├── best_params.json               # GOA-tuned hyperparameters
│   ├── predictions.csv                # OUTPUT: per-facility outbreak status
│   └── inventory_requests.csv         # OUTPUT: peer-to-peer delivery routes
│
├── models/                            # AI brain & logistics
│   ├── optimize_goa.py                # GOA hyperparameter search (real F1 fitness)
│   ├── train_stgnn_pytorch.py         # Full ST-GNN training (300 epochs)
│   ├── predict.py                     # Daily inference engine (loads saved model)
│   ├── social_media_analyzer.py       # Two-factor hashtag verification
│   ├── marl_dispatcher.py             # Peer-to-peer supply chain routing
│   └── stgnn_model.pt                 # Saved PyTorch model weights (after training)
│
├── utils/
│   └── build_graph.py                 # Converts CSVs into temporal graph JSON
│
├── run_all.py                         # Master orchestrator (full pipeline)
└── README.md
```

---

## Installation

**Requirements:** Python 3.8+, PyTorch, NumPy

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install torch numpy
```

---

## Usage

### Full Training Pipeline (run once)

Runs all 6 steps automatically in order:

```bash
python run_all.py
```

**What it does:**

| Step | Script | Description |
|------|--------|-------------|
| 1 | `generate_bangladesh_data.py` | Simulates 240 facilities, 90 days of sales & social media |
| 2 | `build_graph.py` | Builds temporal graph with data-driven feature normalization |
| 3 | `optimize_goa.py` | GOA searches for best hyperparameters (real F1 evaluation) |
| 4 | `train_stgnn_pytorch.py` | Trains ST-GNN for 300 epochs, saves `stgnn_model.pt` |
| 5 | `predict.py` | Verifies inference pipeline works end-to-end with saved model |
| 6 | `marl_dispatcher.py` | Generates medicine dispatch routes from predictions |

Total runtime: ~5–15 minutes depending on hardware (GOA + 300-epoch training dominate).

---

### Daily Operations (run every morning)

Do **not** retrain the model every day. Instead run just the two inference scripts:

**Step A — Predict today's outbreaks (~1 second):**
```bash
python models/predict.py
```
Output: `data/predictions.csv`

**Step B — Dispatch medicine (~1 second):**
```bash
python models/marl_dispatcher.py
```
Output: `data/inventory_requests.csv`

To update with fresh data: replace the CSV files in `data/`, re-run `build_graph.py`, then run the two steps above.

---

## Output Files

### `data/predictions.csv`
One row per facility. Columns:

| Column | Description |
|--------|-------------|
| `facility_id` | Unique facility identifier (PH0001–PH0240) |
| `upazila` | Division/region name |
| `model_prob` | Raw ST-GNN outbreak probability (0.0–1.0) |
| `social_score` | Normalised hashtag confirmation score (0.0–1.0) |
| `final_confidence` | Weighted combination: `0.7 × model_prob + 0.3 × social_score` |
| `status` | `ALERT` / `WATCH` / `Normal` |
| `likely_disease` | Detected disease type (Fever/Flu, Diarrhea, Respiratory, Allergy/Fever) |
| `social_confirmation` | Human-readable confirmation reason |

### `data/inventory_requests.csv`
One row per transfer. Columns: `requesting_facility`, `requesting_region`, `supplying_facility`, `supplying_region`, `requested_medicine`, `quantity`, `distance_km`

---

## Key Configuration Constants

| Constant | File | Value | Meaning |
|----------|------|-------|---------|
| `ALERT_THRESHOLD` | `train_stgnn_pytorch.py` | 0.85 | Min model_prob to trigger two-factor check |
| `THRESHOLD` | `train_stgnn_pytorch.py` | 0.5 | Binary cutoff for F1/accuracy metrics |
| `WATCH` | both predict files | 0.35 | Min prob to flag for monitoring |
| `EDGE_RADIUS_KM` | `build_graph.py` | 2.5 | Max distance for facility graph edge |
| `SEQ_LEN` | training/predict | 7 | Days of history fed to the model |
| `EPOCHS` | `train_stgnn_pytorch.py` | 300 | Training epochs |
| `BASE_RESERVE_STOCK` | `marl_dispatcher.py` | 150 | Units each safe facility can supply |

---

## Social Media Verification

The social media CSV uses a **wide format** — one column per disease signal per day per upazila:

| Column | Maps to disease |
|--------|----------------|
| `hashtag_dengue` | Fever/Flu |
| `hashtag_fever` | Fever/Flu |
| `hashtag_diarrhea` | Diarrhea |
| `hashtag_respiratory` | Respiratory |
| `hashtag_allergy` | Allergy/Fever |

Scores are normalized per upazila (highest-volume disease = 1.0). The social window is aligned to the exact 7 dates the model predicts on — not the last 7 dates of the full dataset.

In production: replace `data/social_media_hashtags.csv` with real scraped Twitter/Facebook data in the same column format.

---

## Disease → Medicine Mapping

| Detected Disease | Dispatched Medicine |
|-----------------|---------------------|
| Fever/Flu | Paracetamol 500mg |
| Diarrhea | ORS Sachet |
| Respiratory | Amoxicillin 500mg |
| Allergy/Fever | Cetirizine 10mg |

---

## Future Scope

- **Frontend dashboard** — Connect `predictions.csv` and `inventory_requests.csv` to a React/Next.js map interface showing live outbreak zones and allowing administrators to approve dispatches with one click
- **Real data ingestion** — Replace the synthetic CSV generator with live facility POS feeds and scraped social media data
- **GPU acceleration** — The ST-GNN and GOA mini-training run on CPU; adding `.to(device)` calls enables GPU speedup for larger networks
- **Expanded geography** — Increase from 8 divisions to upazila-level (600+ nodes) by adjusting `EDGE_RADIUS_KM` and retraining


