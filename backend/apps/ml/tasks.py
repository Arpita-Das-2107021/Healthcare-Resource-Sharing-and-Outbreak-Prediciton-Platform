"""Celery tasks for ML dataset pipeline and dispatch."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import timedelta
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.hospitals.models import Hospital
from apps.resources.models import ResourceCatalog

from .dataset_pipeline import (
    HEALTHCARES_FILE_NAME,
    MEDICINES_FILE_NAME,
    SALES_FILE_NAME,
    generate_and_upload_ml_input_datasets,
)
from .models import MLJob, MLJobEvent, MLSchedule, MLTrainingDatasetSnapshot, MLTrainingJob
from .scheduling import compute_next_run_at, evaluate_schedule, resolve_timezone_for_facility
from .training_services import get_active_model_version_name  # noqa: F401

logger = logging.getLogger("hrsp.ml")


TERMINAL_STATUSES = {
    MLJob.Status.COMPLETED,
    MLJob.Status.PARTIAL_COMPLETED,
    MLJob.Status.FAILED,
    MLJob.Status.CANCELLED,
}


class TrainingSnapshotMirrorError(RuntimeError):
    """Raised when training snapshot artifacts cannot be mirrored safely."""


def _client_error_code(exc: ClientError) -> str:
    return str(((exc.response or {}).get("Error") or {}).get("Code") or "")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parse_minio_uri(uri: str) -> tuple[str, str]:
    raw_uri = str(uri or "").strip()
    if not raw_uri.startswith("minio://"):
        raise TrainingSnapshotMirrorError(f"Unsupported training artifact URI: {raw_uri}")

    remainder = raw_uri[len("minio://") :]
    bucket, _, object_key = remainder.partition("/")
    if not bucket or not object_key:
        raise TrainingSnapshotMirrorError(f"Invalid training artifact URI: {raw_uri}")

    return bucket, object_key


def _build_minio_client(*, endpoint_url: str, access_key: str, secret_key: str, region_name: str):
    endpoint = str(endpoint_url or "").strip()
    if not endpoint:
        raise TrainingSnapshotMirrorError("MinIO endpoint URL is required for snapshot mirroring.")

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=str(access_key or "").strip() or None,
        aws_secret_access_key=str(secret_key or "").strip() or None,
        region_name=str(region_name or "").strip() or "us-east-1",
    )


def _head_object_or_none(client, bucket: str, object_key: str):
    try:
        return client.head_object(Bucket=bucket, Key=object_key)
    except ClientError as exc:
        if _client_error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def _ensure_bucket(client, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if _client_error_code(exc) in {"404", "NoSuchBucket", "NotFound"}:
            client.create_bucket(Bucket=bucket)
            return
        raise


def _write_training_mirror_dead_letter(*, training_job_id: str, dataset_id: str, payload: dict) -> None:
    record = {
        "timestamp": timezone.now().isoformat(),
        "training_job_id": training_job_id,
        "dataset_id": dataset_id,
        "payload": payload,
    }
    log_path = Path(
        str(
            getattr(
                settings,
                "ML_TRAINING_MIRROR_DEAD_LETTER_LOG_PATH",
                "/app/logs/ml_training_mirror_dead_letter.jsonl",
            )
        )
    )

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str))
            handle.write("\n")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to write ML training mirror dead-letter record for job %s", training_job_id)


def _mirror_single_training_object(
    *,
    source_client,
    destination_client,
    source_uri: str,
    expected_sha256: str,
    destination_bucket_override: str,
    destination_prefix: str,
) -> tuple[str, dict]:
    source_bucket, source_key = _parse_minio_uri(source_uri)

    destination_bucket = str(destination_bucket_override or "").strip() or source_bucket
    normalized_prefix = str(destination_prefix or "").strip().strip("/")
    destination_key = f"{normalized_prefix}/{source_key}" if normalized_prefix else source_key

    source_head = _head_object_or_none(source_client, source_bucket, source_key)
    if source_head is None:
        raise TrainingSnapshotMirrorError(f"Source artifact does not exist: {source_uri}")

    source_body = source_client.get_object(Bucket=source_bucket, Key=source_key)["Body"].read()
    source_sha256 = _sha256_bytes(source_body)
    expected_hash = str(expected_sha256 or "").strip().lower()
    if expected_hash and source_sha256.lower() != expected_hash:
        raise TrainingSnapshotMirrorError(
            f"Checksum mismatch for source artifact {source_uri}: expected={expected_hash} actual={source_sha256}"
        )

    _ensure_bucket(destination_client, destination_bucket)
    destination_head = _head_object_or_none(destination_client, destination_bucket, destination_key)
    destination_uri = f"minio://{destination_bucket}/{destination_key}"

    if destination_head:
        destination_metadata = {
            str(key).strip().lower(): str(value)
            for key, value in (destination_head.get("Metadata") or {}).items()
        }
        mirrored_hash = str(destination_metadata.get("sha256") or "").strip().lower()
        if mirrored_hash and mirrored_hash == source_sha256.lower():
            return destination_uri, {
                "copied": False,
                "reason": "already_mirrored_metadata_match",
                "checksum_sha256": source_sha256,
            }

        destination_body = destination_client.get_object(Bucket=destination_bucket, Key=destination_key)["Body"].read()
        destination_sha256 = _sha256_bytes(destination_body)
        if destination_sha256.lower() == source_sha256.lower():
            return destination_uri, {
                "copied": False,
                "reason": "already_mirrored_content_match",
                "checksum_sha256": source_sha256,
            }

    destination_client.put_object(
        Bucket=destination_bucket,
        Key=destination_key,
        Body=source_body,
        Metadata={
            "sha256": source_sha256,
            "source_bucket": source_bucket,
            "source_key": source_key,
        },
    )

    verify_body = destination_client.get_object(Bucket=destination_bucket, Key=destination_key)["Body"].read()
    verify_sha256 = _sha256_bytes(verify_body)
    if verify_sha256.lower() != source_sha256.lower():
        raise TrainingSnapshotMirrorError(
            f"Checksum mismatch after mirror for {destination_uri}: expected={source_sha256} actual={verify_sha256}"
        )

    return destination_uri, {
        "copied": True,
        "reason": "copied_and_verified",
        "checksum_sha256": source_sha256,
    }


def _mirror_single_training_object_with_retry(
    *,
    source_client,
    destination_client,
    source_uri: str,
    expected_sha256: str,
    destination_bucket_override: str,
    destination_prefix: str,
) -> tuple[str, dict]:
    attempts = max(1, int(getattr(settings, "ML_TRAINING_MIRROR_MAX_RETRIES", 3)))
    backoff_seconds = max(1, int(getattr(settings, "ML_TRAINING_MIRROR_BACKOFF_SECONDS", 1)))
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return _mirror_single_training_object(
                source_client=source_client,
                destination_client=destination_client,
                source_uri=source_uri,
                expected_sha256=expected_sha256,
                destination_bucket_override=destination_bucket_override,
                destination_prefix=destination_prefix,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "Training artifact mirror attempt %s/%s failed for %s: %s",
                attempt,
                attempts,
                source_uri,
                exc,
            )
            if attempt < attempts:
                time.sleep(backoff_seconds * (2 ** (attempt - 1)))

    raise TrainingSnapshotMirrorError(
        f"Failed to mirror training artifact after {attempts} attempts for {source_uri}: {last_error}"
    )


def _mirror_training_snapshot_inputs(
    *,
    training_job: MLTrainingJob,
    dataset: MLTrainingDatasetSnapshot,
    dataset_manifest: dict,
    files: dict,
) -> dict:
    if not bool(getattr(settings, "ML_TRAINING_MIRROR_ENABLED", True)):
        return {
            "files": files,
            "outbreaks_file_path": ((dataset_manifest.get("outbreaks_ground_truth") or {}).get("uri")),
            "audit": {"enabled": False},
        }

    source_endpoint = str(getattr(settings, "MINIO_ENDPOINT_URL", "") or "").strip()
    source_access_key = str(getattr(settings, "MINIO_ACCESS_KEY", "") or "").strip()
    source_secret_key = str(getattr(settings, "MINIO_SECRET_KEY", "") or "").strip()
    source_region = str(getattr(settings, "MINIO_REGION_NAME", "us-east-1") or "us-east-1").strip()

    destination_endpoint = str(
        getattr(settings, "ML_SERVER_B_MINIO_ENDPOINT_URL", "")
        or source_endpoint
    ).strip()
    destination_access_key = str(
        getattr(settings, "ML_SERVER_B_MINIO_ACCESS_KEY", "")
        or source_access_key
    ).strip()
    destination_secret_key = str(
        getattr(settings, "ML_SERVER_B_MINIO_SECRET_KEY", "")
        or source_secret_key
    ).strip()
    destination_region = str(
        getattr(settings, "ML_SERVER_B_MINIO_REGION_NAME", "")
        or source_region
    ).strip()
    destination_bucket_override = str(getattr(settings, "ML_SERVER_B_MINIO_BUCKET_NAME_OVERRIDE", "") or "").strip()
    destination_prefix = str(getattr(settings, "ML_SERVER_B_MINIO_KEY_PREFIX", "") or "").strip()

    source_client = _build_minio_client(
        endpoint_url=source_endpoint,
        access_key=source_access_key,
        secret_key=source_secret_key,
        region_name=source_region,
    )
    destination_client = _build_minio_client(
        endpoint_url=destination_endpoint,
        access_key=destination_access_key,
        secret_key=destination_secret_key,
        region_name=destination_region,
    )

    mirrored_files = {}
    mirror_audit = {}
    for file_name in (SALES_FILE_NAME, MEDICINES_FILE_NAME, HEALTHCARES_FILE_NAME):
        file_entry = files.get(file_name)
        if not isinstance(file_entry, dict):
            raise TrainingSnapshotMirrorError(f"Missing required training dataset file metadata: {file_name}")

        source_uri = str(file_entry.get("uri") or "").strip()
        if not source_uri:
            raise TrainingSnapshotMirrorError(f"Missing source URI for training dataset file: {file_name}")

        mirrored_uri, audit_entry = _mirror_single_training_object_with_retry(
            source_client=source_client,
            destination_client=destination_client,
            source_uri=source_uri,
            expected_sha256=str(file_entry.get("sha256") or ""),
            destination_bucket_override=destination_bucket_override,
            destination_prefix=destination_prefix,
        )
        mirrored_files[file_name] = {**file_entry, "source_uri": source_uri, "uri": mirrored_uri}
        mirror_audit[file_name] = audit_entry

    outbreaks_uri = str(((dataset_manifest.get("outbreaks_ground_truth") or {}).get("uri")) or "").strip()
    mirrored_outbreaks_uri = outbreaks_uri
    if outbreaks_uri:
        mirrored_outbreaks_uri, audit_entry = _mirror_single_training_object_with_retry(
            source_client=source_client,
            destination_client=destination_client,
            source_uri=outbreaks_uri,
            expected_sha256="",
            destination_bucket_override=destination_bucket_override,
            destination_prefix=destination_prefix,
        )
        mirror_audit["outbreaks_ground_truth.csv"] = audit_entry

    logger.info(
        "Mirrored training snapshot artifacts for training_job_id=%s dataset_id=%s to endpoint=%s",
        str(training_job.id),
        str(dataset.id),
        destination_endpoint,
    )
    return {
        "files": mirrored_files,
        "outbreaks_file_path": mirrored_outbreaks_uri,
        "audit": {
            "enabled": True,
            "source_endpoint": source_endpoint,
            "destination_endpoint": destination_endpoint,
            "destination_bucket_override": destination_bucket_override,
            "destination_prefix": destination_prefix,
            "items": mirror_audit,
        },
    }


def _schedule_idempotency_key(schedule_id, due_at) -> str:
    return f"ml_schedule:{schedule_id}:{due_at.isoformat()}"


def _write_job_event(job: MLJob, event_type: str, payload: dict | None = None) -> None:
    MLJobEvent.objects.create(job=job, event_type=event_type, payload=payload or {})


def _dispatch_url_for_job(job: MLJob) -> str:
    base_url = str(getattr(settings, "ML_SERVER_B_BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("ML_SERVER_B_BASE_URL is not configured.")

    if job.job_type == MLJob.JobType.FORECAST:
        path = str(getattr(settings, "ML_SERVER_B_FORECAST_PATH", "/api/v1/ml/jobs/forecast"))
    else:
        path = str(getattr(settings, "ML_SERVER_B_OUTBREAK_PATH", "/api/v1/ml/jobs/outbreak"))

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url}{path}"


def _dispatch_url_for_model(model_key: str) -> str:
    base_url = str(getattr(settings, "ML_SERVER_B_BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("ML_SERVER_B_BASE_URL is not configured.")

    if model_key == "model1":
        path = str(getattr(settings, "ML_SERVER_B_MODEL1_PREDICT_PATH", "/api/v1/inference/model1/predict"))
    elif model_key == "model2":
        path = str(getattr(settings, "ML_SERVER_B_MODEL2_PREDICT_PATH", "/api/v1/inference/model2/predict"))
    else:
        raise RuntimeError(f"Unsupported JSON inference model key: {model_key}")

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url}{path}"


def _dispatch_url_for_training(model_type: str) -> str:
    base_url = str(getattr(settings, "ML_SERVER_B_BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("ML_SERVER_B_BASE_URL is not configured.")

    model_key = str(model_type or "").strip().lower()
    if model_key == "model1":
        path = str(
            getattr(settings, "ML_SERVER_B_MODEL1_TRAIN_PATH", "")
            or getattr(settings, "ML_SERVER_B_TRAIN_PATH", "/api/v1/training/model1/train")
        )
    elif model_key == "model2":
        path = str(
            getattr(settings, "ML_SERVER_B_MODEL2_TRAIN_PATH", "")
            or getattr(settings, "ML_SERVER_B_TRAIN_PATH", "/api/v1/training/model2/train")
        )
    else:
        raise RuntimeError(f"Unsupported training model_type: {model_type}")

    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url}{path}"


def _resolve_training_callback_url() -> str:
    training_callback = str(getattr(settings, "ML_SERVER_A_TRAINING_CALLBACK_URL", "") or "").strip()
    if training_callback:
        return training_callback

    generic_callback = str(getattr(settings, "ML_SERVER_A_CALLBACK_URL", "") or "").strip()
    if not generic_callback:
        return ""

    if "/callbacks/server-b/" in generic_callback:
        return generic_callback.replace(
            "/callbacks/server-b/",
            "/training/callbacks/server-b/",
        )
    return generic_callback


def _job_execution_mode(job: MLJob) -> str:
    parameters = job.parameters if isinstance(job.parameters, dict) else {}
    return str(parameters.get("_execution_mode") or "csv_snapshot").strip().lower()


def _server_b_idempotency_key(*, explicit_key: str | None, fallback_id: str) -> str:
    key = str(explicit_key or "").strip()
    if key:
        return key
    return f"ml-dispatch:{fallback_id}"


def queue_ml_job_execution(job_id: str) -> None:
    job = MLJob.objects.only("id", "parameters").filter(id=job_id).first()
    if not job:
        return

    if _job_execution_mode(job) == "json_inference":
        process_ml_job_json_inference_task.delay(str(job.id))
    else:
        process_ml_job_pipeline_task.delay(str(job.id))


def _require_minio_uri(value: str, field_name: str) -> str:
    uri = str(value or "").strip()
    if not uri.startswith("minio://"):
        raise RuntimeError(f"{field_name} must start with minio://")
    return uri


def _require_callback_payload() -> dict:
    callback_url = str(getattr(settings, "ML_SERVER_A_CALLBACK_URL", "") or "").strip()
    if not callback_url:
        raise RuntimeError("ML_SERVER_A_CALLBACK_URL is required for Server B dispatch.")

    timeout_seconds = int(getattr(settings, "ML_SERVER_B_CALLBACK_TIMEOUT_SECONDS", 10))
    if timeout_seconds <= 0:
        raise RuntimeError("ML_SERVER_B_CALLBACK_TIMEOUT_SECONDS must be greater than 0.")

    return {
        "url": callback_url,
        "timeout_seconds": timeout_seconds,
    }


def _build_server_b_payload(job: MLJob, dataset_manifest: dict) -> dict:
    horizon = int(job.parameters.get("prediction_horizon_days", 1) or 1)
    files = dataset_manifest["files"]

    input_payload = {
        "snapshot_id": str(job.id),
        "sales_file_path": _require_minio_uri(files[SALES_FILE_NAME]["uri"], "input.sales_file_path"),
        "facilities_file_path": _require_minio_uri(files[HEALTHCARES_FILE_NAME]["uri"], "input.facilities_file_path"),
    }
    if job.job_type == MLJob.JobType.FORECAST:
        input_payload["medicines_file_path"] = _require_minio_uri(
            files[MEDICINES_FILE_NAME]["uri"],
            "input.medicines_file_path",
        )

    ground_truth_uri = (dataset_manifest.get("outbreaks_ground_truth") or {}).get("uri")
    if ground_truth_uri:
        input_payload["outbreaks_ground_truth_file_path"] = _require_minio_uri(
            ground_truth_uri,
            "input.outbreaks_ground_truth_file_path",
        )

    resolved_model_version = (job.model_version or "").strip()
    if not resolved_model_version:
        model_key = _model_key_for_job_type(job.job_type)
        resolved_model_version = _resolve_server_b_model_version(model_key)

    payload = {
        "job_id": str(job.id),
        "prediction_horizon_days": horizon,
        "input": input_payload,
        "model_version": resolved_model_version,
        "callback": _require_callback_payload(),
    }

    if job.job_type == MLJob.JobType.OUTBREAK:
        payload["max_neighbors"] = int(job.parameters.get("max_neighbors", 20) or 20)

    return payload


def _resolve_server_b_model_version(model_key: str) -> str:
    base_url = str(getattr(settings, "ML_SERVER_B_BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("ML_SERVER_B_BASE_URL is not configured.")

    path_template = str(
        getattr(settings, "ML_SERVER_B_MODEL_VERSIONS_PATH_TEMPLATE", "/api/v1/models/{model_type}/versions") or ""
    ).strip()
    if not path_template:
        raise RuntimeError("ML_SERVER_B_MODEL_VERSIONS_PATH_TEMPLATE is not configured.")

    try:
        path = path_template.format(model_type=model_key)
    except Exception:  # noqa: BLE001
        path = path_template

    if not path.startswith("/"):
        path = f"/{path}"

    url = f"{base_url}{path}"
    try:
        response = requests.get(url, timeout=int(getattr(settings, "ML_SERVER_B_REQUEST_TIMEOUT_SECONDS", 30)))
        response.raise_for_status()
        payload = response.json() if response.content else {}
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Unable to resolve Server B model version for {model_key} from {url}: {exc}")

    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid model versions response for {model_key}: expected object.")

    active_version = str(payload.get("active_version") or "").strip()
    if active_version:
        return active_version
    raise RuntimeError(f"No active model version found for {model_key} at {url}.")


def _normalize_schedule_execution_mode(raw_mode: str | None) -> str:
    normalized = str(raw_mode or "").strip().lower()
    if normalized in {"json", "json_inference", "pretrained"}:
        return "json_inference"
    return "csv_snapshot"


def _schedule_default_execution_mode() -> str:
    return _normalize_schedule_execution_mode(getattr(settings, "ML_SCHEDULE_DEFAULT_EXECUTION_MODE", "csv_snapshot"))


def _model_key_for_job_type(job_type: str) -> str:
    return "model1" if job_type == MLJob.JobType.FORECAST else "model2"


def _schedule_locality_label(facility: Hospital) -> str:
    return str(
        facility.region_level_2
        or facility.city
        or facility.region_level_1
        or facility.state
        or facility.country
        or "UNKNOWN"
    )


def _default_forecast_rows_for_schedule(schedule: MLSchedule) -> list[dict]:
    facility = schedule.facility
    locality = _schedule_locality_label(facility)
    healthcare_id = str(facility.registration_number or facility.id)
    catalogs = ResourceCatalog.objects.filter(hospital=facility).order_by("name").values("id", "name")[:25]

    rows: list[dict] = []
    for catalog in catalogs:
        rows.append(
            {
                "facility_id": str(facility.id),
                "resource_catalog_id": str(catalog["id"]),
                "features": {
                    "healthcare_id": healthcare_id,
                    "medicine_name": str(catalog["name"]),
                    "upazila": locality,
                    "signals_disease": "baseline",
                    "base_daily_sales": 24.0,
                    "outbreak_multiplier": 1.1,
                },
            }
        )
    return rows


def _default_outbreak_rows_for_schedule(schedule: MLSchedule) -> list[dict]:
    facility = schedule.facility
    return [
        {
            "facility_id": str(facility.id),
            "upazila": _schedule_locality_label(facility),
            "features": {
                "recent_avg_sales": 45.0,
                "baseline_avg_sales": 32.0,
                "neighbor_trend_score": 0.18,
                "outbreak_signal": 0.12,
            },
        }
    ]


def _default_schedule_inference_input(schedule: MLSchedule, model_key: str) -> dict:
    if model_key == "model1":
        rows = _default_forecast_rows_for_schedule(schedule)
        if not rows:
            return {}
        return {"rows": rows}

    rows = _default_outbreak_rows_for_schedule(schedule)
    if not rows:
        return {}
    return {"rows": rows, "neighbors": {}}


def _apply_schedule_execution_defaults(schedule: MLSchedule, job_parameters: dict) -> None:
    execution_mode = _normalize_schedule_execution_mode(job_parameters.get("_execution_mode"))
    if "_execution_mode" not in job_parameters:
        execution_mode = _schedule_default_execution_mode()

    job_parameters["_execution_mode"] = execution_mode
    if execution_mode != "json_inference":
        return

    model_key = str(job_parameters.get("_model_key") or "").strip().lower()
    if model_key not in {"model1", "model2"}:
        model_key = _model_key_for_job_type(schedule.job_type)
    job_parameters["_model_key"] = model_key

    inference_input = job_parameters.get("inference_input") if isinstance(job_parameters.get("inference_input"), dict) else {}
    if not inference_input:
        inference_input = _default_schedule_inference_input(schedule, model_key)

    if not inference_input:
        # If no viable JSON input can be generated, fall back to CSV snapshot mode.
        job_parameters["_execution_mode"] = "csv_snapshot"
        job_parameters.pop("_model_key", None)
        job_parameters.pop("inference_input", None)
        job_parameters.pop("inference_context", None)
        return

    job_parameters["inference_input"] = inference_input
    inference_context = (
        job_parameters.get("inference_context") if isinstance(job_parameters.get("inference_context"), dict) else {}
    )
    inference_context.setdefault("source", "schedule")
    inference_context.setdefault("schedule_id", str(schedule.id))
    job_parameters["inference_context"] = inference_context


def _build_json_inference_payload(job: MLJob) -> dict:
    parameters = job.parameters if isinstance(job.parameters, dict) else {}
    model_key = str(parameters.get("_model_key") or "").strip().lower()
    if model_key not in {"model1", "model2"}:
        model_key = "model1" if job.job_type == MLJob.JobType.FORECAST else "model2"

    inference_input = parameters.get("inference_input") if isinstance(parameters.get("inference_input"), dict) else {}
    inference_context = parameters.get("inference_context") if isinstance(parameters.get("inference_context"), dict) else {}

    resolved_model_version = (job.model_version or "").strip()
    if not resolved_model_version:
        resolved_model_version = _resolve_server_b_model_version(model_key)

    payload = {
        "job_id": str(job.id),
        "prediction_horizon_days": int(parameters.get("prediction_horizon_days", 1) or 1),
        "input": inference_input,
        "model_version": resolved_model_version,
        "callback": _require_callback_payload(),
    }

    if model_key == "model2":
        payload["max_neighbors"] = int(parameters.get("max_neighbors", 20) or 20)

    if not isinstance(inference_input.get("rows"), list) or not inference_input.get("rows"):
        raise RuntimeError("JSON inference payload requires input.rows with at least one row.")

    if model_key == "model1":
        for index, row in enumerate(inference_input["rows"]):
            if not isinstance(row, dict):
                raise RuntimeError(f"model1 input.rows[{index}] must be an object.")
            if not row.get("facility_id"):
                raise RuntimeError(f"model1 input.rows[{index}].facility_id is required.")
            if not row.get("resource_catalog_id"):
                raise RuntimeError(f"model1 input.rows[{index}].resource_catalog_id is required.")

    if model_key == "model2":
        for index, row in enumerate(inference_input["rows"]):
            if not isinstance(row, dict):
                raise RuntimeError(f"model2 input.rows[{index}] must be an object.")
            if not row.get("facility_id"):
                raise RuntimeError(f"model2 input.rows[{index}].facility_id is required.")

        neighbors = inference_input.get("neighbors", {})
        if neighbors is not None and not isinstance(neighbors, dict):
            raise RuntimeError("model2 input.neighbors must be an object when provided.")

    return payload


def _mark_job_failed(job_id: str, error_message: str, *, error_code: str = "pipeline_error") -> None:
    with transaction.atomic():
        job = MLJob.objects.select_for_update().filter(id=job_id).first()
        if not job:
            return
        if job.status in TERMINAL_STATUSES:
            return
        job.status = MLJob.Status.FAILED
        job.completed_at = timezone.now()
        job.error_code = error_code
        job.error_message = str(error_message)[:1200]
        job.save(update_fields=["status", "completed_at", "error_code", "error_message", "updated_at"])
        _write_job_event(job, "pipeline_failed", payload={"error": str(error_message)[:1200]})


def _mark_training_job_failed(training_job_id: str, error_message: str, *, error_code: str = "training_dispatch_error") -> None:
    with transaction.atomic():
        job = MLTrainingJob.objects.select_for_update().filter(id=training_job_id).first()
        if not job:
            return
        if job.status in {MLTrainingJob.Status.TRAINED, MLTrainingJob.Status.FAILED, MLTrainingJob.Status.CANCELLED}:
            return
        job.status = MLTrainingJob.Status.FAILED
        job.completed_at = timezone.now()
        job.error_code = error_code
        job.error_message = str(error_message)[:1200]
        job.save(update_fields=["status", "completed_at", "error_code", "error_message", "updated_at"])


@shared_task
def process_ml_job_pipeline_task(job_id: str) -> dict:
    """Generate datasets, upload to MinIO, then dispatch one ML job to Server B."""
    if not bool(getattr(settings, "ML_DATASET_PIPELINE_ENABLED", True)):
        return {"job_id": job_id, "status": "skipped", "reason": "pipeline_disabled"}

    try:
        with transaction.atomic():
            job = MLJob.objects.select_for_update().filter(id=job_id).first()
            if not job:
                return {"job_id": job_id, "status": "skipped", "reason": "not_found"}
            if job.status in TERMINAL_STATUSES:
                return {"job_id": job_id, "status": "skipped", "reason": "terminal"}
            if job.status != MLJob.Status.PENDING:
                return {"job_id": job_id, "status": "skipped", "reason": f"status={job.status}"}
            if not job.facility_id:
                raise RuntimeError("ML job must be facility-scoped for dataset generation.")

            job.status = MLJob.Status.RUNNING
            if not job.started_at:
                job.started_at = timezone.now()
            job.save(update_fields=["status", "started_at", "updated_at"])
            _write_job_event(job, "job_processing_started", payload={"job_type": job.job_type})

        job = MLJob.objects.select_related("facility").get(id=job_id)
        dataset_manifest = generate_and_upload_ml_input_datasets(job)

        with transaction.atomic():
            job = MLJob.objects.select_for_update().get(id=job_id)
            if job.status in TERMINAL_STATUSES:
                return {"job_id": job_id, "status": "skipped", "reason": "terminal_after_upload"}

            job.status = MLJob.Status.SNAPSHOT_READY
            job.save(update_fields=["status", "updated_at"])
            _write_job_event(
                job,
                "snapshot_ready",
                payload={
                    "files": dataset_manifest.get("files", {}),
                    "source_tables": dataset_manifest.get("source_tables", []),
                },
            )

        job = MLJob.objects.select_related("facility").get(id=job_id)
        dispatch_url = _dispatch_url_for_job(job)
        payload = _build_server_b_payload(job, dataset_manifest)

        response = requests.post(
            dispatch_url,
            json=payload,
            headers={
                "Idempotency-Key": _server_b_idempotency_key(
                    explicit_key=job.idempotency_key,
                    fallback_id=str(job.id),
                )
            },
            timeout=int(getattr(settings, "ML_SERVER_B_REQUEST_TIMEOUT_SECONDS", 30)),
        )
        response.raise_for_status()
        response_data = response.json() if response.content else {}

        with transaction.atomic():
            job = MLJob.objects.select_for_update().get(id=job_id)
            if job.status in TERMINAL_STATUSES:
                return {"job_id": job_id, "status": "skipped", "reason": "terminal_after_dispatch"}

            job.status = MLJob.Status.DISPATCHED
            external_job_id = response_data.get("external_job_id") or response_data.get("job_id")
            if external_job_id:
                job.external_job_id = str(external_job_id)
            job.save(update_fields=["status", "external_job_id", "updated_at"])
            _write_job_event(
                job,
                "job_dispatched",
                payload={
                    "dispatch_url": dispatch_url,
                    "response": response_data,
                },
            )

        logger.info("ML job %s dispatched successfully", job_id)
        return {
            "job_id": job_id,
            "status": "dispatched",
            "dispatch_url": dispatch_url,
            "response": response_data,
            "dataset_files": dataset_manifest.get("files", {}),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("ML pipeline processing failed for job %s", job_id)
        _mark_job_failed(job_id, str(exc))
        return {
            "job_id": job_id,
            "status": "failed",
            "error": str(exc),
        }


@shared_task
def process_ml_job_json_inference_task(job_id: str) -> dict:
    """Dispatch JSON-based inference payloads directly to Server B without CSV snapshots."""
    if not bool(getattr(settings, "ML_DATASET_PIPELINE_ENABLED", True)):
        return {"job_id": job_id, "status": "skipped", "reason": "pipeline_disabled"}

    try:
        with transaction.atomic():
            job = MLJob.objects.select_for_update().filter(id=job_id).first()
            if not job:
                return {"job_id": job_id, "status": "skipped", "reason": "not_found"}
            if job.status in TERMINAL_STATUSES:
                return {"job_id": job_id, "status": "skipped", "reason": "terminal"}
            if job.status != MLJob.Status.PENDING:
                return {"job_id": job_id, "status": "skipped", "reason": f"status={job.status}"}
            if job.scheduled_time and job.scheduled_time > timezone.now():
                return {"job_id": job_id, "status": "skipped", "reason": "scheduled_for_future"}

            job.status = MLJob.Status.RUNNING
            if not job.started_at:
                job.started_at = timezone.now()
            job.save(update_fields=["status", "started_at", "updated_at"])
            _write_job_event(job, "job_processing_started", payload={"execution_mode": "json_inference"})

        job = MLJob.objects.select_related("facility").get(id=job_id)
        parameters = job.parameters if isinstance(job.parameters, dict) else {}
        model_key = str(parameters.get("_model_key") or "").strip().lower()
        if model_key not in {"model1", "model2"}:
            model_key = "model1" if job.job_type == MLJob.JobType.FORECAST else "model2"

        dispatch_url = _dispatch_url_for_model(model_key)
        payload = _build_json_inference_payload(job)

        response = requests.post(
            dispatch_url,
            json=payload,
            headers={
                "Idempotency-Key": _server_b_idempotency_key(
                    explicit_key=job.idempotency_key,
                    fallback_id=str(job.id),
                )
            },
            timeout=int(getattr(settings, "ML_SERVER_B_REQUEST_TIMEOUT_SECONDS", 30)),
        )
        response.raise_for_status()
        response_data = response.json() if response.content else {}

        with transaction.atomic():
            job = MLJob.objects.select_for_update().get(id=job_id)
            if job.status in TERMINAL_STATUSES:
                return {"job_id": job_id, "status": "skipped", "reason": "terminal_after_dispatch"}

            job.status = MLJob.Status.DISPATCHED
            external_job_id = response_data.get("external_job_id") or response_data.get("job_id")
            if external_job_id:
                job.external_job_id = str(external_job_id)
            if not job.model_version:
                model_version = str(payload.get("model_version") or "").strip()
                if model_version:
                    job.model_version = model_version
            job.save(update_fields=["status", "external_job_id", "model_version", "updated_at"])
            _write_job_event(
                job,
                "json_job_dispatched",
                payload={
                    "dispatch_url": dispatch_url,
                    "response": response_data,
                    "model": model_key,
                },
            )

        logger.info("ML JSON inference job %s dispatched successfully", job_id)
        return {
            "job_id": job_id,
            "status": "dispatched",
            "dispatch_url": dispatch_url,
            "response": response_data,
            "model": model_key,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("ML JSON inference processing failed for job %s", job_id)
        _mark_job_failed(job_id, str(exc), error_code="json_inference_error")
        return {
            "job_id": job_id,
            "status": "failed",
            "error": str(exc),
        }


@shared_task
def process_ml_training_job_task(training_job_id: str) -> dict:
    """Dispatch ML training jobs to Server B using approved dataset snapshots."""
    mirror_audit_payload = {}
    dataset_id_for_dead_letter = ""
    try:
        with transaction.atomic():
            job = MLTrainingJob.objects.select_for_update().filter(id=training_job_id).first()
            if not job:
                return {"training_job_id": training_job_id, "status": "skipped", "reason": "not_found"}
            if job.status in {MLTrainingJob.Status.TRAINED, MLTrainingJob.Status.FAILED, MLTrainingJob.Status.CANCELLED}:
                return {"training_job_id": training_job_id, "status": "skipped", "reason": "terminal"}
            if job.status not in {MLTrainingJob.Status.PENDING, MLTrainingJob.Status.RUNNING}:
                return {"training_job_id": training_job_id, "status": "skipped", "reason": f"status={job.status}"}

            if not job.started_at:
                job.started_at = timezone.now()
            job.status = MLTrainingJob.Status.RUNNING
            job.save(update_fields=["status", "started_at", "updated_at"])

        job = MLTrainingJob.objects.select_related("dataset_snapshot").get(id=training_job_id)
        dataset = job.dataset_snapshot
        if not dataset:
            raise RuntimeError("Training job is missing dataset snapshot.")
        if dataset.approval_status != MLTrainingDatasetSnapshot.ApprovalStatus.APPROVED:
            raise RuntimeError("Training dataset snapshot must be approved before dispatch.")
        dataset_id_for_dead_letter = str(dataset.id)

        dispatch_url = _dispatch_url_for_training(job.model_type)
        dataset_manifest = dataset.manifest if isinstance(dataset.manifest, dict) else {}
        files = dataset_manifest.get("files") if isinstance(dataset_manifest.get("files"), dict) else {}

        mirrored_inputs = _mirror_training_snapshot_inputs(
            training_job=job,
            dataset=dataset,
            dataset_manifest=dataset_manifest,
            files=files,
        )
        mirror_audit_payload = mirrored_inputs.get("audit") if isinstance(mirrored_inputs.get("audit"), dict) else {}
        files = mirrored_inputs.get("files") if isinstance(mirrored_inputs.get("files"), dict) else files
        mirrored_outbreaks_path = mirrored_inputs.get("outbreaks_file_path")

        callback_url = _resolve_training_callback_url()

        training_params = job.parameters if isinstance(job.parameters, dict) else {}
        requested_version_label = str(training_params.get("requested_version_label") or "").strip()

        snapshot_prefix = str(dataset.snapshot_prefix or "").strip().strip("/")
        dataset_bucket = str(getattr(settings, "ML_DATASET_BUCKET_NAME", "ml-input") or "ml-input").strip() or "ml-input"
        dataset_snapshot_uri = f"minio://{dataset_bucket}/{snapshot_prefix}" if snapshot_prefix else ""
        if not dataset_snapshot_uri:
            sales_uri = str((files.get(SALES_FILE_NAME) or {}).get("uri") or "")
            if sales_uri.startswith("minio://") and "/" in sales_uri[len("minio://") :]:
                dataset_snapshot_uri = sales_uri.rsplit("/", 1)[0]

        training_input = {
            "snapshot_id": str(dataset.id),
            "sales_file_path": ((files.get(SALES_FILE_NAME) or {}).get("uri")),
            "medicines_file_path": ((files.get(MEDICINES_FILE_NAME) or {}).get("uri")),
            "facilities_file_path": ((files.get(HEALTHCARES_FILE_NAME) or {}).get("uri")),
        }
        if mirrored_outbreaks_path:
            training_input["outbreaks_file_path"] = mirrored_outbreaks_path
        if bool(getattr(settings, "ML_SERVER_B_INCLUDE_DATASET_SNAPSHOT_URI", False)) and dataset_snapshot_uri:
            training_input["dataset_snapshot_uri"] = dataset_snapshot_uri

        payload = {
            "job_id": str(job.id),
            "date_range": {
                "start_date": job.date_from.isoformat(),
                "end_date": job.date_to.isoformat(),
            },
            "input": training_input,
            "training_params": training_params,
        }

        if requested_version_label:
            payload["requested_version_label"] = requested_version_label

        if callback_url:
            payload["callback"] = {
                "url": callback_url,
                "timeout_seconds": int(getattr(settings, "ML_SERVER_B_CALLBACK_TIMEOUT_SECONDS", 10)),
            }

        response = requests.post(
            dispatch_url,
            json=payload,
            headers={
                "Idempotency-Key": _server_b_idempotency_key(
                    explicit_key=job.idempotency_key,
                    fallback_id=str(job.id),
                )
            },
            timeout=int(getattr(settings, "ML_SERVER_B_REQUEST_TIMEOUT_SECONDS", 30)),
        )
        response.raise_for_status()
        response_data = response.json() if response.content else {}

        with transaction.atomic():
            job = MLTrainingJob.objects.select_for_update().get(id=training_job_id)
            if job.status in {MLTrainingJob.Status.TRAINED, MLTrainingJob.Status.FAILED, MLTrainingJob.Status.CANCELLED}:
                return {"training_job_id": training_job_id, "status": "skipped", "reason": "terminal_after_dispatch"}

            job.status = MLTrainingJob.Status.DISPATCHED
            external_job_id = response_data.get("external_job_id") or response_data.get("job_id")
            if external_job_id:
                job.external_job_id = str(external_job_id)
            job.save(update_fields=["status", "external_job_id", "updated_at"])

        logger.info("ML training job %s dispatched successfully", training_job_id)
        return {
            "training_job_id": training_job_id,
            "status": "dispatched",
            "dispatch_url": dispatch_url,
            "response": response_data,
            "mirror_audit": mirror_audit_payload,
        }
    except TrainingSnapshotMirrorError as exc:
        logger.exception("ML training snapshot mirror failed for %s", training_job_id)
        _write_training_mirror_dead_letter(
            training_job_id=training_job_id,
            dataset_id=dataset_id_for_dead_letter,
            payload={
                "stage": "snapshot_mirror",
                "error": str(exc),
                "mirror_audit": mirror_audit_payload,
            },
        )
        _mark_training_job_failed(training_job_id, str(exc), error_code="training_snapshot_mirror_failed")
        return {
            "training_job_id": training_job_id,
            "status": "failed",
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("ML training job dispatch failed for %s", training_job_id)
        _mark_training_job_failed(training_job_id, str(exc))
        return {
            "training_job_id": training_job_id,
            "status": "failed",
            "error": str(exc),
        }


@shared_task
def enqueue_due_ml_schedules_task(limit: int = 50) -> dict:
    """Create pending MLJob entries for due active schedules."""
    if not bool(getattr(settings, "ML_DATASET_PIPELINE_ENABLED", True)):
        return {"queued": 0, "evaluated": 0, "updated": 0, "reason": "pipeline_disabled"}

    now = timezone.now()
    grace_minutes = max(0, int(getattr(settings, "ML_SCHEDULE_SAME_DAY_GRACE_MINUTES", 60)))
    recently_updated_cutoff = now - timedelta(minutes=grace_minutes)

    schedule_ids = list(
        MLSchedule.objects.filter(is_active=True)
        .filter(
            Q(next_run_at__isnull=True)
            | Q(next_run_at__lte=now)
            | Q(updated_at__gte=recently_updated_cutoff)
        )
        .order_by("next_run_at", "created_at")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )

    queued = 0
    evaluated = 0
    updated = 0
    skipped = 0
    queued_job_ids: list[str] = []

    for schedule_id in schedule_ids:
        with transaction.atomic():
            schedule = (
                MLSchedule.objects.select_for_update()
                .select_related("facility")
                .filter(id=schedule_id, is_active=True)
                .first()
            )
            if not schedule:
                continue

            evaluated += 1
            if schedule.facility.verified_status == Hospital.VerifiedStatus.OFFBOARDED:
                skipped += 1
                continue

            iteration_now = timezone.now()

            evaluated_schedule = evaluate_schedule(
                schedule,
                now_utc=iteration_now,
                allow_initial_catchup=True,
                prefer_facility_timezone_for_utc=True,
            )
            due_at = evaluated_schedule.due_at
            update_fields = []

            grace_due_at = compute_next_run_at(schedule, now_utc=iteration_now)
            if due_at is None and grace_due_at is not None and grace_due_at <= iteration_now:
                due_at = grace_due_at
                if schedule.next_run_at != grace_due_at:
                    schedule.next_run_at = grace_due_at
                    update_fields.append("next_run_at")

            if evaluated_schedule.next_run_at != schedule.next_run_at:
                schedule.next_run_at = evaluated_schedule.next_run_at
                update_fields.append("next_run_at")

            if (
                evaluated_schedule.timezone_source == "facility_fallback"
                and schedule.last_run_at is None
                and schedule.timezone != evaluated_schedule.timezone_name
            ):
                schedule.timezone = evaluated_schedule.timezone_name
                update_fields.append("timezone")

            if due_at and schedule.last_run_at and schedule.last_run_at >= due_at:
                due_at = None

            if due_at is not None:
                idempotency_key = _schedule_idempotency_key(schedule.id, due_at)
                existing_job = MLJob.objects.filter(idempotency_key=idempotency_key).first()
                if existing_job is None:
                    local_tz, timezone_name, _ = resolve_timezone_for_facility(
                        schedule.timezone,
                        schedule.facility,
                        prefer_facility_for_utc=True,
                    )

                    job_parameters = dict(schedule.parameters or {})
                    if "prediction_horizon_days" not in job_parameters:
                        job_parameters["prediction_horizon_days"] = 1
                    if schedule.job_type == MLJob.JobType.OUTBREAK and "max_neighbors" not in job_parameters:
                        job_parameters["max_neighbors"] = 20
                    _apply_schedule_execution_defaults(schedule, job_parameters)
                    job_parameters["_schedule_meta"] = {
                        "schedule_id": str(schedule.id),
                        "run_time_local": schedule.run_time.isoformat() if schedule.run_time else None,
                        "scheduled_time_utc": due_at.isoformat(),
                        "scheduled_time_local": due_at.astimezone(local_tz).isoformat(),
                        "timezone": timezone_name,
                    }

                    job = MLJob.objects.create(
                        job_type=schedule.job_type,
                        scope_type=MLJob.ScopeType.FACILITY,
                        facility=schedule.facility,
                        status=MLJob.Status.PENDING,
                        scheduled_time=due_at,
                        model_version=str((schedule.parameters or {}).get("model_version", "") or ""),
                        parameters=job_parameters,
                        created_by=schedule.created_by,
                        idempotency_key=idempotency_key,
                    )
                    _write_job_event(
                        job,
                        "job_created_from_schedule",
                        payload={
                            "schedule_id": str(schedule.id),
                            "due_at": due_at.isoformat(),
                        },
                    )
                    queued += 1
                    queued_job_ids.append(str(job.id))

                schedule.last_run_at = due_at
                update_fields.append("last_run_at")
                schedule.next_run_at = evaluate_schedule(
                    schedule,
                    now_utc=iteration_now,
                    allow_initial_catchup=False,
                    prefer_facility_timezone_for_utc=True,
                ).next_run_at
                if "next_run_at" not in update_fields:
                    update_fields.append("next_run_at")

            if update_fields:
                schedule.save(update_fields=[*dict.fromkeys(update_fields), "updated_at"])
                updated += 1

    if queued:
        logger.info("Queued %s ML jobs from due schedules", queued)

    dispatched_immediately = 0
    if queued_job_ids and bool(getattr(settings, "ML_AUTO_DISPATCH_ON_JOB_CREATE", False)):
        for job_id in queued_job_ids:
            queue_ml_job_execution(job_id)
        dispatched_immediately = len(queued_job_ids)

    return {
        "queued": queued,
        "evaluated": evaluated,
        "updated": updated,
        "skipped": skipped,
        "dispatched_immediately": dispatched_immediately,
    }


@shared_task
def dispatch_pending_ml_jobs_task(limit: int = 25, include_schedule_sweep: bool = True) -> dict:
    """Periodic safety-net dispatcher for pending ML jobs.

    By default this task also performs a schedule sweep first so jobs are not
    blocked if the dedicated enqueue task is delayed.
    """
    if not bool(getattr(settings, "ML_DATASET_PIPELINE_ENABLED", True)):
        return {"dispatched": 0, "reason": "pipeline_disabled"}

    queued_from_schedules = 0
    if include_schedule_sweep:
        schedule_sweep = enqueue_due_ml_schedules_task(limit=max(50, int(limit)))
        queued_from_schedules = int(schedule_sweep.get("queued", 0) or 0)

    now = timezone.now()
    pending_ids = [
        str(pk)
        for pk in MLJob.objects.filter(status=MLJob.Status.PENDING, scheduled_time__lte=now)
        .order_by("scheduled_time", "created_at")
        .values_list("id", flat=True)[: max(1, int(limit))]
    ]

    for job_id in pending_ids:
        queue_ml_job_execution(job_id)

    if pending_ids:
        logger.info("Queued %s pending ML jobs for processing", len(pending_ids))
    return {
        "dispatched": len(pending_ids),
        "queued_from_schedules": queued_from_schedules,
    }
