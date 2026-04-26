from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_INPUT_FILES = ("sales.csv", "medicines.csv")
DEFAULT_OUTBREAK_THRESHOLD = 0.5

# Maps medicine disease signal → feature vector index (must match training build_graph.py)
_SIGNAL_GROUPS: dict[str, int] = {
    "Fever/Flu": 0,
    "Diarrhea": 1,
    "Respiratory": 2,
    "Allergy/Fever": 3,
    "Normal": 4,
}
_NUM_FEATURES = 5


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not parse optional outbreak dependency JSON: %s", path)
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_checkpoint_dependencies(model_path: Path) -> dict[str, dict[str, Any]]:
    artifact_dir = model_path if model_path.is_dir() else model_path.parent

    preprocessing = _load_optional_json(artifact_dir / "preprocessing.json")
    scaler = _load_optional_json(artifact_dir / "scaler.json")
    encoder = _load_optional_json(artifact_dir / "encoder.json")

    loaded = []
    if preprocessing:
        loaded.append("preprocessing.json")
    if scaler:
        loaded.append("scaler.json")
    if encoder:
        loaded.append("encoder.json")
    if loaded:
        logger.info(
            "Loaded outbreak artifact dependencies from %s: %s",
            artifact_dir,
            ", ".join(loaded),
        )

    return {
        "preprocessing": preprocessing,
        "scaler": scaler,
        "encoder": encoder,
    }


def _coerce_positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0.0:
        return None
    return parsed


def _resolve_sales_scaler_max(
    scaler_payload: dict[str, Any],
    preprocessing_payload: dict[str, Any],
    fallback: float,
) -> float:
    candidates = [
        scaler_payload.get("max_total_quantity_sold"),
        scaler_payload.get("max_quantity_sold"),
        preprocessing_payload.get("max_total_quantity_sold"),
        preprocessing_payload.get("max_quantity_sold"),
    ]

    for candidate in candidates:
        parsed = _coerce_positive_float(candidate)
        if parsed is not None:
            return parsed

    return max(float(fallback), 1.0)


def _resolve_outbreak_threshold(preprocessing_payload: dict[str, Any]) -> float:
    candidates = [
        preprocessing_payload.get("outbreak_probability_threshold"),
        preprocessing_payload.get("outbreak_threshold"),
        preprocessing_payload.get("threshold"),
    ]
    for candidate in candidates:
        try:
            parsed = float(candidate)
        except (TypeError, ValueError):
            continue
        return min(max(parsed, 0.0), 1.0)
    return DEFAULT_OUTBREAK_THRESHOLD


def _sigmoid(values: pd.Series) -> pd.Series:
    clipped = np.clip(values.astype(float), -8.0, 8.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _ensure_required_files(root: Path) -> None:
    missing = [name for name in REQUIRED_INPUT_FILES if not (root / name).exists()]
    # Also require at least one of facilities.csv or pharmacies.csv
    has_facilities = (root / "facilities.csv").exists() or (root / "pharmacies.csv").exists()
    if not has_facilities:
        missing.append("facilities.csv (or pharmacies.csv)")
    if missing:
        raise FileNotFoundError(
            f"Missing outbreak input files in {root}: {', '.join(sorted(missing))}"
        )


def _load_medicines_map(root: Path) -> dict[str, int]:
    """Load medicines.csv and return {medicine_name: feature_index} mapping."""
    med_path = root / "medicines.csv"
    if not med_path.exists():
        return {}
    try:
        df = pd.read_csv(med_path)
    except Exception:
        return {}
    if "medicine_name" not in df.columns or "signals_disease" not in df.columns:
        return {}
    return {
        str(row["medicine_name"]): _SIGNAL_GROUPS.get(str(row["signals_disease"]), 4)
        for _, row in df.iterrows()
    }


def _load_inputs(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    _ensure_required_files(root)

    sales = pd.read_csv(root / "sales.csv")

    # Accept pharmacies.csv (pharmacy_id) or facilities.csv (facility_id)
    if (root / "pharmacies.csv").exists() and not (root / "facilities.csv").exists():
        facilities = pd.read_csv(root / "pharmacies.csv")
        if "pharmacy_id" in facilities.columns and "facility_id" not in facilities.columns:
            facilities = facilities.rename(columns={"pharmacy_id": "facility_id"})
        id_col = "facility_id"
    else:
        facilities = pd.read_csv(root / "facilities.csv")
        id_col = "facility_id"

    if "healthcare_id" in facilities.columns and "facility_id" not in facilities.columns:
        facilities = facilities.rename(columns={"healthcare_id": "facility_id"})

    # Normalize sales id column: accept pharmacy_id or facility_id
    if "pharmacy_id" in sales.columns and "facility_id" not in sales.columns:
        sales = sales.rename(columns={"pharmacy_id": "facility_id"})
    if "healthcare_id" in sales.columns and "facility_id" not in sales.columns:
        sales = sales.rename(columns={"healthcare_id": "facility_id"})

    required_sales_columns = {"date", "facility_id", "medicine_name", "quantity_sold"}
    required_facility_columns = {"facility_id", "upazila", "lat", "lon"}

    if not required_sales_columns.issubset(sales.columns):
        missing = sorted(required_sales_columns - set(sales.columns))
        raise ValueError(f"sales.csv is missing columns: {missing}")

    if not required_facility_columns.issubset(facilities.columns):
        missing = sorted(required_facility_columns - set(facilities.columns))
        raise ValueError(f"facilities/pharmacies.csv is missing columns: {missing}")

    sales = sales.copy()
    sales["date"] = pd.to_datetime(sales["date"], errors="coerce")
    sales = sales.dropna(subset=["date"])
    sales["quantity_sold"] = pd.to_numeric(sales["quantity_sold"], errors="coerce").fillna(0.0)
    sales["facility_id"] = sales["facility_id"].astype(str)

    facilities = facilities.copy()
    facilities["facility_id"] = facilities["facility_id"].astype(str)
    facilities["upazila"] = facilities["upazila"].astype(str)
    facilities["lat"] = pd.to_numeric(facilities["lat"], errors="coerce")
    facilities["lon"] = pd.to_numeric(facilities["lon"], errors="coerce")
    facilities = facilities.dropna(subset=["lat", "lon"])

    return sales, facilities


def _build_edges(facilities: pd.DataFrame, radius_km: float) -> tuple[list[str], list[dict]]:
    facilities = facilities.sort_values("facility_id").reset_index(drop=True)
    node_ids = facilities["facility_id"].tolist()

    edges: list[dict] = []
    for i in range(len(facilities)):
        for j in range(i + 1, len(facilities)):
            row_i = facilities.iloc[i]
            row_j = facilities.iloc[j]
            distance = _haversine_km(row_i["lat"], row_i["lon"], row_j["lat"], row_j["lon"])
            if distance <= radius_km:
                edges.append(
                    {
                        "from_idx": i,
                        "to_idx": j,
                        "from_id": row_i["facility_id"],
                        "to_id": row_j["facility_id"],
                        "distance_km": round(distance, 3),
                    }
                )

    return node_ids, edges


def _build_neighbor_index(node_ids: list[str], edges: list[dict]) -> dict[str, list[dict]]:
    neighbors: dict[str, list[dict]] = {node_id: [] for node_id in node_ids}

    for edge in edges:
        from_id = str(edge["from_id"])
        to_id = str(edge["to_id"])
        distance_km = round(float(edge["distance_km"]), 3)

        neighbors.setdefault(from_id, []).append(
            {"facility_id": to_id, "distance_km": distance_km}
        )
        neighbors.setdefault(to_id, []).append(
            {"facility_id": from_id, "distance_km": distance_km}
        )

    for node_id in neighbors:
        neighbors[node_id] = sorted(
            neighbors[node_id],
            key=lambda item: (float(item["distance_km"]), str(item["facility_id"])),
        )

    return neighbors


def _neighbor_rows(
    node_ids: list[str],
    edges: list[dict],
    facilities: pd.DataFrame,
    max_neighbors: int | None = None,
) -> dict[str, list[dict]]:
    if max_neighbors is not None and max_neighbors < 1:
        raise ValueError("max_neighbors must be >= 1 when provided")

    upazila_lookup = (
        facilities[["facility_id", "upazila"]]
        .drop_duplicates(subset=["facility_id"])
        .set_index("facility_id")["upazila"]
        .astype(str)
        .to_dict()
    )

    neighbor_index = _build_neighbor_index(node_ids=node_ids, edges=edges)
    rows: dict[str, list[dict]] = {}

    for node_id in node_ids:
        linked = neighbor_index.get(node_id, [])
        if max_neighbors is not None:
            linked = linked[:max_neighbors]

        rows[node_id] = [
            {
                "facility_id": str(item["facility_id"]),
                "upazila": upazila_lookup.get(str(item["facility_id"]), ""),
                "distance_km": round(float(item["distance_km"]), 3),
            }
            for item in linked
        ]

    return rows


def _save_graph_dataset(
    root: Path,
    sales: pd.DataFrame,
    node_ids: list[str],
    edges: list[dict],
    sequence_length: int,
) -> Path:
    medicines_map = _load_medicines_map(root)
    sales_with_date = sales.copy()
    sales_with_date["date_str"] = sales_with_date["date"].dt.strftime("%Y-%m-%d")
    sales_with_date["feat_idx"] = sales_with_date["medicine_name"].astype(str).map(
        lambda m: medicines_map.get(m, 4)
    )

    # Build {(date_str, facility_id, feat_idx): total_qty}
    feat_totals = (
        sales_with_date.groupby(["date_str", "facility_id", "feat_idx"], as_index=False)["quantity_sold"]
        .sum()
    )

    # Per-feature max for normalization
    feat_max = [1.0] * _NUM_FEATURES
    for _, row in feat_totals.iterrows():
        fi = int(row["feat_idx"])
        if fi < _NUM_FEATURES and float(row["quantity_sold"]) > feat_max[fi]:
            feat_max[fi] = float(row["quantity_sold"])

    feat_lookup: dict[tuple[str, str, int], float] = {
        (str(row["date_str"]), str(row["facility_id"]), int(row["feat_idx"])): float(row["quantity_sold"])
        for _, row in feat_totals.iterrows()
    }

    dates = sorted(feat_totals["date_str"].unique().tolist())
    snapshots = []
    for day_index, date_str in enumerate(dates):
        node_feats = []
        for node_id in node_ids:
            feats = [
                feat_lookup.get((date_str, node_id, fi), 0.0) / feat_max[fi]
                for fi in range(_NUM_FEATURES)
            ]
            node_feats.append(feats)

        snapshots.append(
            {
                "day": day_index,
                "date": date_str,
                "node_ids": node_ids,
                "node_feats": node_feats,
                "node_labels": [],
                "edges": edges,
                "sequence_length": sequence_length,
            }
        )

    out_path = root / "graph_dataset.json"
    out_path.write_text(json.dumps(snapshots, indent=2), encoding="utf-8")
    return out_path


def _heuristic_outbreak_inference(
    sales: pd.DataFrame,
    facilities: pd.DataFrame,
    node_ids: list[str],
    edges: list[dict],
    sequence_length: int,
) -> pd.DataFrame:
    sales_with_day = sales.copy()
    sales_with_day["day"] = sales_with_day["date"].dt.date

    daily_totals = (
        sales_with_day.groupby(["day", "facility_id"], as_index=False)["quantity_sold"]
        .sum()
    )

    pivot = daily_totals.pivot(index="day", columns="facility_id", values="quantity_sold").fillna(0.0)
    for node_id in node_ids:
        if node_id not in pivot.columns:
            pivot[node_id] = 0.0
    pivot = pivot.reindex(columns=node_ids)

    if pivot.empty:
        baseline = pd.Series(0.0, index=node_ids)
        recent = pd.Series(0.0, index=node_ids)
    else:
        recent_window = min(sequence_length, len(pivot))
        recent = pivot.tail(recent_window).mean(axis=0)

        baseline_source = pivot.iloc[:-recent_window] if len(pivot) > recent_window else pivot
        baseline_window = min(sequence_length, len(baseline_source))
        baseline = baseline_source.tail(baseline_window).mean(axis=0)

    trend = (recent - baseline) / (baseline + 1.0)

    neighbor_index = _build_neighbor_index(node_ids=node_ids, edges=edges)

    neighbor_trend: dict[str, float] = {}
    global_trend = float(trend.mean()) if len(trend) else 0.0
    for node_id in node_ids:
        linked = [neighbor["facility_id"] for neighbor in neighbor_index.get(node_id, [])]
        if not linked:
            neighbor_trend[node_id] = global_trend
            continue
        neighbor_trend[node_id] = float(trend.loc[linked].mean())

    trend_std = float(trend.std()) if float(trend.std()) > 1e-6 else 1.0
    trend_z = (trend - float(trend.mean())) / trend_std
    neighbor_series = pd.Series(neighbor_trend)
    neighbor_std = float(neighbor_series.std()) if float(neighbor_series.std()) > 1e-6 else 1.0
    neighbor_z = (neighbor_series - float(neighbor_series.mean())) / neighbor_std

    logits = 1.2 * trend_z + 0.8 * neighbor_z
    probabilities = _sigmoid(logits)

    results = facilities[["facility_id", "upazila"]].copy()
    results = results.drop_duplicates(subset=["facility_id"]).set_index("facility_id")
    results = results.reindex(node_ids)
    results["outbreak_probability"] = probabilities.reindex(node_ids).fillna(0.0)
    results["outbreak_flag"] = results["outbreak_probability"] >= DEFAULT_OUTBREAK_THRESHOLD

    return results.reset_index()


def _try_checkpoint_outbreak_inference(
    sales: pd.DataFrame,
    facilities: pd.DataFrame,
    node_ids: list[str],
    edges: list[dict],
    sequence_length: int,
    model_path: Path,
    data_dir: Path | None = None,
) -> pd.DataFrame | None:
    if not model_path.exists() or model_path.suffix.lower() != ".pt":
        return None

    try:
        import torch
        import torch.nn as nn
        import torch.nn.functional as F
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.info("PyTorch unavailable; fallback heuristic enabled: %s", exc)
        return None

    class GCNLayer(nn.Module):
        def __init__(self, in_features: int, out_features: int) -> None:
            super().__init__()
            self.linear = nn.Linear(in_features, out_features, bias=True)

        def forward(self, x, adj):
            return F.elu(self.linear(adj @ x))

    class STGNN(nn.Module):
        def __init__(self, in_features: int, hidden: int, dropout: float = 0.3) -> None:
            super().__init__()
            self.gcn1 = GCNLayer(in_features, hidden)
            self.gcn2 = GCNLayer(hidden, hidden)
            self.gru = nn.GRU(hidden, hidden, batch_first=True, num_layers=2, dropout=dropout)
            self.attn = nn.Linear(hidden, 1)
            self.drop = nn.Dropout(dropout)
            self.fc1 = nn.Linear(hidden, hidden // 2)
            self.fc2 = nn.Linear(hidden // 2, 1)
            self.bn1 = nn.BatchNorm1d(hidden)
            self.bn2 = nn.BatchNorm1d(hidden)

        def forward(self, x_seq, adj):
            seq_len, _, _ = x_seq.shape
            out = []
            for t in range(seq_len):
                hidden = self.bn1(self.gcn1(x_seq[t], adj))
                hidden = self.bn2(self.gcn2(self.drop(hidden), adj))
                out.append(hidden.unsqueeze(0))
            seq = torch.cat(out, 0).permute(1, 0, 2)
            g, _ = self.gru(seq)
            ctx = (torch.softmax(self.attn(g), 1) * g).sum(1)
            return torch.sigmoid(self.fc2(F.elu(self.fc1(self.drop(ctx))))).squeeze()

    try:
        checkpoint = torch.load(model_path, map_location="cpu")
    except Exception as exc:
        logger.warning("Could not load outbreak checkpoint %s: %s", model_path, exc)
        return None

    if not isinstance(checkpoint, dict):
        logger.warning("Unsupported outbreak checkpoint format: %s", model_path)
        return None

    config = checkpoint.get("config")
    model_state = checkpoint.get("model_state")
    if not isinstance(config, dict) or not isinstance(model_state, dict):
        logger.warning("Checkpoint missing config/model_state: %s", model_path)
        return None

    dependencies = _load_checkpoint_dependencies(model_path)
    preprocessing_payload = dependencies.get("preprocessing", {})
    scaler_payload = dependencies.get("scaler", {})

    in_features = int(config.get("in_features", _NUM_FEATURES))
    hidden_dim = int(config.get("hidden_dim", 64))
    dropout = float(config.get("dropout", 0.3))
    in_features = max(in_features, 1)
    outbreak_threshold = _resolve_outbreak_threshold(preprocessing_payload)

    # Load medicines mapping for proper 5-feature extraction
    artifact_dir = model_path if model_path.is_dir() else model_path.parent
    medicines_map = _load_medicines_map(data_dir) if data_dir else {}
    if not medicines_map:
        medicines_map = _load_medicines_map(artifact_dir)

    sales_with_date = sales.copy()
    sales_with_date["date_str"] = sales_with_date["date"].dt.strftime("%Y-%m-%d")
    sales_with_date["feat_idx"] = sales_with_date["medicine_name"].astype(str).map(
        lambda m: medicines_map.get(m, 4)
    )

    feat_totals = (
        sales_with_date.groupby(["date_str", "facility_id", "feat_idx"], as_index=False)["quantity_sold"]
        .sum()
    )

    if feat_totals.empty:
        return None

    feat_max = [1.0] * in_features
    for _, row in feat_totals.iterrows():
        fi = int(row["feat_idx"])
        if fi < in_features and float(row["quantity_sold"]) > feat_max[fi]:
            feat_max[fi] = float(row["quantity_sold"])

    feat_lookup: dict[tuple[str, str, int], float] = {
        (str(row["date_str"]), str(row["facility_id"]), int(row["feat_idx"])): float(row["quantity_sold"])
        for _, row in feat_totals.iterrows()
    }

    dates = sorted(feat_totals["date_str"].unique().tolist())
    recent_dates = dates[-sequence_length:]
    if len(recent_dates) < sequence_length:
        recent_dates = (["__padding__"] * (sequence_length - len(recent_dates))) + recent_dates

    node_feats_seq: list[list[list[float]]] = []
    for date_key in recent_dates:
        day_feats: list[list[float]] = []
        for facility_id in node_ids:
            if date_key == "__padding__":
                feat = [0.0] * in_features
            else:
                feat = [
                    feat_lookup.get((date_key, facility_id, fi), 0.0) / feat_max[fi]
                    for fi in range(in_features)
                ]
            day_feats.append(feat)
        node_feats_seq.append(day_feats)

    idx_by_id = {facility_id: idx for idx, facility_id in enumerate(node_ids)}
    adj = torch.zeros((len(node_ids), len(node_ids)), dtype=torch.float32)
    for edge in edges:
        from_id = str(edge["from_id"])
        to_id = str(edge["to_id"])
        if from_id not in idx_by_id or to_id not in idx_by_id:
            continue
        i = idx_by_id[from_id]
        j = idx_by_id[to_id]
        weight = 1.0 / (float(edge["distance_km"]) + 1e-6)
        adj[i][j] = weight
        adj[j][i] = weight

    adj += torch.eye(len(node_ids), dtype=torch.float32)
    adj = adj / adj.sum(1, keepdim=True).clamp(min=1e-9)

    model = STGNN(in_features=in_features, hidden=hidden_dim, dropout=dropout)
    try:
        model.load_state_dict(model_state, strict=False)
    except Exception as exc:
        logger.warning("Could not apply checkpoint model_state from %s: %s", model_path, exc)
        return None

    model.eval()
    with torch.no_grad():
        preds = model(torch.tensor(node_feats_seq, dtype=torch.float32), adj)

    probs = preds.detach().cpu().numpy()
    probs = np.array(probs, dtype=float).reshape(-1)
    if len(probs) != len(node_ids):
        logger.warning(
            "Checkpoint inference output mismatch for %s (expected %s, got %s)",
            model_path,
            len(node_ids),
            len(probs),
        )
        return None

    results = facilities[["facility_id", "upazila"]].copy()
    results = results.drop_duplicates(subset=["facility_id"]).set_index("facility_id")
    results = results.reindex(node_ids)
    results["outbreak_probability"] = pd.Series(probs, index=node_ids)
    results["outbreak_flag"] = results["outbreak_probability"] >= outbreak_threshold

    logger.info(
        "Using pretrained ST-GNN checkpoint for outbreak inference: %s (threshold=%.3f)",
        model_path,
        outbreak_threshold,
    )
    return results.reset_index()


def _run_outbreak_pipeline_core(
    input_dir: str,
    model_path: str | None,
    graph_radius_km: float,
    sequence_length: int,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[dict]]:
    root = Path(input_dir)
    logger.info("Outbreak pipeline started in %s", root)

    sales, facilities = _load_inputs(root)
    node_ids, edges = _build_edges(facilities, radius_km=graph_radius_km)

    graph_path = _save_graph_dataset(
        root=root,
        sales=sales,
        node_ids=node_ids,
        edges=edges,
        sequence_length=sequence_length,
    )
    logger.info("Graph dataset generated at %s", graph_path)

    result_df = None
    if model_path:
        checkpoint_result = _try_checkpoint_outbreak_inference(
            sales=sales,
            facilities=facilities,
            node_ids=node_ids,
            edges=edges,
            sequence_length=sequence_length,
            model_path=Path(model_path),
            data_dir=root,
        )
        if checkpoint_result is not None:
            result_df = checkpoint_result

    if result_df is None:
        logger.info("Using deterministic outbreak fallback inference")
        result_df = _heuristic_outbreak_inference(
            sales=sales,
            facilities=facilities,
            node_ids=node_ids,
            edges=edges,
            sequence_length=sequence_length,
        )

    result_df["outbreak_probability"] = result_df["outbreak_probability"].clip(0.0, 1.0)
    result_df["outbreak_probability"] = result_df["outbreak_probability"].round(4)
    result_df["outbreak_flag"] = result_df["outbreak_flag"].astype(bool)

    return result_df, facilities, node_ids, edges


def _to_standardized_rows(result_df: pd.DataFrame) -> list[dict]:
    return result_df[["facility_id", "upazila", "outbreak_probability", "outbreak_flag"]].to_dict(
        orient="records"
    )


def run_outbreak_pipeline(
    input_dir: str,
    model_path: str | None = None,
    graph_radius_km: float = 10.0,
    sequence_length: int = 7,
) -> list[dict]:
    result_df, _, _, _ = _run_outbreak_pipeline_core(
        input_dir=input_dir,
        model_path=model_path,
        graph_radius_km=graph_radius_km,
        sequence_length=sequence_length,
    )

    rows = _to_standardized_rows(result_df)

    logger.info("Outbreak pipeline produced %s rows", len(rows))
    return rows


def run_outbreak_pipeline_with_neighbors(
    input_dir: str,
    model_path: str | None = None,
    graph_radius_km: float = 10.0,
    sequence_length: int = 7,
    max_neighbors: int | None = None,
) -> dict[str, object]:
    """Run outbreak inference and return rows plus graph-based neighboring facilities."""
    result_df, facilities, node_ids, edges = _run_outbreak_pipeline_core(
        input_dir=input_dir,
        model_path=model_path,
        graph_radius_km=graph_radius_km,
        sequence_length=sequence_length,
    )

    rows = _to_standardized_rows(result_df)
    neighbors = _neighbor_rows(
        node_ids=node_ids,
        edges=edges,
        facilities=facilities,
        max_neighbors=max_neighbors,
    )

    logger.info(
        "Outbreak pipeline produced %s rows with neighbor metadata for %s nodes",
        len(rows),
        len(neighbors),
    )
    return {
        "results": rows,
        "neighbors": neighbors,
    }