"""Service helpers for JSON-based model inference job creation."""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.hospitals.models import Hospital

from .models import MLJob, MLJobIdempotency
from .services import (
    _ensure_active_job_limit,
    _idempotent_fetch_or_conflict,
    _job_api_status,
    _normalize_job_payload,
    _payload_hash,
    _user_hospital,
    _user_is_super_admin,
    _write_job_event,
)

MODEL_TO_JOB_TYPE = {
    "model1": MLJob.JobType.FORECAST,
    "model2": MLJob.JobType.OUTBREAK,
}


def _resolve_inference_facility(actor, facility_id):
    if facility_id:
        try:
            facility = Hospital.objects.get(id=facility_id)
        except Hospital.DoesNotExist:
            raise ValidationError({"facility_id": "Facility not found."})

        if facility.verified_status == Hospital.VerifiedStatus.OFFBOARDED:
            raise ValidationError({"detail": "offboarded facility cannot be used for ML jobs."})

        if not _user_is_super_admin(actor):
            actor_hospital = _user_hospital(actor)
            if not actor_hospital or str(actor_hospital.id) != str(facility.id):
                raise PermissionDenied("Hospital admins can only create jobs for their own facility.")
        return facility

    if _user_is_super_admin(actor):
        raise ValidationError({"facility_id": "facility_id is required for platform scoped users."})

    actor_hospital = _user_hospital(actor)
    if not actor_hospital:
        raise ValidationError({"facility_id": "facility_id is required."})
    if actor_hospital.verified_status == Hospital.VerifiedStatus.OFFBOARDED:
        raise ValidationError({"detail": "offboarded facility cannot be used for ML jobs."})
    return actor_hospital


def create_json_inference_job(actor, *, model_key: str, validated_data: dict, idempotency_key: str) -> dict:
    if model_key not in MODEL_TO_JOB_TYPE:
        raise ValidationError({"model": "Unsupported model key."})
    if not idempotency_key:
        raise ValidationError({"detail": "Idempotency-Key header is required."})

    job_type = MODEL_TO_JOB_TYPE[model_key]
    facility = _resolve_inference_facility(actor, validated_data.get("facility_id"))
    _ensure_active_job_limit(facility, job_type)

    job_parameters = {
        "prediction_horizon_days": int(validated_data.get("prediction_horizon_days") or 1),
        "_execution_mode": "json_inference",
        "_model_key": model_key,
        "inference_input": validated_data.get("input") or {},
    }
    if model_key == "model2":
        job_parameters["max_neighbors"] = int(validated_data.get("max_neighbors") or 20)

    normalized_payload = _normalize_job_payload(
        {
            "model_key": model_key,
            "facility_id": str(facility.id),
            "job_type": job_type,
            "scheduled_time": validated_data.get("scheduled_time") or timezone.now(),
            "model_version": validated_data.get("model_version", ""),
            "parameters": job_parameters,
        }
    )
    payload_hash = _payload_hash(normalized_payload)
    endpoint = f"ml_{model_key}_predict"

    existing = _idempotent_fetch_or_conflict(actor, endpoint, idempotency_key, payload_hash)
    if existing:
        return existing

    scheduled_time = validated_data.get("scheduled_time") or timezone.now()

    with transaction.atomic():
        job = MLJob.objects.create(
            job_type=job_type,
            scope_type=MLJob.ScopeType.FACILITY,
            facility=facility,
            status=MLJob.Status.PENDING,
            scheduled_time=scheduled_time,
            model_version=validated_data.get("model_version", ""),
            parameters=job_parameters,
            created_by=actor,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        _write_job_event(
            job,
            "json_inference_job_created",
            payload={"model_key": model_key, "facility_id": str(job.facility_id)},
        )

        result_suffix = "forecast" if job_type == MLJob.JobType.FORECAST else "outbreak"
        response_snapshot = {
            "job": {
                "id": str(job.id),
                "model": model_key,
                "job_type": job.job_type,
                "scope_type": job.scope_type,
                "facility_id": str(job.facility_id) if job.facility_id else None,
                "status": _job_api_status(job),
                "scheduled_time": job.scheduled_time.isoformat(),
                "created_at": job.created_at.isoformat(),
                "result_path": f"/api/v1/ml/jobs/{job.id}/results/{result_suffix}/",
            }
        }

        MLJobIdempotency.objects.create(
            actor=actor,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            response_snapshot=response_snapshot,
        )

        if (
            bool(getattr(settings, "ML_INFERENCE_AUTO_DISPATCH_ON_CREATE", True))
            and scheduled_time <= timezone.now()
        ):
            queued_job_id = str(job.id)

            def _queue_json_inference() -> None:
                from .tasks import process_ml_job_json_inference_task  # noqa: PLC0415

                process_ml_job_json_inference_task.delay(queued_job_id)

            transaction.on_commit(_queue_json_inference)

    return response_snapshot
