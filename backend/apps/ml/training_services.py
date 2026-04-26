"""Service layer for ML engineer/admin dataset and training lifecycle workflows."""

from __future__ import annotations

import json
import uuid

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from common.permissions.runtime import has_any_permission

from .dataset_pipeline import generate_and_upload_training_datasets
from .models import (
    MLActiveModelConfig,
    MLJobIdempotency,
    MLModelType,
    MLModelVersion,
    MLTrainingDatasetSnapshot,
    MLTrainingJob,
)
from .services import _ensure_callback_headers, _idempotent_fetch_or_conflict, _payload_hash

ML_TRAINING_ROLE_NAMES = ("ML_ENGINEER", "ML_ADMIN")
ML_MODEL_ADMIN_ROLE_NAMES = ("ML_ADMIN",)
ML_TRAINING_PERMISSION_CODES = (
    "ml:training.manage",
    "ml:dataset.review",
    "ml:model_version.manage",
)


def _ensure_training_access(user) -> None:
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication required.")

    allowed = has_any_permission(
        user,
        ML_TRAINING_PERMISSION_CODES,
        allow_role_fallback=True,
        legacy_roles=ML_TRAINING_ROLE_NAMES,
    )
    if not allowed:
        raise PermissionDenied("ML training workflow access is restricted to ML roles.")


def _ensure_model_admin_access(user) -> None:
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication required.")

    allowed = has_any_permission(
        user,
        ("ml:model_version.manage", "ml:model_version.activate"),
        allow_role_fallback=True,
        legacy_roles=ML_MODEL_ADMIN_ROLE_NAMES,
    )
    if not allowed:
        raise PermissionDenied("Model activation is restricted to ML admin roles.")


def _serialize_dataset(snapshot: MLTrainingDatasetSnapshot) -> dict:
    return {
        "dataset_id": str(snapshot.id),
        "model_type": snapshot.model_type,
        "date_from": snapshot.date_from.isoformat(),
        "date_to": snapshot.date_to.isoformat(),
        "row_count": snapshot.row_count,
        "schema_version": snapshot.schema_version,
        "snapshot_prefix": snapshot.snapshot_prefix,
        "approval_status": snapshot.approval_status,
        "review_notes": snapshot.review_notes,
        "reviewed_by": str(snapshot.reviewed_by_id) if snapshot.reviewed_by_id else None,
        "reviewed_at": snapshot.reviewed_at.isoformat() if snapshot.reviewed_at else None,
        "created_timestamp": snapshot.created_at.isoformat(),
        "parameters": snapshot.parameters,
        "manifest": snapshot.manifest,
    }


def _serialize_training_job(job: MLTrainingJob) -> dict:
    return {
        "id": str(job.id),
        "model_type": job.model_type,
        "dataset_id": str(job.dataset_snapshot_id) if job.dataset_snapshot_id else None,
        "date_from": job.date_from.isoformat(),
        "date_to": job.date_to.isoformat(),
        "status": job.status,
        "external_job_id": job.external_job_id,
        "model_version_name": job.model_version_name,
        "artifact_uri": job.artifact_uri,
        "metrics": job.metrics,
        "error": {"code": job.error_code, "message": job.error_message} if job.error_code or job.error_message else None,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _serialize_model_version(version: MLModelVersion) -> dict:
    return {
        "id": str(version.id),
        "model_type": version.model_type,
        "version_name": version.version_name,
        "status": version.status,
        "is_active": version.is_active,
        "training_job_id": str(version.training_job_id) if version.training_job_id else None,
        "artifact_uri": version.artifact_uri,
        "metadata": version.metadata,
        "metrics": version.metrics,
        "reviewed_by": str(version.reviewed_by_id) if version.reviewed_by_id else None,
        "reviewed_at": version.reviewed_at.isoformat() if version.reviewed_at else None,
        "approved_by": str(version.approved_by_id) if version.approved_by_id else None,
        "approved_at": version.approved_at.isoformat() if version.approved_at else None,
        "activated_by": str(version.activated_by_id) if version.activated_by_id else None,
        "activated_at": version.activated_at.isoformat() if version.activated_at else None,
        "deactivated_at": version.deactivated_at.isoformat() if version.deactivated_at else None,
        "created_at": version.created_at.isoformat(),
        "updated_at": version.updated_at.isoformat(),
    }


def _json_safe_dict(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return json.loads(json.dumps(value, default=str))


def _dataset_or_404(dataset_id) -> MLTrainingDatasetSnapshot:
    try:
        return MLTrainingDatasetSnapshot.objects.get(id=dataset_id)
    except MLTrainingDatasetSnapshot.DoesNotExist:
        raise NotFound("Training dataset snapshot not found.")


def _training_job_or_404(training_job_id) -> MLTrainingJob:
    try:
        return MLTrainingJob.objects.select_related("dataset_snapshot").get(id=training_job_id)
    except MLTrainingJob.DoesNotExist:
        raise NotFound("Training job not found.")


def _model_version_or_404(version_id) -> MLModelVersion:
    try:
        return MLModelVersion.objects.select_related("training_job").get(id=version_id)
    except MLModelVersion.DoesNotExist:
        raise NotFound("Model version not found.")


def create_training_dataset_snapshot(actor, validated_data: dict) -> dict:
    _ensure_training_access(actor)

    model_type = validated_data["model_type"]
    date_from = validated_data["date_from"]
    date_to = validated_data["date_to"]
    if date_to < date_from:
        raise ValidationError({"date_to": "date_to cannot be before date_from."})

    snapshot = MLTrainingDatasetSnapshot.objects.create(
        model_type=model_type,
        date_from=date_from,
        date_to=date_to,
        schema_version=validated_data.get("schema_version") or "v1",
        parameters=validated_data.get("parameters") or {},
        created_by=actor,
    )

    try:
        manifest = generate_and_upload_training_datasets(
            snapshot_id=str(snapshot.id),
            model_type=model_type,
            date_from=date_from,
            date_to=date_to,
            schema_version=snapshot.schema_version,
            parameters=snapshot.parameters,
        )
    except Exception:
        snapshot.delete()
        raise

    snapshot.manifest = manifest
    snapshot.snapshot_prefix = manifest.get("snapshot_prefix", "")
    snapshot.row_count = int(manifest.get("row_count") or 0)
    snapshot.save(update_fields=["manifest", "snapshot_prefix", "row_count", "updated_at"])

    return {"dataset": _serialize_dataset(snapshot)}


def list_training_dataset_snapshots(actor, query_params) -> list[dict]:
    _ensure_training_access(actor)

    queryset = MLTrainingDatasetSnapshot.objects.select_related("created_by", "reviewed_by").order_by("-created_at")
    model_type = query_params.get("model_type")
    approval_status = query_params.get("approval_status")

    if model_type in {MLModelType.MODEL1, MLModelType.MODEL2}:
        queryset = queryset.filter(model_type=model_type)
    if approval_status in {
        MLTrainingDatasetSnapshot.ApprovalStatus.PENDING,
        MLTrainingDatasetSnapshot.ApprovalStatus.APPROVED,
        MLTrainingDatasetSnapshot.ApprovalStatus.REJECTED,
    }:
        queryset = queryset.filter(approval_status=approval_status)

    return [_serialize_dataset(item) for item in queryset]


def get_training_dataset_snapshot(actor, dataset_id) -> dict:
    _ensure_training_access(actor)
    snapshot = _dataset_or_404(dataset_id)
    return {"dataset": _serialize_dataset(snapshot)}


def review_training_dataset_snapshot(actor, dataset_id, *, approve: bool, notes: str = "") -> dict:
    _ensure_training_access(actor)
    snapshot = _dataset_or_404(dataset_id)

    snapshot.approval_status = (
        MLTrainingDatasetSnapshot.ApprovalStatus.APPROVED
        if approve
        else MLTrainingDatasetSnapshot.ApprovalStatus.REJECTED
    )
    snapshot.review_notes = notes or ""
    snapshot.reviewed_by = actor
    snapshot.reviewed_at = timezone.now()
    snapshot.save(update_fields=["approval_status", "review_notes", "reviewed_by", "reviewed_at", "updated_at"])
    return {"dataset": _serialize_dataset(snapshot)}


def create_training_job(actor, validated_data: dict, idempotency_key: str) -> dict:
    _ensure_training_access(actor)

    if not idempotency_key:
        raise ValidationError({"detail": "Idempotency-Key header is required."})

    dataset = None
    dataset_id = validated_data.get("dataset_id")
    if dataset_id:
        dataset = _dataset_or_404(dataset_id)
        if dataset.approval_status != MLTrainingDatasetSnapshot.ApprovalStatus.APPROVED:
            raise ValidationError({"dataset_id": "Dataset must be approved before training."})

    model_type = validated_data.get("model_type") or (dataset.model_type if dataset else "")
    date_from = validated_data.get("date_from") or (dataset.date_from if dataset else None)
    date_to = validated_data.get("date_to") or (dataset.date_to if dataset else None)

    if model_type not in {MLModelType.MODEL1, MLModelType.MODEL2}:
        raise ValidationError({"model_type": "model_type must be model1 or model2."})
    if not date_from or not date_to:
        raise ValidationError({"date_range": "date_from and date_to are required."})
    if date_to < date_from:
        raise ValidationError({"date_to": "date_to cannot be before date_from."})

    payload = {
        "model_type": model_type,
        "dataset_id": str(dataset.id) if dataset else "",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "parameters": validated_data.get("parameters") or {},
    }
    payload_hash = _payload_hash(payload)
    endpoint = "ml_training_job_create"

    existing = _idempotent_fetch_or_conflict(actor, endpoint, idempotency_key, payload_hash)
    if existing:
        return existing

    with transaction.atomic():
        job = MLTrainingJob.objects.create(
            model_type=model_type,
            dataset_snapshot=dataset,
            date_from=date_from,
            date_to=date_to,
            status=MLTrainingJob.Status.PENDING,
            parameters=validated_data.get("parameters") or {},
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            created_by=actor,
        )

        response_snapshot = {
            "training_job": {
                "id": str(job.id),
                "status": job.status,
                "model_type": job.model_type,
                "dataset_id": str(job.dataset_snapshot_id) if job.dataset_snapshot_id else None,
                "created_at": job.created_at.isoformat(),
            }
        }

        MLJobIdempotency.objects.create(
            actor=actor,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            response_snapshot=response_snapshot,
        )

        queued_job_id = str(job.id)

        def _queue_training() -> None:
            from .tasks import process_ml_training_job_task  # noqa: PLC0415

            process_ml_training_job_task.delay(queued_job_id)

        transaction.on_commit(_queue_training)

    return response_snapshot


def list_training_jobs(actor, query_params) -> list[dict]:
    _ensure_training_access(actor)

    queryset = MLTrainingJob.objects.select_related("dataset_snapshot", "created_by").order_by("-created_at")
    model_type = query_params.get("model_type")
    status_value = query_params.get("status")

    if model_type in {MLModelType.MODEL1, MLModelType.MODEL2}:
        queryset = queryset.filter(model_type=model_type)
    if status_value in {
        MLTrainingJob.Status.PENDING,
        MLTrainingJob.Status.DISPATCHED,
        MLTrainingJob.Status.RUNNING,
        MLTrainingJob.Status.TRAINED,
        MLTrainingJob.Status.FAILED,
        MLTrainingJob.Status.CANCELLED,
    }:
        queryset = queryset.filter(status=status_value)

    return [_serialize_training_job(job) for job in queryset]


def get_training_job(actor, training_job_id) -> dict:
    _ensure_training_access(actor)
    job = _training_job_or_404(training_job_id)
    return {"training_job": _serialize_training_job(job)}


def process_training_callback(payload: dict, headers: dict, signature_payload: dict | None = None) -> dict:
    request_id = _ensure_callback_headers(
        payload,
        headers,
        signature_payload=signature_payload,
        allow_legacy_hmac=True,
    )

    with transaction.atomic():
        job = _training_job_or_404(payload["training_job_id"])
        status_value = str(payload.get("status") or "").strip().lower()

        job.external_job_id = str(payload.get("external_job_id") or job.external_job_id)
        job.started_at = job.started_at or timezone.now()

        if status_value == "failed":
            error = payload.get("error") or {}
            existing_metrics = job.metrics if isinstance(job.metrics, dict) else {}
            job.status = MLTrainingJob.Status.FAILED
            job.error_code = str(error.get("code") or "training_failed")
            job.error_message = str(error.get("message") or "Training failed.")
            job.metrics = {**_json_safe_dict(existing_metrics), "_callback_payload": _json_safe_dict(payload)}
            job.completed_at = timezone.now()
            job.save(
                update_fields=[
                    "status",
                    "external_job_id",
                    "started_at",
                    "metrics",
                    "error_code",
                    "error_message",
                    "completed_at",
                    "updated_at",
                ]
            )
            return {"training_job_id": str(job.id), "accepted": True, "request_id": request_id}

        if status_value not in {"trained", "completed"}:
            raise ValidationError({"status": "status must be one of trained, completed, or failed."})

        version_name = str(payload.get("version_name") or payload.get("model_version") or "").strip()
        if not version_name:
            suffix = timezone.now().strftime("%Y%m%d%H%M%S")
            version_name = f"{job.model_type}-{suffix}"

        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        approval_status = str(payload.get("approval_status") or "").strip()
        if approval_status:
            metadata = {**metadata, "approval_status": approval_status}
        metadata = _json_safe_dict(metadata)
        metrics = _json_safe_dict(payload.get("metrics"))
        persisted_metrics = {**metrics, "_callback_payload": _json_safe_dict(payload)}
        artifact_uri = str(payload.get("artifact_uri") or "").strip()

        version, _ = MLModelVersion.objects.update_or_create(
            model_type=job.model_type,
            version_name=version_name,
            defaults={
                "status": MLModelVersion.Status.STORED,
                "is_active": False,
                "training_job": job,
                "artifact_uri": artifact_uri,
                "metadata": metadata,
                "metrics": metrics,
            },
        )

        job.status = MLTrainingJob.Status.TRAINED
        job.model_version_name = version.version_name
        job.artifact_uri = artifact_uri
        job.metrics = persisted_metrics
        job.error_code = ""
        job.error_message = ""
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "external_job_id",
                "started_at",
                "model_version_name",
                "artifact_uri",
                "metrics",
                "error_code",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )

    return {
        "training_job_id": str(job.id),
        "accepted": True,
        "request_id": request_id,
        "model_version_id": str(version.id),
    }


def list_model_versions(actor, query_params) -> list[dict]:
    _ensure_training_access(actor)

    queryset = MLModelVersion.objects.select_related("training_job").order_by("-created_at")
    model_type = query_params.get("model_type")
    status_value = query_params.get("status")

    if model_type in {MLModelType.MODEL1, MLModelType.MODEL2}:
        queryset = queryset.filter(model_type=model_type)
    if status_value in {
        MLModelVersion.Status.TRAINED,
        MLModelVersion.Status.STORED,
        MLModelVersion.Status.REVIEWED,
        MLModelVersion.Status.APPROVED,
        MLModelVersion.Status.ACTIVE,
        MLModelVersion.Status.INACTIVE,
    }:
        queryset = queryset.filter(status=status_value)

    return [_serialize_model_version(item) for item in queryset]


def get_model_version(actor, version_id) -> dict:
    _ensure_training_access(actor)
    version = _model_version_or_404(version_id)
    return {"model_version": _serialize_model_version(version)}


def mark_model_version_reviewed(actor, version_id, notes: str = "") -> dict:
    _ensure_training_access(actor)
    version = _model_version_or_404(version_id)

    if version.status not in {
        MLModelVersion.Status.STORED,
        MLModelVersion.Status.REVIEWED,
        MLModelVersion.Status.APPROVED,
        MLModelVersion.Status.ACTIVE,
        MLModelVersion.Status.INACTIVE,
    }:
        raise ValidationError({"detail": "Version is not reviewable."})

    metadata = dict(version.metadata or {})
    if notes:
        metadata["review_notes"] = notes

    version.status = MLModelVersion.Status.REVIEWED
    version.metadata = metadata
    version.reviewed_by = actor
    version.reviewed_at = timezone.now()
    version.save(update_fields=["status", "metadata", "reviewed_by", "reviewed_at", "updated_at"])
    return {"model_version": _serialize_model_version(version)}


def mark_model_version_approved(actor, version_id, notes: str = "") -> dict:
    _ensure_model_admin_access(actor)
    version = _model_version_or_404(version_id)

    if version.status not in {
        MLModelVersion.Status.REVIEWED,
        MLModelVersion.Status.APPROVED,
        MLModelVersion.Status.ACTIVE,
    }:
        raise ValidationError({"detail": "Version must be reviewed before approval."})

    metadata = dict(version.metadata or {})
    if notes:
        metadata["approval_notes"] = notes

    version.status = MLModelVersion.Status.APPROVED
    version.metadata = metadata
    version.approved_by = actor
    version.approved_at = timezone.now()
    version.save(update_fields=["status", "metadata", "approved_by", "approved_at", "updated_at"])
    return {"model_version": _serialize_model_version(version)}


def activate_model_version(actor, version_id) -> dict:
    _ensure_model_admin_access(actor)

    with transaction.atomic():
        version = _model_version_or_404(version_id)
        if version.status not in {
            MLModelVersion.Status.APPROVED,
            MLModelVersion.Status.ACTIVE,
            MLModelVersion.Status.INACTIVE,
        }:
            raise ValidationError({"detail": "Version must be approved before activation."})

        existing_active_qs = MLModelVersion.objects.select_for_update().filter(
            model_type=version.model_type,
            is_active=True,
        )
        for current in existing_active_qs:
            if current.id == version.id:
                continue
            current.is_active = False
            current.status = MLModelVersion.Status.INACTIVE
            current.deactivated_at = timezone.now()
            current.save(update_fields=["is_active", "status", "deactivated_at", "updated_at"])

        version.is_active = True
        version.status = MLModelVersion.Status.ACTIVE
        version.activated_by = actor
        version.activated_at = timezone.now()
        version.deactivated_at = None
        version.save(
            update_fields=[
                "is_active",
                "status",
                "activated_by",
                "activated_at",
                "deactivated_at",
                "updated_at",
            ]
        )

        MLActiveModelConfig.objects.update_or_create(
            model_type=version.model_type,
            defaults={
                "active_version": version,
                "updated_by": actor,
            },
        )

    return {"model_version": _serialize_model_version(version)}


def deactivate_model_version(actor, version_id) -> dict:
    _ensure_model_admin_access(actor)

    with transaction.atomic():
        version = _model_version_or_404(version_id)
        version.is_active = False
        version.status = MLModelVersion.Status.INACTIVE
        version.deactivated_at = timezone.now()
        version.save(update_fields=["is_active", "status", "deactivated_at", "updated_at"])

        MLActiveModelConfig.objects.filter(
            model_type=version.model_type,
            active_version_id=version.id,
        ).update(active_version=None, updated_by=actor, updated_at=timezone.now())

    return {"model_version": _serialize_model_version(version)}


def rollback_active_model_version(actor, version_id, target_version_id) -> dict:
    _ensure_model_admin_access(actor)

    if not target_version_id:
        raise ValidationError({"target_version_id": "target_version_id is required for rollback."})

    current = _model_version_or_404(version_id)
    target = _model_version_or_404(target_version_id)
    if str(current.model_type) != str(target.model_type):
        raise ValidationError({"target_version_id": "Rollback target must belong to the same model type."})

    return activate_model_version(actor, target.id)


def list_active_model_configs(actor) -> list[dict]:
    _ensure_training_access(actor)

    configs = MLActiveModelConfig.objects.select_related("active_version").order_by("model_type")
    known = {MLModelType.MODEL1, MLModelType.MODEL2}
    seen = set()
    items = []

    for config in configs:
        seen.add(config.model_type)
        items.append(
            {
                "model_type": config.model_type,
                "active_version_id": str(config.active_version_id) if config.active_version_id else None,
                "active_version_name": config.active_version.version_name if config.active_version_id else None,
                "updated_at": config.updated_at.isoformat() if config.updated_at else None,
            }
        )

    for model_type in sorted(known - seen):
        items.append(
            {
                "model_type": model_type,
                "active_version_id": None,
                "active_version_name": None,
                "updated_at": None,
            }
        )

    return items


def get_active_model_version_name(model_type: str) -> str:
    config = MLActiveModelConfig.objects.select_related("active_version").filter(model_type=model_type).first()
    if not config or not config.active_version_id:
        return ""
    return config.active_version.version_name
