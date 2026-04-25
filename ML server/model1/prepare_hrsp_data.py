from pathlib import Path

import numpy as np
import pandas as pd


def _column_or_default(frame: pd.DataFrame, column: str, default: object) -> pd.Series:
    """Return an existing column as Series, or a same-length default Series."""
    if column in frame.columns:
        return frame[column]
    return pd.Series(default, index=frame.index)


def resolve_input_files(base_dir: Path) -> dict:
    """Resolve expected input CSV paths, supporting alternate file names."""
    candidates = {
        "sales": ["sales.csv"],
        "medicine_info": ["medicine_info.csv", "medicines.csv"],
        "disease": ["disease.csv", "outbreaks_ground_truth.csv"],
        "healthcare": ["healthcare.csv", "healthcares.csv"],
    }

    resolved = {}
    for key, names in candidates.items():
        match = next((base_dir / name for name in names if (base_dir / name).exists()), None)
        if match is None:
            readable = ", ".join(names)
            raise FileNotFoundError(f"Could not find any file for '{key}'. Tried: {readable}")
        resolved[key] = match

    return resolved


def load_csvs(file_map: dict) -> dict:
    """Load all required CSV files into pandas DataFrames."""
    return {
        "sales": pd.read_csv(file_map["sales"]),
        "medicine_info": pd.read_csv(file_map["medicine_info"]),
        "disease": pd.read_csv(file_map["disease"]),
        "healthcare": pd.read_csv(file_map["healthcare"]),
    }


def preprocess_sales(sales_df: pd.DataFrame) -> pd.DataFrame:
    """Preprocess sales data: datetime conversion, sorting, and missing value handling."""
    df = sales_df.copy()

    # Accept either healthcare_id or facility_id in incoming snapshots.
    if "healthcare_id" not in df.columns and "facility_id" in df.columns:
        df = df.rename(columns={"facility_id": "healthcare_id"})

    # Convert date column to pandas datetime.
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    # Drop rows with critical missing values required for time series modeling.
    critical_cols = ["date", "medicine_name", "quantity_sold"]
    df = df.dropna(subset=critical_cols)

    # Fill optional string fields with placeholders to keep a consistent schema.
    if "healthcare_id" in df.columns:
        df["healthcare_id"] = df["healthcare_id"].fillna("UNKNOWN")
    if "upazila" in df.columns:
        df["upazila"] = df["upazila"].fillna("UNKNOWN")

    # Ensure quantity is numeric and replace invalid entries with 0.
    df["quantity_sold"] = pd.to_numeric(df["quantity_sold"], errors="coerce").fillna(0)

    # Sort records by date for stable downstream processing.
    df = df.sort_values("date").reset_index(drop=True)
    return df


def build_daily_medicine_timeseries(sales_df: pd.DataFrame) -> pd.DataFrame:
    """Group by date/medicine and fill missing dates with 0 sales for each medicine."""
    # Aggregate total daily sales per medicine.
    daily = (
        sales_df.groupby(["date", "medicine_name"], as_index=False)["quantity_sold"]
        .sum()
        .sort_values(["medicine_name", "date"])
    )

    min_date = daily["date"].min()
    max_date = daily["date"].max()
    all_dates = pd.date_range(start=min_date, end=max_date, freq="D")

    # Build complete per-medicine date index and fill missing sales with 0.
    frames = []
    for medicine, med_df in daily.groupby("medicine_name", sort=True):
        series = med_df.set_index("date")["quantity_sold"]
        reindexed = series.reindex(all_dates, fill_value=0)
        out = reindexed.reset_index()
        out.columns = ["date", "quantity_sold"]
        out["medicine_name"] = medicine
        frames.append(out)

    result = pd.concat(frames, ignore_index=True)
    return result[["date", "medicine_name", "quantity_sold"]]


def create_model_frames(timeseries_df: pd.DataFrame) -> dict:
    """Create model-ready ds/y DataFrame for each medicine."""
    model_data = {}
    for medicine, med_df in timeseries_df.groupby("medicine_name", sort=True):
        frame = med_df[["date", "quantity_sold"]].copy()
        frame = frame.rename(columns={"date": "ds", "quantity_sold": "y"})
        frame = frame.sort_values("ds").reset_index(drop=True)
        model_data[medicine] = frame
    return model_data


def split_train_test(model_data: dict, test_days: int = 7) -> dict:
    """Split each medicine series into train/test, using last N days for test."""
    split_data = {}
    for medicine, df in model_data.items():
        if len(df) <= test_days:
            train_df = df.iloc[0:0].copy()
            test_df = df.copy()
        else:
            train_df = df.iloc[:-test_days].copy()
            test_df = df.iloc[-test_days:].copy()

        split_data[medicine] = {
            "train": train_df,
            "test": test_df,
        }
    return split_data


def prepare_hrsp_data(base_dir: str = ".", test_days: int = 7) -> dict:
    """Prepare one combined medicine-level dataset across all hospitals."""
    root = Path(base_dir)
    files = resolve_input_files(root)
    datasets = load_csvs(files)
    sales_clean = preprocess_sales(datasets["sales"])

    daily_ts = build_daily_medicine_timeseries(sales_clean)
    model_frames = create_model_frames(daily_ts)
    processed = split_train_test(model_frames, test_days=test_days)
    return processed


def print_summary(processed_data: dict) -> None:
    """Print requested summary information."""
    all_medicines = sorted(processed_data.keys())
    print(f"Number of medicines: {len(all_medicines)}")

    if not all_medicines:
        print("Date range: N/A")
        print("No medicine data available.")
        return

    # Date range is derived from one medicine since all were reindexed to full daily range.
    sample_key = all_medicines[0]
    sample_full = pd.concat(
        [processed_data[sample_key]["train"], processed_data[sample_key]["test"]],
        ignore_index=True,
    )
    start_date = sample_full["ds"].min().date()
    end_date = sample_full["ds"].max().date()
    print(f"Date range: {start_date} to {end_date}")

    print(f"\nSample medicine: {sample_key}")
    print("Train sample (first 5 rows):")
    print(processed_data[sample_key]["train"].head())
    print("\nTest sample (last 5 rows):")
    print(processed_data[sample_key]["test"].tail())


def prepare_hrsp_data_per_hospital(base_dir: str = ".", test_days: int = 7) -> dict:
    """Prepare separate time series and forecasts per hospital."""
    root = Path(base_dir)
    files = resolve_input_files(root)
    datasets = load_csvs(files)
    sales_clean = preprocess_sales(datasets["sales"])

    hospital_results = {}
    for hospital_id, hosp_df in sales_clean.groupby("healthcare_id", sort=True):
        daily_ts = build_daily_medicine_timeseries(hosp_df)
        model_frames = create_model_frames(daily_ts)
        processed = split_train_test(model_frames, test_days=test_days)
        hospital_results[hospital_id] = processed

    return hospital_results


def print_summary_per_hospital(hospital_data: dict) -> None:
    """Print a compact summary when data is grouped by hospital."""
    hospital_ids = sorted(hospital_data.keys())
    print(f"Number of hospitals: {len(hospital_ids)}")

    if not hospital_ids:
        print("No hospital data available.")
        return

    sample_hospital = hospital_ids[0]
    medicines = sorted(hospital_data[sample_hospital].keys())
    print(f"Sample hospital: {sample_hospital}")
    print(f"Medicines in sample hospital: {len(medicines)}")

    if medicines:
        sample_medicine = medicines[0]
        sample_train = hospital_data[sample_hospital][sample_medicine]["train"]
        sample_test = hospital_data[sample_hospital][sample_medicine]["test"]
        sample_full = pd.concat([sample_train, sample_test], ignore_index=True)
        if not sample_full.empty:
            print(
                "Date range in sample hospital: "
                f"{sample_full['ds'].min().date()} to {sample_full['ds'].max().date()}"
            )
        print(f"Sample medicine in hospital: {sample_medicine}")
        print(sample_test.head())


def build_daily_entity_medicine_timeseries(sales_df: pd.DataFrame) -> pd.DataFrame:
    """Build a dense daily panel per healthcare_id and medicine_name."""
    if sales_df.empty:
        return pd.DataFrame(
            columns=["date", "healthcare_id", "upazila", "medicine_name", "quantity_sold"]
        )

    base = sales_df.copy()
    if "healthcare_id" not in base.columns and "facility_id" in base.columns:
        base = base.rename(columns={"facility_id": "healthcare_id"})

    base["healthcare_id"] = _column_or_default(base, "healthcare_id", "UNKNOWN").fillna("UNKNOWN").astype(str)
    base["medicine_name"] = base["medicine_name"].fillna("UNKNOWN").astype(str)
    base["upazila"] = _column_or_default(base, "upazila", "UNKNOWN").fillna("UNKNOWN").astype(str)

    daily = (
        base.groupby(["date", "healthcare_id", "medicine_name"], as_index=False)["quantity_sold"]
        .sum()
        .sort_values(["healthcare_id", "medicine_name", "date"])
    )

    all_dates = pd.date_range(daily["date"].min(), daily["date"].max(), freq="D")
    upazila_per_entity = (
        base.groupby("healthcare_id")["upazila"]
        .agg(lambda s: s.mode().iloc[0] if not s.mode().empty else s.iloc[0])
        .to_dict()
    )

    frames = []
    for (healthcare_id, medicine_name), pair_df in daily.groupby(
        ["healthcare_id", "medicine_name"],
        sort=True,
    ):
        series = pair_df.set_index("date")["quantity_sold"].reindex(all_dates, fill_value=0)
        out = series.reset_index()
        out.columns = ["date", "quantity_sold"]
        out["healthcare_id"] = healthcare_id
        out["medicine_name"] = medicine_name
        out["upazila"] = upazila_per_entity.get(str(healthcare_id), "UNKNOWN")
        frames.append(out)

    panel = pd.concat(frames, ignore_index=True)
    panel = panel[["date", "healthcare_id", "upazila", "medicine_name", "quantity_sold"]]
    panel = panel.sort_values(["healthcare_id", "medicine_name", "date"]).reset_index(drop=True)
    return panel


def _split_pipe_values(raw_value: object) -> list[str]:
    """Split a pipe-separated string into cleaned tokens."""
    if pd.isna(raw_value):
        return []
    return [token.strip() for token in str(raw_value).split("|") if token and token.strip()]


def _build_outbreak_lookup(disease_df: pd.DataFrame) -> dict:
    """Map (upazila, medicine) to outbreak intervals for fast feature lookup."""
    required_cols = {"start_day", "end_day", "upazilas", "medicines"}
    if disease_df is None or disease_df.empty or not required_cols.issubset(disease_df.columns):
        return {}

    lookup: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for _, row in disease_df.iterrows():
        try:
            start_day = int(row["start_day"])
            end_day = int(row["end_day"])
        except (TypeError, ValueError):
            continue

        upazilas = _split_pipe_values(row["upazilas"])
        medicines = _split_pipe_values(row["medicines"])

        for upazila in upazilas:
            for medicine_name in medicines:
                key = (upazila, medicine_name)
                lookup.setdefault(key, []).append((start_day, end_day))

    return lookup


def _compute_outbreak_signal(panel_df: pd.DataFrame, lookup: dict) -> pd.Series:
    """Return per-row outbreak intensity as count of active matching intervals."""
    if panel_df.empty or not lookup:
        return pd.Series(0.0, index=panel_df.index)

    def _row_score(row: pd.Series) -> float:
        key = (str(row["upazila"]), str(row["medicine_name"]))
        intervals = lookup.get(key, [])
        day_index = int(row["day_index"])
        return float(sum(1 for start_day, end_day in intervals if start_day <= day_index <= end_day))

    return panel_df.apply(_row_score, axis=1)


def _future_horizon_sum(series: pd.Series, horizon_days: int) -> pd.Series:
    """Compute next-horizon summed demand for each row in a time series."""
    shifted = [series.shift(-offset) for offset in range(1, horizon_days + 1)]
    return pd.concat(shifted, axis=1).sum(axis=1, min_count=horizon_days)


def prepare_hrsp_global_shareable_data(
    base_dir: str = ".",
    horizon_days: int = 7,
    test_days: int = 7,
    buffer_days: int = 3,
    share_ratio: float = 0.8,
) -> dict:
    """Prepare global feature table for one cross-entity shareable-amount model."""
    if horizon_days <= 0:
        raise ValueError("horizon_days must be > 0.")
    if test_days <= 0:
        raise ValueError("test_days must be > 0.")
    if buffer_days < 0:
        raise ValueError("buffer_days must be >= 0.")
    if not (0 <= share_ratio <= 1):
        raise ValueError("share_ratio must be between 0 and 1.")

    root = Path(base_dir)
    files = resolve_input_files(root)
    datasets = load_csvs(files)

    sales_clean = preprocess_sales(datasets["sales"])
    healthcare_df = datasets["healthcare"].copy()
    medicine_df = datasets["medicine_info"].copy()
    disease_df = datasets["disease"].copy()

    if "healthcare_id" not in healthcare_df.columns and "facility_id" in healthcare_df.columns:
        healthcare_df = healthcare_df.rename(columns={"facility_id": "healthcare_id"})

    panel = build_daily_entity_medicine_timeseries(sales_clean)
    if panel.empty:
        empty_cols = ["date", "healthcare_id", "medicine_name", "target_shareable_amount"]
        empty = pd.DataFrame(columns=empty_cols)
        return {
            "train": empty.copy(),
            "test": empty.copy(),
            "inference": empty.copy(),
            "categorical_columns": ["healthcare_id", "upazila", "medicine_name", "signals_disease"],
            "numeric_columns": [],
            "feature_columns": [],
            "target_column": "target_shareable_amount",
            "horizon_days": horizon_days,
            "buffer_days": buffer_days,
            "share_ratio": share_ratio,
            "meta": {
                "rows": 0,
                "entities": 0,
                "medicines": 0,
            },
        }

    healthcare_cols = [col for col in ["healthcare_id", "name", "upazila", "lat", "lon", "capacity"] if col in healthcare_df.columns]
    if "healthcare_id" in healthcare_cols:
        healthcare_meta = healthcare_df[healthcare_cols].drop_duplicates(subset=["healthcare_id"])
        panel = panel.merge(healthcare_meta, on="healthcare_id", how="left", suffixes=("", "_healthcare"))

    medicine_cols = [
        col
        for col in ["medicine_name", "base_daily_sales", "outbreak_multiplier", "signals_disease"]
        if col in medicine_df.columns
    ]
    if "medicine_name" in medicine_cols:
        medicine_meta = medicine_df[medicine_cols].drop_duplicates(subset=["medicine_name"])
        panel = panel.merge(medicine_meta, on="medicine_name", how="left")

    if "upazila_healthcare" in panel.columns:
        panel["upazila"] = panel["upazila"].fillna(panel["upazila_healthcare"])
    panel["upazila"] = panel["upazila"].fillna("UNKNOWN").astype(str)

    panel["lat"] = pd.to_numeric(_column_or_default(panel, "lat", 0.0), errors="coerce").fillna(0.0)
    panel["lon"] = pd.to_numeric(_column_or_default(panel, "lon", 0.0), errors="coerce").fillna(0.0)
    panel["base_daily_sales"] = pd.to_numeric(_column_or_default(panel, "base_daily_sales", 0.0), errors="coerce").fillna(0.0)
    panel["outbreak_multiplier"] = (
        pd.to_numeric(_column_or_default(panel, "outbreak_multiplier", 1.0), errors="coerce").fillna(1.0)
    )
    panel["signals_disease"] = _column_or_default(panel, "signals_disease", "Unknown").fillna("Unknown").astype(str)

    panel = panel.sort_values(["healthcare_id", "medicine_name", "date"]).reset_index(drop=True)
    panel["day_index"] = (panel["date"] - panel["date"].min()).dt.days + 1
    panel["day_of_week"] = panel["date"].dt.dayofweek.astype(int)
    panel["month"] = panel["date"].dt.month.astype(int)
    panel["day_of_year"] = panel["date"].dt.dayofyear.astype(int)
    panel["is_weekend"] = (panel["day_of_week"] >= 5).astype(int)
    panel["date_ordinal"] = panel["date"].map(pd.Timestamp.toordinal).astype(float)

    outbreak_lookup = _build_outbreak_lookup(disease_df)
    panel["outbreak_signal"] = _compute_outbreak_signal(panel, outbreak_lookup)

    pair_group = panel.groupby(["healthcare_id", "medicine_name"], sort=False)
    panel["lag_1_sales"] = pair_group["quantity_sold"].shift(1).fillna(0.0)
    panel["lag_7_sales"] = pair_group["quantity_sold"].shift(7).fillna(0.0)
    panel["rolling_mean_7"] = (
        pair_group["quantity_sold"]
        .transform(lambda s: s.shift(1).rolling(window=7, min_periods=1).mean())
        .fillna(0.0)
    )
    panel["rolling_mean_14"] = (
        pair_group["quantity_sold"]
        .transform(lambda s: s.shift(1).rolling(window=14, min_periods=1).mean())
        .fillna(0.0)
    )
    panel["rolling_std_14"] = (
        pair_group["quantity_sold"]
        .transform(lambda s: s.shift(1).rolling(window=14, min_periods=2).std())
        .fillna(0.0)
    )
    panel["entity_medicine_expanding_mean"] = (
        pair_group["quantity_sold"].transform(lambda s: s.shift(1).expanding(min_periods=1).mean()).fillna(0.0)
    )

    entity_daily = (
        panel.groupby(["healthcare_id", "date"], as_index=False)["quantity_sold"]
        .sum()
        .sort_values(["healthcare_id", "date"])
    )
    entity_daily["entity_capacity_proxy"] = (
        entity_daily.groupby("healthcare_id")["quantity_sold"]
        .transform(lambda s: s.shift(1).rolling(window=14, min_periods=1).mean())
        .fillna(0.0)
    )
    panel = panel.merge(
        entity_daily[["healthcare_id", "date", "entity_capacity_proxy"]],
        on=["healthcare_id", "date"],
        how="left",
    )
    panel["entity_capacity_proxy"] = panel["entity_capacity_proxy"].fillna(0.0)

    if "capacity" in panel.columns:
        panel["capacity_feature"] = pd.to_numeric(panel["capacity"], errors="coerce")
    else:
        panel["capacity_feature"] = np.nan
    panel["capacity_feature"] = panel["capacity_feature"].fillna(panel["entity_capacity_proxy"])
    panel["utilization_ratio"] = np.where(
        panel["capacity_feature"] > 0,
        panel["rolling_mean_7"] / panel["capacity_feature"],
        0.0,
    )

    medicine_daily = (
        panel.groupby(["medicine_name", "date"], as_index=False)["quantity_sold"]
        .sum()
        .sort_values(["medicine_name", "date"])
    )
    medicine_daily["medicine_global_rolling_mean_7"] = (
        medicine_daily.groupby("medicine_name")["quantity_sold"]
        .transform(lambda s: s.shift(1).rolling(window=7, min_periods=1).mean())
        .fillna(0.0)
    )
    panel = panel.merge(
        medicine_daily[["medicine_name", "date", "medicine_global_rolling_mean_7"]],
        on=["medicine_name", "date"],
        how="left",
    )
    panel["medicine_global_rolling_mean_7"] = panel["medicine_global_rolling_mean_7"].fillna(0.0)

    pair_key = panel["healthcare_id"].astype(str) + "|" + panel["medicine_name"].astype(str)
    hashed = pd.util.hash_pandas_object(pair_key, index=False).astype("uint64")
    panel["inventory_cover_days"] = 8.0 + ((hashed % 1000).astype(float) / 1000.0) * 10.0
    panel["current_stock_estimate"] = np.round(panel["rolling_mean_14"] * panel["inventory_cover_days"]).astype(float)
    panel["expiry_risk_index"] = np.where(
        panel["rolling_mean_14"] > 0,
        panel["current_stock_estimate"] / (panel["rolling_mean_14"] * max(float(horizon_days), 1.0)),
        panel["inventory_cover_days"],
    )
    panel["expiry_risk_index"] = panel["expiry_risk_index"].clip(lower=0.0, upper=100.0)

    panel["future_horizon_demand"] = pair_group["quantity_sold"].transform(
        lambda s: _future_horizon_sum(s, horizon_days=horizon_days)
    )
    panel["demand_uncertainty"] = panel["rolling_std_14"]
    panel["safety_buffer"] = panel["rolling_mean_14"] * float(buffer_days)
    panel["adjusted_future_demand"] = panel["future_horizon_demand"] + panel["demand_uncertainty"]

    panel["target_shareable_amount"] = (
        panel["current_stock_estimate"] - panel["adjusted_future_demand"] - panel["safety_buffer"]
    ).clip(lower=0.0) * float(share_ratio)
    panel["target_restock_alert"] = panel["current_stock_estimate"] < (
        panel["adjusted_future_demand"] + panel["safety_buffer"]
    )

    categorical_columns = ["healthcare_id", "upazila", "medicine_name", "signals_disease"]
    numeric_columns = [
        "lat",
        "lon",
        "capacity_feature",
        "base_daily_sales",
        "outbreak_multiplier",
        "day_of_week",
        "month",
        "day_of_year",
        "is_weekend",
        "date_ordinal",
        "lag_1_sales",
        "lag_7_sales",
        "rolling_mean_7",
        "rolling_mean_14",
        "rolling_std_14",
        "entity_medicine_expanding_mean",
        "entity_capacity_proxy",
        "utilization_ratio",
        "medicine_global_rolling_mean_7",
        "outbreak_signal",
        "inventory_cover_days",
        "current_stock_estimate",
        "expiry_risk_index",
        "demand_uncertainty",
        "safety_buffer",
    ]

    for col in categorical_columns:
        panel[col] = panel[col].fillna("UNKNOWN").astype(str)
    for col in numeric_columns:
        panel[col] = pd.to_numeric(panel[col], errors="coerce").fillna(0.0)

    target_ready = panel.dropna(subset=["future_horizon_demand"]).copy()
    if target_ready.empty:
        raise ValueError("Not enough historical rows to compute shareable target for the selected horizon.")

    max_date = target_ready["date"].max()
    cutoff_date = max_date - pd.Timedelta(days=test_days)
    train_df = target_ready[target_ready["date"] <= cutoff_date].copy()
    test_df = target_ready[target_ready["date"] > cutoff_date].copy()

    if train_df.empty or test_df.empty:
        ordered = target_ready.sort_values("date").reset_index(drop=True)
        split_idx = int(len(ordered) * 0.8)
        split_idx = max(1, min(split_idx, len(ordered) - 1))
        train_df = ordered.iloc[:split_idx].copy()
        test_df = ordered.iloc[split_idx:].copy()

    inference_df = (
        panel.sort_values("date")
        .groupby(["healthcare_id", "medicine_name"], as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )

    return {
        "train": train_df,
        "test": test_df,
        "inference": inference_df,
        "categorical_columns": categorical_columns,
        "numeric_columns": numeric_columns,
        "feature_columns": categorical_columns + numeric_columns,
        "target_column": "target_shareable_amount",
        "horizon_days": horizon_days,
        "buffer_days": buffer_days,
        "share_ratio": share_ratio,
        "meta": {
            "rows": int(len(panel)),
            "entities": int(panel["healthcare_id"].nunique()),
            "medicines": int(panel["medicine_name"].nunique()),
            "start_date": str(panel["date"].min().date()),
            "end_date": str(panel["date"].max().date()),
        },
    }


if __name__ == "__main__":
    data_dict = prepare_hrsp_data_per_hospital(base_dir=".", test_days=7)
    print_summary_per_hospital(data_dict)
