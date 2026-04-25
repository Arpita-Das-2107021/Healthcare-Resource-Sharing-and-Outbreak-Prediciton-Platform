"""Views for ML orchestration APIs.

Two API layers live here:

  SECTION A — INTERNAL ML PIPELINE  (ML engineers / background workers only)
      POST /api/v1/ml/model1/predict/
      POST /api/v1/ml/model2/predict/
      POST /api/v1/ml/jobs/            (and sub-routes)
      POST /api/v1/ml/schedules/       (and sub-routes)
      GET  /api/v1/ml/jobs/<id>/results/forecast|outbreak/
      Training & model-version lifecycle routes

  SECTION B — CLIENT-FACING FACILITY INSIGHTS  (authenticated hospital staff)
      GET  /api/v1/ml/facilities/me/latest-forecast/
      GET  /api/v1/ml/facilities/<id>/latest-forecast/
      GET  /api/v1/ml/facilities/me/latest-outbreak/
      GET  /api/v1/ml/facilities/<id>/latest-outbreak/
      GET  /api/v1/ml/facilities/me/request-suggestions/
      GET  /api/v1/ml/facilities/<id>/request-suggestions/
      POST /api/v1/ml/facilities/me/refresh/
      POST /api/v1/ml/facilities/<id>/refresh/

Do NOT add raw ML feature fields, scheduled_time, model_version, or
job-polling concerns to Section B endpoints.
"""
import datetime
import uuid

from rest_framework import status
from rest_framework.exceptions import NotFound as DRFNotFound
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions.base import (
    CanManageMLModelVersions,
    CanManageMLOperations,
    CanManageMLTrainingLifecycle,
    CanTriggerFacilityRefresh,
    CanViewFacilityForecast,
    CanViewFacilityOutbreak,
    CanViewFacilitySuggestions,
    CanViewMLForecast,
    CanViewMLJobs,
    CanViewMLOutbreak,
    CanViewMLSuggestions,
)

from .inference_services import create_json_inference_job

from .serializers import (
    FacilityMLSettingPatchSerializer,
    FacilityMLSettingSerializer,
    FacilityRefreshSerializer,
    MLJobCancelSerializer,
    MLJobCreateSerializer,
    MLJobEventSerializer,
    MLModelVersionReviewSerializer,
    MLModelVersionRollbackSerializer,
    MLJobRetrySerializer,
    MLScheduleCreateSerializer,
    MLScheduleSerializer,
    MLScheduleUpdateSerializer,
    MLTrainingCallbackSerializer,
    MLTrainingDatasetGenerateSerializer,
    MLTrainingDatasetReviewSerializer,
    MLTrainingJobCreateSerializer,
    Model1PredictSerializer,
    Model2PredictSerializer,
    ServerBCallbackSerializer,
)
from .services import (
    cancel_ml_job,
    create_ml_job,
    create_schedule,
    get_forecast_results,
    get_latest_forecast_for_facility,
    get_latest_outbreak_for_facility,
    get_ml_job,
    get_outbreak_results,
    get_request_suggestions,
    get_schedule,
    list_job_events,
    list_ml_jobs,
    list_schedules,
    process_server_b_callback,
    retry_ml_job,
    serialize_job,
    set_schedule_active,
    trigger_facility_refresh,
    update_facility_settings,
    update_schedule,
)
from .training_services import (
    activate_model_version,
    create_training_dataset_snapshot,
    create_training_job,
    deactivate_model_version,
    get_model_version,
    get_training_dataset_snapshot,
    get_training_job,
    list_active_model_configs,
    list_model_versions,
    list_training_dataset_snapshots,
    list_training_jobs,
    mark_model_version_approved,
    mark_model_version_reviewed,
    process_training_callback,
    review_training_dataset_snapshot,
    rollback_active_model_version,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _request_id(request) -> str:
    return request.headers.get("X-Request-Id") or str(uuid.uuid4())


def _success_response(request, data, *, status_code=status.HTTP_200_OK, meta=None):
    payload = {
        "success": True,
        "data": data,
        "meta": {"request_id": _request_id(request)},
    }
    if meta:
        payload["meta"].update(meta)
    return Response(payload, status=status_code)


def _validate_or_raise_422(serializer) -> None:
    if serializer.is_valid():
        return
    exc = ValidationError(serializer.errors)
    exc.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    raise exc


def _resolve_facility_id_from_user(user):
    """Derives facility_id for hospital-scoped users when it is not in the URL."""
    hospital = getattr(getattr(user, "staff", None), "hospital", None)
    if not hospital:
        raise PermissionDenied("Cannot determine facility: user is not associated with any facility.")
    return hospital.id


def _strip_ml_internals(item: dict) -> dict:
    """Remove internal ML fields that must not be exposed to frontend clients.

    Strips decision_log (ML pipeline debug data) and request_candidates
    (moved exclusively to the /request-suggestions/ endpoint to avoid duplication).
    """
    entry = dict(item)
    entry.pop("decision_log", None)
    entry.pop("request_candidates", None)
    return entry


_FORECAST_STALE_HOURS = 24
_OUTBREAK_STALE_HOURS = 12


def _is_stale(completed_at_iso: str | None, stale_hours: int) -> bool:
    """Returns True when the result is older than stale_hours or has no timestamp."""
    if not completed_at_iso:
        return True
    try:
        dt = datetime.datetime.fromisoformat(completed_at_iso.replace("Z", "+00:00"))
        age = datetime.datetime.now(datetime.timezone.utc) - dt
        return age > datetime.timedelta(hours=stale_hours)
    except Exception:
        return True


def _error_response(request, code: str, message: str, http_status: int) -> Response:
    """Return a structured error envelope matching the project standard."""
    return Response(
        {
            "success": False,
            "error": {"code": code, "message": message},
            "meta": {"request_id": _request_id(request)},
        },
        status=http_status,
    )


# ===========================================================================
# SECTION A — INTERNAL ML PIPELINE
# Access: ML_ENGINEER / ML_ADMIN roles (CanManageMLOperations and variants).
# These endpoints accept raw ML feature inputs, scheduling config, and
# model versioning fields.  They MUST NOT be called directly by the frontend.
# ===========================================================================

class MLJobCollectionView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLOperations]

    def post(self, request):
        serializer = MLJobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = create_ml_job(
            actor=request.user,
            validated_data=serializer.validated_data,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
        )
        return _success_response(request, data, status_code=status.HTTP_202_ACCEPTED)

    def get(self, request):
        jobs, page, limit, total = list_ml_jobs(request.user, request.query_params)
        data = {
            "items": [serialize_job(job) for job in jobs],
        }
        return _success_response(
            request,
            data,
            meta={
                "page": page,
                "limit": limit,
                "total": total,
            },
        )


class MLJobDetailView(APIView):
    permission_classes = [IsAuthenticated, CanViewMLJobs]

    def get(self, request, job_id):
        job = get_ml_job(job_id, request.user)
        return _success_response(request, {"job": serialize_job(job)})


class MLJobRetryView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLOperations]

    def post(self, request, job_id):
        job = get_ml_job(job_id, request.user)
        serializer = MLJobRetrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = retry_ml_job(
            job=job,
            actor=request.user,
            reason=serializer.validated_data.get("reason", ""),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
        )
        return _success_response(request, data, status_code=status.HTTP_202_ACCEPTED)


class MLJobCancelView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLOperations]

    def post(self, request, job_id):
        job = get_ml_job(job_id, request.user)
        serializer = MLJobCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = cancel_ml_job(
            job=job,
            actor=request.user,
            reason=serializer.validated_data.get("reason", ""),
        )
        return _success_response(request, data, status_code=status.HTTP_202_ACCEPTED)


class MLJobEventsView(APIView):
    permission_classes = [IsAuthenticated, CanViewMLJobs]

    def get(self, request, job_id):
        job = get_ml_job(job_id, request.user)
        events = list_job_events(job, request.user)
        return _success_response(request, {"items": MLJobEventSerializer(events, many=True).data})


class MLScheduleCollectionView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLOperations]

    def post(self, request):
        serializer = MLScheduleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        schedule = create_schedule(request.user, serializer.validated_data)
        return _success_response(
            request,
            {"schedule": MLScheduleSerializer(schedule).data},
            status_code=status.HTTP_201_CREATED,
        )

    def get(self, request):
        schedules = list_schedules(request.user, request.query_params)
        return _success_response(request, {"items": MLScheduleSerializer(schedules, many=True).data})


class MLScheduleDetailView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLOperations]

    def patch(self, request, schedule_id):
        schedule = get_schedule(schedule_id, request.user)
        serializer = MLScheduleUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = update_schedule(schedule, request.user, serializer.validated_data)
        return _success_response(request, {"schedule": MLScheduleSerializer(updated).data})


class MLScheduleActivateView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLOperations]

    def post(self, request, schedule_id):
        schedule = get_schedule(schedule_id, request.user)
        updated = set_schedule_active(schedule, request.user, is_active=True)
        return _success_response(request, {"schedule": MLScheduleSerializer(updated).data})


class MLScheduleDeactivateView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLOperations]

    def post(self, request, schedule_id):
        schedule = get_schedule(schedule_id, request.user)
        updated = set_schedule_active(schedule, request.user, is_active=False)
        return _success_response(request, {"schedule": MLScheduleSerializer(updated).data})


class MLForecastResultView(APIView):
    """Internal: raw forecast results tied to a specific job_id, including decision_log."""
    permission_classes = [IsAuthenticated, CanViewMLForecast]

    def get(self, request, job_id):
        job = get_ml_job(job_id, request.user)
        data = get_forecast_results(job, request.user)
        return _success_response(request, data)


class MLOutbreakResultView(APIView):
    """Internal: raw outbreak results tied to a specific job_id, including decision_log."""
    permission_classes = [IsAuthenticated, CanViewMLOutbreak]

    def get(self, request, job_id):
        job = get_ml_job(job_id, request.user)
        data = get_outbreak_results(job, request.user)
        return _success_response(request, data)


class FacilitySettingsPatchView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLOperations]

    def patch(self, request, facility_id):
        serializer = FacilityMLSettingPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        setting = update_facility_settings(request.user, facility_id, serializer.validated_data)
        return _success_response(request, {"setting": FacilityMLSettingSerializer(setting).data})


class Model1PredictView(APIView):
    """Internal: direct JSON-based forecast inference (model1). ML engineers only."""
    permission_classes = [IsAuthenticated, CanManageMLOperations]

    def post(self, request):
        serializer = Model1PredictSerializer(data=request.data)
        _validate_or_raise_422(serializer)
        try:
            data = create_json_inference_job(
                request.user,
                model_key="model1",
                validated_data=serializer.validated_data,
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        except ValidationError as exc:
            exc.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
            raise exc
        return _success_response(request, data, status_code=status.HTTP_202_ACCEPTED)


class Model2PredictView(APIView):
    """Internal: direct JSON-based outbreak inference (model2). ML engineers only."""
    permission_classes = [IsAuthenticated, CanManageMLOperations]

    def post(self, request):
        serializer = Model2PredictSerializer(data=request.data)
        _validate_or_raise_422(serializer)
        try:
            data = create_json_inference_job(
                request.user,
                model_key="model2",
                validated_data=serializer.validated_data,
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        except ValidationError as exc:
            exc.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
            raise exc
        return _success_response(request, data, status_code=status.HTTP_202_ACCEPTED)


# Training lifecycle — internal, ML engineers / admins only.

class MLTrainingDatasetGenerateView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLTrainingLifecycle]

    def post(self, request):
        serializer = MLTrainingDatasetGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = create_training_dataset_snapshot(request.user, serializer.validated_data)
        return _success_response(request, data, status_code=status.HTTP_201_CREATED)


class MLTrainingDatasetCollectionView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLTrainingLifecycle]

    def get(self, request):
        items = list_training_dataset_snapshots(request.user, request.query_params)
        return _success_response(request, {"items": items})


class MLTrainingDatasetDetailView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLTrainingLifecycle]

    def get(self, request, dataset_id):
        data = get_training_dataset_snapshot(request.user, dataset_id)
        return _success_response(request, data)


class MLTrainingDatasetApproveView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLTrainingLifecycle]

    def post(self, request, dataset_id):
        serializer = MLTrainingDatasetReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = review_training_dataset_snapshot(
            request.user,
            dataset_id,
            approve=True,
            notes=serializer.validated_data.get("notes", ""),
        )
        return _success_response(request, data)


class MLTrainingDatasetRejectView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLTrainingLifecycle]

    def post(self, request, dataset_id):
        serializer = MLTrainingDatasetReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = review_training_dataset_snapshot(
            request.user,
            dataset_id,
            approve=False,
            notes=serializer.validated_data.get("notes", ""),
        )
        return _success_response(request, data)


class MLTrainingJobCollectionView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLTrainingLifecycle]

    def post(self, request):
        serializer = MLTrainingJobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = create_training_job(
            request.user,
            serializer.validated_data,
            request.headers.get("Idempotency-Key", ""),
        )
        return _success_response(request, data, status_code=status.HTTP_202_ACCEPTED)

    def get(self, request):
        items = list_training_jobs(request.user, request.query_params)
        return _success_response(request, {"items": items})


class MLTrainingJobDetailView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLTrainingLifecycle]

    def get(self, request, training_job_id):
        data = get_training_job(request.user, training_job_id)
        return _success_response(request, data)


class MLModelVersionCollectionView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLTrainingLifecycle]

    def get(self, request):
        items = list_model_versions(request.user, request.query_params)
        return _success_response(request, {"items": items})


class MLModelVersionDetailView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLTrainingLifecycle]

    def get(self, request, version_id):
        data = get_model_version(request.user, version_id)
        return _success_response(request, data)


class MLModelVersionReviewView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLTrainingLifecycle]

    def post(self, request, version_id):
        serializer = MLModelVersionReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = mark_model_version_reviewed(request.user, version_id, serializer.validated_data.get("notes", ""))
        return _success_response(request, data)


class MLModelVersionApproveView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLModelVersions]

    def post(self, request, version_id):
        serializer = MLModelVersionReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = mark_model_version_approved(request.user, version_id, serializer.validated_data.get("notes", ""))
        return _success_response(request, data)


class MLModelVersionActivateView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLModelVersions]

    def post(self, request, version_id):
        data = activate_model_version(request.user, version_id)
        return _success_response(request, data)


class MLModelVersionDeactivateView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLModelVersions]

    def post(self, request, version_id):
        data = deactivate_model_version(request.user, version_id)
        return _success_response(request, data)


class MLModelVersionRollbackView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLModelVersions]

    def post(self, request, version_id):
        serializer = MLModelVersionRollbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = rollback_active_model_version(
            request.user,
            version_id,
            serializer.validated_data["target_version_id"],
        )
        return _success_response(request, data)


class ActiveModelConfigView(APIView):
    permission_classes = [IsAuthenticated, CanManageMLTrainingLifecycle]

    def get(self, request):
        items = list_active_model_configs(request.user)
        return _success_response(request, {"items": items})


# Callbacks — no auth; verified via HMAC signature in the service layer.

class ServerBTrainingCallbackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        raw_payload = request.body.decode("utf-8", errors="ignore")
        serializer = MLTrainingCallbackSerializer(data=request.data)
        _validate_or_raise_422(serializer)
        try:
            data = process_training_callback(
                serializer.validated_data,
                request.headers,
                signature_payload=raw_payload,
            )
        except ValidationError as exc:
            exc.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
            raise exc
        return _success_response(request, data, status_code=status.HTTP_202_ACCEPTED)


class ServerBCallbackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        raw_payload = request.body.decode("utf-8", errors="ignore")
        serializer = ServerBCallbackSerializer(data=request.data)
        _validate_or_raise_422(serializer)
        try:
            data = process_server_b_callback(
                serializer.validated_data,
                request.headers,
                signature_payload=raw_payload,
            )
        except ValidationError as exc:
            exc.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
            raise exc
        return _success_response(request, data, status_code=status.HTTP_202_ACCEPTED)


# ===========================================================================
# SECTION B — CLIENT-FACING FACILITY INSIGHTS
# Access: authenticated hospital staff (CanViewFacility* / CanTriggerFacilityRefresh).
# Rules:
#   - "me" routes resolve facility from request.user.staff.hospital (no URL param).
#   - explicit <facility_id> routes enforce ownership in the service layer.
#   - decision_log and request_candidates are stripped from all items.
#   - Empty results are returned as items:[] instead of raising 404.
#   - Every response includes status and is_stale fields.
# ===========================================================================

class FacilityLatestForecastView(APIView):
    """Latest completed forecast for a facility — ML internals stripped.

    Responds with an empty items list (not 404) when no completed forecast exists yet.
    """
    permission_classes = [IsAuthenticated, CanViewFacilityForecast]

    def get(self, request, facility_id=None):
        resolved_id = facility_id or _resolve_facility_id_from_user(request.user)
        try:
            data = get_latest_forecast_for_facility(request.user, resolved_id)
        except DRFNotFound:
            return _success_response(request, {
                "job_id": None,
                "status": None,
                "is_stale": True,
                "completed_at": None,
                "items": [],
                "has_partial_failures": False,
                "partial_failure_count": 0,
            })
        clean_items = [_strip_ml_internals(item) for item in data["items"]]
        return _success_response(request, {
            "job_id": data["job_id"],
            "status": "completed",
            "is_stale": _is_stale(data["completed_at"], _FORECAST_STALE_HOURS),
            "completed_at": data["completed_at"],
            "items": clean_items,
            "has_partial_failures": data["has_partial_failures"],
            "partial_failure_count": data["partial_failure_count"],
        })


class FacilityLatestOutbreakView(APIView):
    """Latest completed outbreak assessment for a facility — ML internals stripped.

    Responds with an empty items list (not 404) when no completed outbreak job exists yet.
    """
    permission_classes = [IsAuthenticated, CanViewFacilityOutbreak]

    def get(self, request, facility_id=None):
        resolved_id = facility_id or _resolve_facility_id_from_user(request.user)
        try:
            data = get_latest_outbreak_for_facility(request.user, resolved_id)
        except DRFNotFound:
            return _success_response(request, {
                "job_id": None,
                "status": None,
                "is_stale": True,
                "completed_at": None,
                "items": [],
                "has_partial_failures": False,
                "partial_failure_count": 0,
            })
        clean_items = [_strip_ml_internals(item) for item in data["items"]]
        return _success_response(request, {
            "job_id": data["job_id"],
            "status": "completed",
            "is_stale": _is_stale(data["completed_at"], _OUTBREAK_STALE_HOURS),
            "completed_at": data["completed_at"],
            "items": clean_items,
            "has_partial_failures": data["has_partial_failures"],
            "partial_failure_count": data["partial_failure_count"],
        })


class FacilityRequestSuggestionsView(APIView):
    """Deduplicated resource request suggestions derived from latest forecast and
    outbreak results.  This is the ONLY endpoint that exposes request_candidates —
    they are stripped from /latest-forecast/ and /latest-outbreak/ responses.

    Returns items:[] (not 404) when no forecast data exists yet.
    """
    permission_classes = [IsAuthenticated, CanViewFacilitySuggestions]

    def get(self, request, facility_id=None):
        resolved_id = facility_id or _resolve_facility_id_from_user(request.user)
        try:
            data = get_request_suggestions(request.user, resolved_id)
        except DRFNotFound:
            return _success_response(request, {
                "facility_id": str(resolved_id),
                "items": [],
            })
        return _success_response(request, data)


class FacilityRefreshView(APIView):
    """On-demand trigger: queues a new ML inference job for a facility.

    Restricted to users with explicit permission (analytics view, inventory manage,
    or ml:job.manage) — no role-based fallback (see CanTriggerFacilityRefresh).

    Accepts only job_type + prediction_horizon_days.  Backend prepares all ML
    feature inputs internally; no raw feature data is accepted from the caller.

    Response includes result_path so the frontend knows exactly where to poll.
    """
    permission_classes = [IsAuthenticated, CanTriggerFacilityRefresh]

    def post(self, request, facility_id=None):
        resolved_id = facility_id or _resolve_facility_id_from_user(request.user)
        serializer = FacilityRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job_type = serializer.validated_data["job_type"]
        idempotency_key = request.headers.get("Idempotency-Key") or str(uuid.uuid4())
        try:
            data = trigger_facility_refresh(
                user=request.user,
                facility_id=resolved_id,
                job_type=job_type,
                prediction_horizon_days=serializer.validated_data["prediction_horizon_days"],
                idempotency_key=idempotency_key,
            )
        except ValidationError as exc:
            detail = getattr(exc, "detail", {}) or {}
            if isinstance(detail, dict) and detail.get("code") == "active_job_exists":
                return _error_response(
                    request,
                    code="active_job_exists",
                    message="An active job of this type already exists for this facility.",
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            exc.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
            raise exc

        result_path = (
            f"/api/v1/ml/facilities/{resolved_id}/latest-forecast/"
            if job_type == "forecast"
            else f"/api/v1/ml/facilities/{resolved_id}/latest-outbreak/"
        )
        return _success_response(
            request,
            {**data, "result_path": result_path},
            status_code=status.HTTP_202_ACCEPTED,
        )
