"""Service layer for ML orchestration APIs."""
import base64
import hashlib
import hmac
import json
import logging
import math
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, NotFound, PermissionDenied, ValidationError

from apps.hospitals.models import Hospital
from apps.resources.models import ResourceCatalog
from common.permissions.runtime import has_any_permission

from .models import (
    FacilityMLSetting,
    MLCallbackDedup,
    MLForecastResult,
    MLJob,
    MLJobEvent,
    MLJobIdempotency,
    MLOutbreakNeighborCandidate,
    MLOutbreakResult,
    MLResultRowError,
    MLSchedule,
)
from .scheduling import compute_next_run_at, normalize_schedule_timezone, resolve_timezone_for_facility

logger = logging.getLogger("hrsp.ml")


WEEKDAY_NAME_TO_INT = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


PROCESSING_API_STATUSES = {
    MLJob.Status.SNAPSHOT_READY,
    MLJob.Status.DISPATCHED,
    MLJob.Status.RUNNING,
    MLJob.Status.CALLBACK_RECEIVED,
}
COMPLETED_API_STATUSES = {MLJob.Status.COMPLETED, MLJob.Status.PARTIAL_COMPLETED}
FAILED_API_STATUSES = {MLJob.Status.FAILED, MLJob.Status.CANCELLED}
TERMINAL_JOB_STATUSES = COMPLETED_API_STATUSES | FAILED_API_STATUSES


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _json_safe_payload(payload) -> dict:
    if not isinstance(payload, dict):
        return {}
    return json.loads(json.dumps(payload, default=str))


def _user_is_super_admin(user) -> bool:
    return has_any_permission(
        user,
        (
            "ml:job.manage",
            "ml:schedule.manage",
            "ml:facility.settings.manage",
        ),
        allow_role_fallback=True,
        legacy_roles=("ML_ADMIN", "ML_ENGINEER"),
    )


def _user_hospital(user):
    if not user or not user.is_authenticated:
        return None
    return getattr(getattr(user, "staff", None), "hospital", None)


def _job_api_status(job: MLJob) -> str:
    if job.status in PROCESSING_API_STATUSES:
        return "processing"
    if job.status in COMPLETED_API_STATUSES:
        return "completed"
    if job.status in FAILED_API_STATUSES:
        return "failed"
    return "pending"


def _write_job_event(job: MLJob, event_type: str, payload=None):
    return MLJobEvent.objects.create(
        job=job,
        event_type=event_type,
        payload=_json_safe_payload(payload),
    )


def _trigger_schedule_processing_on_commit(next_run_at: datetime | None = None) -> None:
    if not bool(getattr(settings, "ML_DATASET_PIPELINE_ENABLED", True)):
        return

    target_run_at = next_run_at

    def _queue_processing() -> None:
        from .tasks import dispatch_pending_ml_jobs_task  # noqa: PLC0415

        dispatch_pending_ml_jobs_task.delay()

        # Fallback one-off trigger so newly updated schedules still execute on
        # time if periodic beat cadence is delayed.
        if target_run_at and target_run_at > timezone.now() and not bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)):
            dispatch_pending_ml_jobs_task.apply_async(
                kwargs={"include_schedule_sweep": True},
                eta=target_run_at,
            )

    transaction.on_commit(_queue_processing)


def _weekly_anchor_weekday(schedule: MLSchedule, local_tz) -> int:
    parameters = schedule.parameters if isinstance(schedule.parameters, dict) else {}
    raw_weekday = parameters.get("weekday")

    if isinstance(raw_weekday, int) and 0 <= raw_weekday <= 6:
        return raw_weekday

    if isinstance(raw_weekday, str):
        mapped = WEEKDAY_NAME_TO_INT.get(raw_weekday.strip().lower())
        if mapped is not None:
            return mapped

    created_local = (schedule.created_at or timezone.now()).astimezone(local_tz)
    return created_local.weekday()


def _same_day_due_slot_on_mutation(schedule: MLSchedule, *, now_utc: datetime | None = None) -> datetime | None:
    """Return today's due slot when a user mutates a schedule after cutoff.

    This is intentionally mutation-scoped behavior so a same-day run isn't
    silently deferred to tomorrow when the user updates/activates a schedule.
    """
    if not schedule.is_active or schedule.run_time is None:
        return None
    if schedule.frequency not in {MLSchedule.Frequency.DAILY, MLSchedule.Frequency.WEEKLY}:
        return None

    now_utc = now_utc or timezone.now()
    local_tz, _, _ = resolve_timezone_for_facility(
        schedule.timezone,
        schedule.facility,
        prefer_facility_for_utc=True,
    )
    local_now = now_utc.astimezone(local_tz)
    pre_run_delta = timedelta(minutes=max(0, int(schedule.pre_run_offset_minutes or 0)))

    if schedule.frequency == MLSchedule.Frequency.WEEKLY:
        if local_now.weekday() != _weekly_anchor_weekday(schedule, local_tz):
            return None

    slot_local = datetime.combine(local_now.date(), schedule.run_time, tzinfo=local_tz) - pre_run_delta

    # Keep this strictly same-day catch-up semantics.
    if slot_local.date() != local_now.date():
        return None
    if slot_local > local_now:
        return None

    slot_utc = slot_local.astimezone(dt_timezone.utc)
    if schedule.last_run_at and schedule.last_run_at >= slot_utc:
        return None
    return slot_utc


def _next_run_at_for_schedule_mutation(schedule: MLSchedule) -> datetime | None:
    if not schedule.is_active:
        return None

    computed_next_run = compute_next_run_at(schedule)
    forced_same_day_due = _same_day_due_slot_on_mutation(schedule)
    if forced_same_day_due is None:
        return computed_next_run

    if computed_next_run is None:
        return forced_same_day_due
    return min(computed_next_run, forced_same_day_due)


def _validate_job_access(job: MLJob, user):
    if _user_is_super_admin(user):
        return
    user_hospital = _user_hospital(user)
    if not user_hospital:
        raise PermissionDenied("User is not associated with a hospital.")
    if not job.facility_id or str(job.facility_id) != str(user_hospital.id):
        raise PermissionDenied("You do not have access to this ML job.")


def _resolve_job_facility(user, scope_type: str, facility_id):
    if scope_type != MLJob.ScopeType.FACILITY:
        return None
    if not facility_id:
        raise ValidationError({"facility_id": "facility_id is required when scope_type is facility."})

    try:
        facility = Hospital.objects.get(id=facility_id)
    except Hospital.DoesNotExist:
        raise NotFound("Facility not found.")

    if facility.verified_status == Hospital.VerifiedStatus.OFFBOARDED:
        raise ValidationError({"detail": "offboarded facility cannot be used for ML jobs."})

    if not _user_is_super_admin(user):
        user_hospital = _user_hospital(user)
        if not user_hospital or str(user_hospital.id) != str(facility.id):
            raise PermissionDenied("Hospital admins can only create jobs for their own facility.")
    return facility


def _ensure_active_job_limit(facility: Hospital | None, job_type: str):
    if facility is None:
        return
    setting, _ = FacilityMLSetting.objects.get_or_create(facility=facility)
    current_active = MLJob.objects.filter(
        facility=facility,
        job_type=job_type,
        status__in=[
            MLJob.Status.PENDING,
            MLJob.Status.SNAPSHOT_READY,
            MLJob.Status.DISPATCHED,
            MLJob.Status.RUNNING,
            MLJob.Status.CALLBACK_RECEIVED,
        ],
    ).count()
    if current_active >= setting.max_active_jobs_per_type:
        raise ValidationError({"code": "active_job_exists", "message": "An active job of this type already exists for this facility."})


def _normalize_job_payload(validated_data: dict) -> dict:
    payload = dict(validated_data)
    if payload.get("facility_id"):
        payload["facility_id"] = str(payload["facility_id"])
    return payload


def _idempotent_fetch_or_conflict(actor, endpoint: str, key: str, payload_hash: str):
    record = MLJobIdempotency.objects.filter(actor=actor, endpoint=endpoint, idempotency_key=key).first()
    if not record:
        return None
    if record.payload_hash != payload_hash:
        raise ValidationError({"detail": "idempotency_conflict"})
    return record.response_snapshot


def create_ml_job(actor, validated_data: dict, idempotency_key: str) -> dict:
    if not idempotency_key:
        raise ValidationError({"detail": "Idempotency-Key header is required."})

    scope_type = validated_data["scope_type"]
    facility = _resolve_job_facility(actor, scope_type, validated_data.get("facility_id"))
    _ensure_active_job_limit(facility, validated_data["job_type"])

    endpoint = "ml_jobs_create"
    payload = _normalize_job_payload(validated_data)
    payload_hash = _payload_hash(payload)
    existing = _idempotent_fetch_or_conflict(actor, endpoint, idempotency_key, payload_hash)
    if existing:
        return existing

    with transaction.atomic():
        job = MLJob.objects.create(
            job_type=validated_data["job_type"],
            scope_type=scope_type,
            facility=facility,
            status=MLJob.Status.PENDING,
            scheduled_time=validated_data.get("scheduled_time") or timezone.now(),
            model_version=validated_data.get("model_version", ""),
            parameters=validated_data.get("parameters", {}),
            created_by=actor,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        _write_job_event(job, "job_created", payload={"scope_type": scope_type, "facility_id": str(job.facility_id or "")})

        response_snapshot = {
            "job": {
                "id": str(job.id),
                "job_type": job.job_type,
                "scope_type": job.scope_type,
                "facility_id": str(job.facility_id) if job.facility_id else None,
                "status": _job_api_status(job),
                "scheduled_time": job.scheduled_time.isoformat(),
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

        if bool(getattr(settings, "ML_AUTO_DISPATCH_ON_JOB_CREATE", False)):
            queued_job_id = str(job.id)

            def _queue_pipeline() -> None:
                from .tasks import queue_ml_job_execution  # noqa: PLC0415

                queue_ml_job_execution(queued_job_id)

            transaction.on_commit(_queue_pipeline)

    logger.info("ML job created: %s (%s)", job.id, job.job_type)
    return response_snapshot


def list_ml_jobs(user, query_params) -> tuple[list[MLJob], int, int, int]:
    queryset = MLJob.objects.select_related("facility", "created_by").all().order_by("-created_at")
    if not _user_is_super_admin(user):
        hospital = _user_hospital(user)
        queryset = queryset.filter(facility=hospital)

    filters = {
        "job_type": query_params.get("job_type"),
        "status": query_params.get("status"),
        "facility_id": query_params.get("facility_id"),
        "scope_type": query_params.get("scope_type"),
    }
    if filters["job_type"]:
        queryset = queryset.filter(job_type=filters["job_type"])
    if filters["scope_type"]:
        queryset = queryset.filter(scope_type=filters["scope_type"])

    status_filter = filters["status"]
    if status_filter in {"pending", "processing", "completed", "failed"}:
        if status_filter == "pending":
            queryset = queryset.filter(status=MLJob.Status.PENDING)
        elif status_filter == "processing":
            queryset = queryset.filter(status__in=PROCESSING_API_STATUSES)
        elif status_filter == "completed":
            queryset = queryset.filter(status__in=COMPLETED_API_STATUSES)
        elif status_filter == "failed":
            queryset = queryset.filter(status__in=FAILED_API_STATUSES)

    if filters["facility_id"]:
        queryset = queryset.filter(facility_id=filters["facility_id"])

    created_from = query_params.get("created_from")
    created_to = query_params.get("created_to")
    if created_from:
        queryset = queryset.filter(created_at__gte=created_from)
    if created_to:
        queryset = queryset.filter(created_at__lte=created_to)

    page = int(query_params.get("page", 1))
    limit = int(query_params.get("limit", 20))
    if page < 1:
        page = 1
    if limit < 1:
        limit = 20

    total = queryset.count()
    offset = (page - 1) * limit
    jobs = list(queryset[offset : offset + limit])
    return jobs, page, limit, total


def get_ml_job(job_id, user) -> MLJob:
    try:
        job = MLJob.objects.select_related("facility", "created_by").get(id=job_id)
    except MLJob.DoesNotExist:
        raise NotFound("ML job not found.")
    _validate_job_access(job, user)
    return job


def _localize_datetime_iso(value, local_tz):
    if not value:
        return None
    return value.astimezone(local_tz).isoformat()


def _job_display_timezone(job: MLJob):
    parameters = job.parameters if isinstance(job.parameters, dict) else {}
    schedule_meta = parameters.get("_schedule_meta") if isinstance(parameters.get("_schedule_meta"), dict) else {}
    requested_timezone = str(schedule_meta.get("timezone") or "").strip() or None
    local_tz, timezone_name, _ = resolve_timezone_for_facility(
        requested_timezone or "auto",
        job.facility,
        prefer_facility_for_utc=True,
    )
    return local_tz, timezone_name, schedule_meta


def serialize_job(job: MLJob) -> dict:
    local_tz, timezone_name, schedule_meta = _job_display_timezone(job)

    schedule_context = None
    if schedule_meta:
        schedule_context = {
            "schedule_id": schedule_meta.get("schedule_id"),
            "run_time_local": schedule_meta.get("run_time_local"),
            "scheduled_time_local": schedule_meta.get("scheduled_time_local"),
            "scheduled_time_utc": schedule_meta.get("scheduled_time_utc"),
            "timezone": schedule_meta.get("timezone"),
        }

    return {
        "id": str(job.id),
        "model": (job.parameters or {}).get("_model_key"),
        "job_type": job.job_type,
        "status": _job_api_status(job),
        "facility_id": str(job.facility_id) if job.facility_id else None,
        "display_timezone": timezone_name,
        "scheduled_time": job.scheduled_time.isoformat() if job.scheduled_time else None,
        "scheduled_time_local": _localize_datetime_iso(job.scheduled_time, local_tz),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "started_at_local": _localize_datetime_iso(job.started_at, local_tz),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "completed_at_local": _localize_datetime_iso(job.completed_at, local_tz),
        "retry_count": job.retry_count,
        "has_partial_failures": job.has_partial_failures,
        "partial_failure_count": job.partial_failure_count,
        "error": {"code": job.error_code, "message": job.error_message} if job.error_code or job.error_message else None,
        "schedule_context": schedule_context,
    }


def retry_ml_job(job: MLJob, actor, reason: str, idempotency_key: str = "") -> dict:
    _validate_job_access(job, actor)
    if job.status not in FAILED_API_STATUSES:
        raise ValidationError({"detail": "Only failed/cancelled jobs can be retried."})

    payload = {"job_id": str(job.id), "reason": reason}
    payload_hash = _payload_hash(payload)
    endpoint = f"ml_jobs_retry:{job.id}"
    if idempotency_key:
        existing = _idempotent_fetch_or_conflict(actor, endpoint, idempotency_key, payload_hash)
        if existing:
            return existing

    with transaction.atomic():
        job.status = MLJob.Status.PENDING
        job.retry_count += 1
        job.error_code = ""
        job.error_message = ""
        job.started_at = None
        job.completed_at = None
        job.has_partial_failures = False
        job.partial_failure_count = 0
        job.save(
            update_fields=[
                "status",
                "retry_count",
                "error_code",
                "error_message",
                "started_at",
                "completed_at",
                "has_partial_failures",
                "partial_failure_count",
                "updated_at",
            ]
        )
        _write_job_event(job, "job_retried", payload={"reason": reason, "retry_count": job.retry_count})

        response_snapshot = {
            "job": {
                "id": str(job.id),
                "status": _job_api_status(job),
                "retry_count": job.retry_count,
            }
        }

        if idempotency_key:
            MLJobIdempotency.objects.create(
                actor=actor,
                endpoint=endpoint,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                response_snapshot=response_snapshot,
            )

        if bool(getattr(settings, "ML_AUTO_DISPATCH_ON_JOB_CREATE", False)):
            queued_job_id = str(job.id)

            def _queue_pipeline() -> None:
                from .tasks import queue_ml_job_execution  # noqa: PLC0415

                queue_ml_job_execution(queued_job_id)

            transaction.on_commit(_queue_pipeline)

    return response_snapshot


def cancel_ml_job(job: MLJob, actor, reason: str) -> dict:
    _validate_job_access(job, actor)
    if job.status in COMPLETED_API_STATUSES:
        raise ValidationError({"detail": "Completed jobs cannot be cancelled."})

    job.status = MLJob.Status.CANCELLED
    job.completed_at = timezone.now()
    job.error_code = "cancelled"
    job.error_message = reason or "manual cancellation"
    job.save(update_fields=["status", "completed_at", "error_code", "error_message", "updated_at"])
    _write_job_event(job, "job_cancelled", payload={"reason": reason})
    return {"job": {"id": str(job.id), "status": _job_api_status(job)}}


def list_job_events(job: MLJob, user) -> list[MLJobEvent]:
    _validate_job_access(job, user)
    return list(job.events.order_by("-event_time"))


def create_schedule(actor, validated_data: dict) -> MLSchedule:
    facility = _resolve_job_facility(actor, MLJob.ScopeType.FACILITY, validated_data.get("facility_id"))
    schedule_timezone = normalize_schedule_timezone(validated_data.get("timezone"), facility)
    schedule = MLSchedule.objects.create(
        job_type=validated_data["job_type"],
        facility=facility,
        frequency=validated_data["frequency"],
        run_time=validated_data.get("run_time"),
        cron_expression=validated_data.get("cron_expression") or "",
        timezone=schedule_timezone,
        pre_run_offset_minutes=validated_data.get("pre_run_offset_minutes", 0),
        parameters=validated_data.get("parameters", {}),
        is_active=validated_data.get("is_active", True),
        created_by=actor,
    )
    if schedule.is_active:
        schedule.next_run_at = _next_run_at_for_schedule_mutation(schedule)
        schedule.save(update_fields=["next_run_at", "updated_at"])
        _trigger_schedule_processing_on_commit(schedule.next_run_at)
    return schedule


def get_schedule(schedule_id, user) -> MLSchedule:
    try:
        schedule = MLSchedule.objects.select_related("facility").get(id=schedule_id)
    except MLSchedule.DoesNotExist:
        raise NotFound("Schedule not found.")

    if _user_is_super_admin(user):
        return schedule

    user_hospital = _user_hospital(user)
    if not user_hospital or str(user_hospital.id) != str(schedule.facility_id):
        raise PermissionDenied("You do not have access to this schedule.")
    return schedule


def list_schedules(user, query_params):
    queryset = MLSchedule.objects.select_related("facility").order_by("-created_at")
    if not _user_is_super_admin(user):
        queryset = queryset.filter(facility=_user_hospital(user))

    job_type = query_params.get("job_type")
    if job_type:
        queryset = queryset.filter(job_type=job_type)

    facility_id = query_params.get("facility_id")
    if facility_id:
        queryset = queryset.filter(facility_id=facility_id)

    is_active = query_params.get("is_active")
    if is_active in {"true", "false"}:
        queryset = queryset.filter(is_active=(is_active == "true"))

    return queryset


def update_schedule(schedule: MLSchedule, user, data: dict) -> MLSchedule:
    _ = get_schedule(schedule.id, user)

    mutable_data = dict(data)
    if "timezone" in mutable_data:
        mutable_data["timezone"] = normalize_schedule_timezone(mutable_data.get("timezone"), schedule.facility)

    for field in [
        "frequency",
        "run_time",
        "cron_expression",
        "timezone",
        "pre_run_offset_minutes",
        "is_active",
        "parameters",
    ]:
        if field in mutable_data:
            setattr(schedule, field, mutable_data[field])

    schedule.next_run_at = _next_run_at_for_schedule_mutation(schedule)
    schedule.save(update_fields=[
        "frequency",
        "run_time",
        "cron_expression",
        "timezone",
        "pre_run_offset_minutes",
        "is_active",
        "parameters",
        "next_run_at",
        "updated_at",
    ])
    if schedule.is_active:
        _trigger_schedule_processing_on_commit(schedule.next_run_at)
    return schedule


def set_schedule_active(schedule: MLSchedule, user, is_active: bool) -> MLSchedule:
    _ = get_schedule(schedule.id, user)
    schedule.is_active = is_active
    schedule.next_run_at = _next_run_at_for_schedule_mutation(schedule)
    schedule.save(update_fields=["is_active", "next_run_at", "updated_at"])
    if is_active:
        _trigger_schedule_processing_on_commit(schedule.next_run_at)
    return schedule


def _assert_probability(value, field_name: str):
    v = Decimal(str(value))
    if v < 0 or v > 1:
        raise ValidationError({field_name: "must be between 0 and 1."})


def _haversine_distance_km(lat1, lon1, lat2, lon2):
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None
    radius_km = 6371
    d_lat = math.radians(float(lat2) - float(lat1))
    d_lon = math.radians(float(lon2) - float(lon1))
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(float(lat1))) * math.cos(math.radians(float(lat2))) * math.sin(d_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(radius_km * c, 2)


def _candidate_facilities_for_catalog(catalog: ResourceCatalog, origin_facility: Hospital, max_candidates: int = 20):
    items = []
    candidates = (
        ResourceCatalog.objects.select_related("hospital", "inventory")
        .filter(name=catalog.name, is_shareable=True)
        .exclude(hospital=origin_facility)
    )
    for candidate in candidates:
        if candidate.hospital.verified_status != Hospital.VerifiedStatus.VERIFIED:
            continue
        inventory = getattr(candidate, "inventory", None)
        if not inventory:
            continue
        available_quantity = max(0, inventory.quantity_available - inventory.reserved_quantity)
        if available_quantity <= 0:
            continue
        distance_km = _haversine_distance_km(
            origin_facility.latitude,
            origin_facility.longitude,
            candidate.hospital.latitude,
            candidate.hospital.longitude,
        )
        items.append(
            {
                "facility_id": str(candidate.hospital_id),
                "distance_km": distance_km if distance_km is not None else 0,
                "available_quantity": available_quantity,
            }
        )
    items.sort(key=lambda x: x["distance_km"])
    return items[:max_candidates]


def _ensure_callback_headers(
    payload: dict,
    headers: dict,
    signature_payload: dict | str | None = None,
    *,
    allow_legacy_hmac: bool = False,
) -> str:
    signature = headers.get("X-Signature") or headers.get("x-signature")
    request_id = headers.get("X-Request-Id") or headers.get("x-request-id")
    timestamp = headers.get("X-Timestamp") or headers.get("x-timestamp")

    if not signature or not request_id or not timestamp:
        raise AuthenticationFailed("Missing callback authentication headers.")

    try:
        ts_int = int(timestamp)
        callback_time = datetime.fromtimestamp(ts_int, tz=dt_timezone.utc)
    except ValueError:
        try:
            callback_time = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError:
            raise AuthenticationFailed("Invalid callback timestamp.")

    drift = abs((timezone.now() - callback_time).total_seconds())
    ttl_seconds = int(getattr(settings, "ML_CALLBACK_REPLAY_TTL_SECONDS", 300))
    if drift > ttl_seconds:
        raise AuthenticationFailed("Callback timestamp outside replay window.")

    normalized_sig = signature.split("=", 1)[-1].strip().lower()

    configured_secret = str(getattr(settings, "ML_SERVER_B_HMAC_SECRET", "dev-secret")).strip()
    if not configured_secret:
        raise AuthenticationFailed("ML_SERVER_B_HMAC_SECRET is not configured.")

    candidate_payload_strings = []
    payload_for_signature = signature_payload if signature_payload is not None else payload
    if isinstance(payload_for_signature, str):
        raw_value = payload_for_signature.strip()
        if raw_value:
            candidate_payload_strings.append(raw_value)
            try:
                parsed = json.loads(raw_value)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                candidate_payload_strings.append(_canonical_json(parsed))
                candidate_payload_strings.append(json.dumps(parsed, separators=(",", ":"), default=str))
    elif isinstance(payload_for_signature, dict):
        candidate_payload_strings.append(_canonical_json(payload_for_signature))
        candidate_payload_strings.append(json.dumps(payload_for_signature, separators=(",", ":"), default=str))

    if not candidate_payload_strings:
        candidate_payload_strings.append(_canonical_json(payload))

    signature_valid = False
    for payload_string in dict.fromkeys(candidate_payload_strings):
        callback_digest = hashlib.sha256(
            f"{timestamp}.{request_id}.{payload_string}.{configured_secret}".encode("utf-8")
        ).hexdigest()
        if hmac.compare_digest(normalized_sig, callback_digest.lower()):
            signature_valid = True
            break

        if allow_legacy_hmac:
            legacy_digest = hmac.new(
                configured_secret.encode("utf-8"),
                f"{timestamp}.{payload_string}".encode("utf-8"),
                hashlib.sha256,
            ).digest()
            if (
                hmac.compare_digest(normalized_sig, legacy_digest.hex().lower())
                or hmac.compare_digest(normalized_sig, base64.b64encode(legacy_digest).decode("ascii").lower())
            ):
                signature_valid = True
                break
        if signature_valid:
            break

    if not signature_valid:
        logger.warning(
            "ML callback signature validation failed for request_id=%s timestamp=%s signature_prefix=%s",
            request_id,
            timestamp,
            normalized_sig[:16],
        )
        raise AuthenticationFailed("Invalid callback signature.")

    return request_id


def _validate_outbreak_neighbors(payload: dict, max_neighbors: int):
    neighbors = payload.get("neighbors") or {}
    results = payload.get("results") or []
    result_facility_ids = {str(item.get("facility_id")) for item in results if item.get("facility_id")}

    for facility_id, neighbor_list in neighbors.items():
        if str(facility_id) not in result_facility_ids:
            raise ValidationError({"neighbors": "Neighbor keys must exist in outbreak results."})
        if len(neighbor_list) > max_neighbors:
            raise ValidationError({"neighbors": "Neighbor count exceeds max_neighbors."})

        sortable_neighbors = []
        for row in neighbor_list:
            neighbor_facility_id = str(row.get("facility_id") or "").strip()
            if not neighbor_facility_id:
                raise ValidationError({"neighbors": "Each neighbor requires facility_id."})
            distance_km = Decimal(str(row.get("distance_km", 0)))
            if distance_km < 0:
                raise ValidationError({"neighbors": "distance_km must be non-negative."})
            sortable_neighbors.append((distance_km, neighbor_facility_id))

        if sortable_neighbors != sorted(sortable_neighbors, key=lambda item: (item[0], item[1])):
            raise ValidationError({"neighbors": "Neighbors must be sorted by distance_km then facility_id ascending."})


def _save_forecast_result(job: MLJob, row: dict):
    predicted_demand = int(row.get("predicted_demand", 0))
    if predicted_demand < 0:
        raise ValidationError({"predicted_demand": "must be non-negative."})

    confidence = row.get("confidence_score", 0)
    _assert_probability(confidence, "confidence_score")

    facility_id = str(row.get("facility_id") or "").strip()
    if not facility_id:
        raise ValidationError({"facility_id": "facility_id is required for forecast rows."})

    resource_catalog_id = str(row.get("resource_catalog_id") or "").strip()
    if not resource_catalog_id:
        raise ValidationError({"resource_catalog_id": "resource_catalog_id is required for forecast rows."})

    try:
        facility = Hospital.objects.get(id=facility_id)
    except Hospital.DoesNotExist:
        raise ValidationError({"facility_id": "Facility not found."})

    try:
        catalog = ResourceCatalog.objects.select_related("inventory").get(id=resource_catalog_id, hospital=facility)
    except ResourceCatalog.DoesNotExist:
        raise ValidationError({"resource_catalog_id": "Resource catalog not found for facility."})

    setting, _ = FacilityMLSetting.objects.get_or_create(facility=facility)
    available_quantity = max(0, catalog.inventory.quantity_available - catalog.inventory.reserved_quantity)
    threshold = max(int(setting.stock_threshold), int(catalog.minimum_stock_level))

    shareable_quantity = max(0, available_quantity - threshold)
    restock_amount = max(0, predicted_demand - available_quantity)
    restock = restock_amount > 0

    decision_log = {
        "inputs": {
            "predicted_demand": predicted_demand,
            "available_quantity": available_quantity,
        },
        "thresholds": {
            "stock_threshold": threshold,
        },
        "outputs": {
            "shareable_quantity": shareable_quantity,
            "restock": restock,
            "restock_amount": restock_amount,
        },
    }

    request_candidates = _candidate_facilities_for_catalog(
        catalog=catalog,
        origin_facility=facility,
        max_candidates=int(job.parameters.get("max_neighbors", 20) or 20),
    )

    MLForecastResult.objects.update_or_create(
        job=job,
        facility=facility,
        resource_catalog=catalog,
        defaults={
            "prediction_horizon_days": int(job.parameters.get("prediction_horizon_days", 1)),
            "predicted_demand": predicted_demand,
            "confidence_score": Decimal(str(confidence)),
            "shareable_quantity": shareable_quantity,
            "restock": restock,
            "restock_amount": restock_amount,
            "explanation": "Stock below safety threshold" if restock else "Stock within threshold",
            "decision_log": decision_log,
            "request_candidates": request_candidates,
        },
    )


def _risk_level(probability: Decimal, setting: FacilityMLSetting) -> str:
    if probability >= setting.outbreak_high_threshold:
        return MLOutbreakResult.RiskLevel.HIGH
    if probability >= setting.outbreak_low_threshold:
        return MLOutbreakResult.RiskLevel.MEDIUM
    return MLOutbreakResult.RiskLevel.LOW


def _save_outbreak_result(job: MLJob, row: dict, neighbors_payload: dict):
    probability = Decimal(str(row.get("outbreak_probability", 0)))
    _assert_probability(probability, "outbreak_probability")

    facility_id = str(row.get("facility_id") or "").strip()
    if not facility_id:
        raise ValidationError({"facility_id": "facility_id is required for outbreak rows."})

    try:
        facility = Hospital.objects.get(id=facility_id)
    except Hospital.DoesNotExist:
        raise ValidationError({"facility_id": "Facility not found."})

    setting, _ = FacilityMLSetting.objects.get_or_create(facility=facility)
    risk_level = _risk_level(probability, setting)

    upazila = str(row.get("upazila") or "")
    ml_status = str(row.get("status") or "")
    likely_disease = str(row.get("likely_disease") or "")
    social_confirmation = str(row.get("social_confirmation") or "")
    social_score_raw = row.get("social_score")
    social_score = Decimal(str(social_score_raw)) if social_score_raw is not None else None
    
    # Store enriched fields from ML server (model_prob and final_confidence)
    model_prob_raw = row.get("model_prob")
    model_prob = Decimal(str(model_prob_raw)) if model_prob_raw is not None else None
    final_confidence_raw = row.get("final_confidence")
    final_confidence = Decimal(str(final_confidence_raw)) if final_confidence_raw is not None else None

    explanation = social_confirmation if social_confirmation else (
        "High outbreak risk detected due to trend signal"
        if risk_level == MLOutbreakResult.RiskLevel.HIGH
        else "Outbreak risk monitored"
    )

    facility_neighbors = neighbors_payload.get(str(facility.id), [])
    filtered_neighbors = []
    for neighbor in facility_neighbors:
        filtered_neighbors.append(
            {
                "facility_id": str(neighbor.get("facility_id")),
                "upazila": str(neighbor.get("upazila", "")),
                "distance_km": float(neighbor.get("distance_km", 0)),
            }
        )

    request_candidates = []
    for entry in filtered_neighbors:
        try:
            neighbor_facility = Hospital.objects.get(id=entry["facility_id"])
        except Hospital.DoesNotExist:
            continue
        total_available = (
            ResourceCatalog.objects.filter(hospital=neighbor_facility, is_shareable=True)
            .exclude(inventory__isnull=True)
            .aggregate(total=Sum(F("inventory__quantity_available") - F("inventory__reserved_quantity")))
            .get("total")
        )
        request_candidates.append(
            {
                "facility_id": entry["facility_id"],
                "upazila": entry["upazila"],
                "distance_km": entry["distance_km"],
                "available_quantity": int(total_available or 0),
            }
        )

    outbreak_result, _ = MLOutbreakResult.objects.update_or_create(
        job=job,
        facility=facility,
        defaults={
            "prediction_horizon_days": int(job.parameters.get("prediction_horizon_days", 1)),
            "outbreak_probability": probability,
            "outbreak_flag": bool(row.get("outbreak_flag", False)),
            "model_prob": model_prob,
            "final_confidence": final_confidence,
            "risk_level": risk_level,
            "upazila": upazila,
            "ml_status": ml_status,
            "likely_disease": likely_disease,
            "social_score": social_score,
            "social_confirmation": social_confirmation,
            "explanation": explanation,
            "decision_log": {
                "inputs": {"outbreak_probability": float(probability)},
                "thresholds": {
                    "low": float(setting.outbreak_low_threshold),
                    "high": float(setting.outbreak_high_threshold),
                },
                "outputs": {"risk_level": risk_level},
            },
            "neighbors": filtered_neighbors,
            "request_candidates": request_candidates,
        },
    )

    MLOutbreakNeighborCandidate.objects.filter(outbreak_result=outbreak_result).delete()
    for index, neighbor in enumerate(filtered_neighbors, start=1):
        try:
            neighbor_facility = Hospital.objects.get(id=neighbor["facility_id"])
        except Hospital.DoesNotExist:
            continue
        MLOutbreakNeighborCandidate.objects.create(
            outbreak_result=outbreak_result,
            neighbor_facility=neighbor_facility,
            distance_km=Decimal(str(neighbor["distance_km"])),
            rank=index,
        )


def process_server_b_callback(payload: dict, headers: dict, signature_payload: dict | None = None) -> dict:
    request_id = _ensure_callback_headers(payload, headers, signature_payload=signature_payload)
    callback_hash = _payload_hash(payload)

    with transaction.atomic():
        try:
            job = MLJob.objects.select_for_update().get(id=payload["job_id"])
        except MLJob.DoesNotExist:
            raise NotFound("ML job not found.")

        existing_request = MLCallbackDedup.objects.filter(job=job, request_id=request_id).first()
        if existing_request:
            if existing_request.payload_hash != callback_hash:
                raise ValidationError({"detail": "callback_conflict"})
            return {"job_id": str(job.id), "accepted": True, "idempotent": True}

        if job.status in TERMINAL_JOB_STATUSES:
            previous_callback_event = (
                MLJobEvent.objects.filter(job=job, event_type__in=["callback_processed", "callback_failed"]).order_by("-event_time").first()
            )
            previous_hash = (previous_callback_event.payload or {}).get("payload_hash") if previous_callback_event else ""
            if previous_hash:
                if previous_hash != callback_hash:
                    raise ValidationError({"detail": "callback_conflict"})
                MLCallbackDedup.objects.create(job=job, request_id=request_id, payload_hash=callback_hash)
                return {"job_id": str(job.id), "accepted": True, "idempotent": True}

        if payload["job_type"] != job.job_type:
            raise ValidationError({"detail": "job_type mismatch."})

        expected_horizon = int(job.parameters.get("prediction_horizon_days", 1) or 1)
        if int(payload.get("prediction_horizon_days", 0) or 0) != expected_horizon:
            raise ValidationError({"detail": "prediction_horizon_days mismatch."})

        if job.job_type == MLJob.JobType.OUTBREAK:
            _validate_outbreak_neighbors(payload, int(job.parameters.get("max_neighbors", 20) or 20))

        job.status = MLJob.Status.CALLBACK_RECEIVED
        job.external_job_id = payload.get("external_job_id") or job.external_job_id
        job.callback_request_id = request_id
        job.model_version = payload.get("model_version", job.model_version)
        job.save(update_fields=["status", "external_job_id", "callback_request_id", "model_version", "updated_at"])

        if payload["status"] == "failed":
            error = payload.get("error") or {}
            job.status = MLJob.Status.FAILED
            job.completed_at = payload.get("completed_at") or timezone.now()
            job.error_code = str(error.get("code", "ml_execution_failed"))
            job.error_message = str(error.get("message", "ML execution failed."))
            job.save(update_fields=["status", "completed_at", "error_code", "error_message", "updated_at"])
            _write_job_event(
                job,
                "callback_failed",
                payload={
                    "request_id": request_id,
                    "payload_hash": callback_hash,
                    "callback_payload": payload,
                },
            )
            MLCallbackDedup.objects.create(job=job, request_id=request_id, payload_hash=callback_hash)
            return {"job_id": str(job.id), "accepted": True}

        results = payload.get("results", [])
        neighbors_payload = payload.get("neighbors", {})
        partial_failures = 0

        for index, row in enumerate(results):
            try:
                if job.job_type == MLJob.JobType.FORECAST:
                    _save_forecast_result(job, row)
                else:
                    _save_outbreak_result(job, row, neighbors_payload)
            except Exception as exc:  # noqa: BLE001
                partial_failures += 1
                MLResultRowError.objects.create(
                    job=job,
                    job_type=job.job_type,
                    source_row_index=index,
                    facility_identifier=str(row.get("facility_id", "")),
                    resource_identifier=str(row.get("resource_catalog_id", "")),
                    error_code="row_processing_error",
                    error_message=str(exc),
                    raw_payload=row,
                )

        job.completed_at = payload.get("completed_at") or timezone.now()
        job.has_partial_failures = partial_failures > 0
        job.partial_failure_count = partial_failures
        job.error_code = ""
        job.error_message = ""
        job.status = MLJob.Status.PARTIAL_COMPLETED if partial_failures > 0 else MLJob.Status.COMPLETED
        job.save(
            update_fields=[
                "status",
                "completed_at",
                "has_partial_failures",
                "partial_failure_count",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )

        _write_job_event(
            job,
            "callback_processed",
            payload={
                "request_id": request_id,
                "payload_hash": callback_hash,
                "partial_failures": partial_failures,
                "callback_payload": payload,
            },
        )
        MLCallbackDedup.objects.create(job=job, request_id=request_id, payload_hash=callback_hash)

    return {"job_id": str(job.id), "accepted": True}


def get_forecast_results(job: MLJob, user) -> dict:
    _validate_job_access(job, user)
    if job.job_type != MLJob.JobType.FORECAST:
        raise ValidationError({"detail": "job_type mismatch."})

    items = list(
        MLForecastResult.objects.filter(job=job)
        .select_related("facility", "resource_catalog")
        .order_by("facility_id", "resource_catalog_id")
    )
    return {
        "job": {"id": str(job.id), "status": _job_api_status(job)},
        "items": [
            {
                "facility_id": str(item.facility_id),
                "resource_catalog_id": str(item.resource_catalog_id),
                "prediction_horizon_days": item.prediction_horizon_days,
                "predicted_demand": item.predicted_demand,
                "shareable_quantity": item.shareable_quantity,
                "restock": item.restock,
                "restock_amount": item.restock_amount,
                "explanation": item.explanation,
                "decision_log": item.decision_log,
                "request_candidates": item.request_candidates,
                "confidence_score": float(item.confidence_score),
            }
            for item in items
        ],
        "has_partial_failures": job.has_partial_failures,
        "partial_failure_count": job.partial_failure_count,
    }


def get_outbreak_results(job: MLJob, user) -> dict:
    _validate_job_access(job, user)
    if job.job_type != MLJob.JobType.OUTBREAK:
        raise ValidationError({"detail": "job_type mismatch."})

    items = list(MLOutbreakResult.objects.filter(job=job).select_related("facility").order_by("facility_id"))
    return {
        "job": {"id": str(job.id), "status": _job_api_status(job)},
        "items": [
            {
                "facility_id": str(item.facility_id),
                "upazila": item.upazila,
                "prediction_horizon_days": item.prediction_horizon_days,
                "outbreak_probability": float(item.outbreak_probability),
                "outbreak_flag": item.outbreak_flag,
                "model_prob": float(item.model_prob) if item.model_prob is not None else None,
                "final_confidence": float(item.final_confidence) if item.final_confidence is not None else None,
                "risk_level": item.risk_level,
                "ml_status": item.ml_status,
                "likely_disease": item.likely_disease,
                "social_score": float(item.social_score) if item.social_score is not None else None,
                "social_confirmation": item.social_confirmation,
                "explanation": item.explanation,
                "decision_log": item.decision_log,
                "neighbors": item.neighbors,
                "request_candidates": item.request_candidates,
            }
            for item in items
        ],
        "has_partial_failures": job.has_partial_failures,
        "partial_failure_count": job.partial_failure_count,
    }


def _get_facility_for_read(user, facility_id):
    try:
        facility = Hospital.objects.get(id=facility_id)
    except Hospital.DoesNotExist:
        raise NotFound("Facility not found.")

    if _user_is_super_admin(user):
        return facility
    user_hospital = _user_hospital(user)
    if not user_hospital or str(user_hospital.id) != str(facility.id):
        raise PermissionDenied("You do not have access to this facility.")
    return facility


def get_latest_forecast_for_facility(user, facility_id) -> dict:
    facility = _get_facility_for_read(user, facility_id)
    job = (
        MLJob.objects.filter(
            facility=facility,
            job_type=MLJob.JobType.FORECAST,
            status__in=COMPLETED_API_STATUSES,
        )
        .order_by("-completed_at", "-created_at", "-id")
        .first()
    )
    if not job:
        raise NotFound("latest_result_not_found")
    items = get_forecast_results(job, user)["items"]
    return {
        "job_id": str(job.id),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "has_partial_failures": job.has_partial_failures,
        "partial_failure_count": job.partial_failure_count,
        "items": items,
    }


def get_latest_outbreak_for_facility(user, facility_id) -> dict:
    facility = _get_facility_for_read(user, facility_id)
    job = (
        MLJob.objects.filter(
            facility=facility,
            job_type=MLJob.JobType.OUTBREAK,
            status__in=COMPLETED_API_STATUSES,
        )
        .order_by("-completed_at", "-created_at", "-id")
        .first()
    )
    if not job:
        raise NotFound("latest_result_not_found")
    items = get_outbreak_results(job, user)["items"]
    return {
        "job_id": str(job.id),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "has_partial_failures": job.has_partial_failures,
        "partial_failure_count": job.partial_failure_count,
        "items": items,
    }


def get_request_suggestions(user, facility_id) -> dict:
    _ = _get_facility_for_read(user, facility_id)

    suggestions = []
    latest_forecast = get_latest_forecast_for_facility(user, facility_id)
    for item in latest_forecast["items"]:
        for candidate in item.get("request_candidates", []):
            suggestions.append(
                {
                    "source": "forecast",
                    "facility_id": candidate.get("facility_id"),
                    "distance_km": candidate.get("distance_km", 0),
                    "available_quantity": candidate.get("available_quantity", 0),
                }
            )

    try:
        latest_outbreak = get_latest_outbreak_for_facility(user, facility_id)
        for item in latest_outbreak["items"]:
            for candidate in item.get("request_candidates", []):
                suggestions.append(
                    {
                        "source": "outbreak",
                        "facility_id": candidate.get("facility_id"),
                        "distance_km": candidate.get("distance_km", 0),
                        "available_quantity": candidate.get("available_quantity", 0),
                    }
                )
    except NotFound:
        pass

    dedup = {}
    for entry in suggestions:
        key = entry["facility_id"]
        if key not in dedup:
            dedup[key] = entry
            continue
        dedup[key]["available_quantity"] = max(dedup[key]["available_quantity"], entry["available_quantity"])
        dedup[key]["distance_km"] = min(dedup[key]["distance_km"], entry["distance_km"])

    items = sorted(dedup.values(), key=lambda x: (x["distance_km"], -x["available_quantity"]))
    return {
        "facility_id": str(facility_id),
        "items": items,
    }


def trigger_facility_refresh(user, facility_id, job_type: str, prediction_horizon_days: int, idempotency_key: str) -> dict:
    """Trigger an on-demand ML inference job for a facility using direct JSON payload (no MinIO)."""
    from .inference_services import create_json_inference_job  # noqa: PLC0415

    model_key = "model1" if job_type == MLJob.JobType.FORECAST else "model2"

    try:
        facility = Hospital.objects.get(id=facility_id)
    except Hospital.DoesNotExist:
        raise NotFound("Facility not found.")

    upazila = str(
        facility.region_level_2
        or facility.city
        or facility.region_level_1
        or facility.state
        or facility.country
        or "UNKNOWN"
    )

    if model_key == "model1":
        catalogs = list(
            ResourceCatalog.objects.filter(hospital=facility)
            .select_related("inventory")
            .order_by("name")[:25]
        )
        rows = []
        for catalog in catalogs:
            inventory = getattr(catalog, "inventory", None)
            available = float(
                max(0, inventory.quantity_available - inventory.reserved_quantity)
            ) if inventory else 0.0
            rows.append(
                {
                    "facility_id": str(facility.id),
                    "resource_catalog_id": str(catalog.id),
                    "features": {
                        "medicine_name": str(catalog.name),
                        "upazila": upazila,
                        "signals_disease": "baseline",
                        "base_daily_sales": max(1.0, round(available / 30.0, 2)),
                        "outbreak_multiplier": 1.1,
                    },
                }
            )
        inference_input: dict = {"rows": rows}
    else:
        catalogs_qs = list(
            ResourceCatalog.objects.filter(hospital=facility).select_related("inventory")
        )
        total_available = sum(
            max(0, c.inventory.quantity_available - c.inventory.reserved_quantity)
            for c in catalogs_qs
            if getattr(c, "inventory", None)
        )
        recent_avg = round(float(total_available) / 30.0, 2) if total_available > 0 else 45.0
        baseline_avg = round(recent_avg * 0.75, 2)
        rows = [
            {
                "facility_id": str(facility.id),
                "upazila": upazila,
                "features": {
                    "recent_avg_sales": recent_avg,
                    "baseline_avg_sales": baseline_avg,
                    "neighbor_trend_score": 0.0,
                    "outbreak_signal": 0.0,
                },
            }
        ]
        inference_input = {"rows": rows, "neighbors": {}}

    validated_data: dict = {
        "facility_id": str(facility_id),
        "prediction_horizon_days": prediction_horizon_days,
        "input": inference_input,
        "model_version": "",
    }
    if model_key == "model2":
        validated_data["max_neighbors"] = 20

    return create_json_inference_job(
        actor=user,
        model_key=model_key,
        validated_data=validated_data,
        idempotency_key=idempotency_key,
    )


def update_facility_settings(user, facility_id, data: dict) -> FacilityMLSetting:
    facility = _get_facility_for_read(user, facility_id)
    setting, _ = FacilityMLSetting.objects.get_or_create(facility=facility)

    for field in [
        "max_neighbor_distance_km",
        "stock_threshold",
        "outbreak_low_threshold",
        "outbreak_high_threshold",
        "notification_cooldown_minutes",
        "max_active_jobs_per_type",
    ]:
        if field in data:
            setattr(setting, field, data[field])

    setting.save(
        update_fields=[
            "max_neighbor_distance_km",
            "stock_threshold",
            "outbreak_low_threshold",
            "outbreak_high_threshold",
            "notification_cooldown_minutes",
            "max_active_jobs_per_type",
            "updated_at",
        ]
    )
    return setting
