"""Serializers for ML orchestration APIs."""
from django.utils import timezone
from rest_framework import serializers

from .scheduling import build_schedule_frontend_hint, is_supported_timezone_value
from .models import (
    FacilityMLSetting,
    MLForecastResult,
    MLJob,
    MLJobEvent,
    MLModelType,
    MLOutbreakResult,
    MLSchedule,
)


class MLJobCreateSerializer(serializers.Serializer):
    job_type = serializers.ChoiceField(choices=MLJob.JobType.choices)
    scope_type = serializers.ChoiceField(choices=MLJob.ScopeType.choices, default=MLJob.ScopeType.FACILITY)
    facility_id = serializers.UUIDField(required=False)
    scheduled_time = serializers.DateTimeField(required=False, allow_null=True)
    model_version = serializers.CharField(required=False, allow_blank=True, default="")
    parameters = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        scope_type = attrs.get("scope_type")
        facility_id = attrs.get("facility_id")
        if scope_type == MLJob.ScopeType.FACILITY and not facility_id:
            raise serializers.ValidationError({"facility_id": "facility_id is required when scope_type is facility."})

        params = attrs.get("parameters", {})
        horizon = params.get("prediction_horizon_days")
        if horizon is None or int(horizon) <= 0:
            raise serializers.ValidationError({"parameters": "prediction_horizon_days must be a positive integer."})

        if attrs.get("job_type") == MLJob.JobType.OUTBREAK:
            max_neighbors = params.get("max_neighbors", 20)
            if int(max_neighbors) <= 0:
                raise serializers.ValidationError({"parameters": "max_neighbors must be a positive integer."})
        return attrs


class MLJobRetrySerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class MLJobCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class MLJobEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = MLJobEvent
        fields = ["event_type", "event_time", "payload"]


class MLJobSerializer(serializers.ModelSerializer):
    facility_id = serializers.SerializerMethodField()

    class Meta:
        model = MLJob
        fields = [
            "id",
            "job_type",
            "scope_type",
            "facility_id",
            "status",
            "scheduled_time",
            "started_at",
            "completed_at",
            "retry_count",
            "has_partial_failures",
            "partial_failure_count",
            "error_code",
            "error_message",
            "created_at",
        ]

    def get_facility_id(self, obj):
        return str(obj.facility_id) if obj.facility_id else None


class MLScheduleCreateSerializer(serializers.Serializer):
    job_type = serializers.ChoiceField(choices=MLJob.JobType.choices)
    facility_id = serializers.UUIDField()
    frequency = serializers.ChoiceField(choices=MLSchedule.Frequency.choices)
    run_time = serializers.TimeField(required=False, allow_null=True)
    cron_expression = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    timezone = serializers.CharField(required=False, allow_blank=True, default="auto")
    pre_run_offset_minutes = serializers.IntegerField(required=False, min_value=0, default=0)
    is_active = serializers.BooleanField(required=False, default=True)
    parameters = serializers.JSONField(required=False, default=dict)

    def validate_timezone(self, value):
        if not is_supported_timezone_value(value):
            raise serializers.ValidationError("timezone must be an IANA timezone, UTC offset (e.g. UTC+06:00), or auto.")
        cleaned = (value or "").strip()
        return cleaned or "auto"

    def validate(self, attrs):
        frequency = attrs.get("frequency")
        run_time = attrs.get("run_time")
        cron_expression = (attrs.get("cron_expression") or "").strip()

        if frequency == MLSchedule.Frequency.CRON:
            if not cron_expression:
                raise serializers.ValidationError({"cron_expression": "cron_expression is required when frequency is cron."})
            attrs["cron_expression"] = cron_expression
        else:
            if run_time is None:
                raise serializers.ValidationError({"run_time": "run_time is required for daily and weekly schedules."})
            attrs["cron_expression"] = ""

        attrs["timezone"] = (attrs.get("timezone") or "auto").strip() or "auto"
        return attrs


class MLScheduleUpdateSerializer(serializers.Serializer):
    frequency = serializers.ChoiceField(choices=MLSchedule.Frequency.choices, required=False)
    run_time = serializers.TimeField(required=False, allow_null=True)
    cron_expression = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    timezone = serializers.CharField(required=False, allow_blank=True)
    pre_run_offset_minutes = serializers.IntegerField(required=False, min_value=0)
    is_active = serializers.BooleanField(required=False)
    parameters = serializers.JSONField(required=False)

    def validate_timezone(self, value):
        if not is_supported_timezone_value(value):
            raise serializers.ValidationError("timezone must be an IANA timezone, UTC offset (e.g. UTC+06:00), or auto.")
        cleaned = (value or "").strip()
        return cleaned or "auto"

    def validate(self, attrs):
        if "timezone" in attrs:
            attrs["timezone"] = (attrs.get("timezone") or "auto").strip() or "auto"

        if attrs.get("frequency") == MLSchedule.Frequency.CRON and "cron_expression" in attrs:
            cron_expression = (attrs.get("cron_expression") or "").strip()
            if not cron_expression:
                raise serializers.ValidationError({"cron_expression": "cron_expression cannot be blank when frequency is cron."})
            attrs["cron_expression"] = cron_expression

        if attrs.get("frequency") in {MLSchedule.Frequency.DAILY, MLSchedule.Frequency.WEEKLY} and "run_time" in attrs:
            if attrs.get("run_time") is None:
                raise serializers.ValidationError({"run_time": "run_time cannot be null for daily and weekly schedules."})

        return attrs


class MLScheduleSerializer(serializers.ModelSerializer):
    facility_id = serializers.SerializerMethodField()
    frontend_timing_hint = serializers.SerializerMethodField()

    class Meta:
        model = MLSchedule
        fields = [
            "id",
            "job_type",
            "facility_id",
            "frequency",
            "run_time",
            "cron_expression",
            "timezone",
            "pre_run_offset_minutes",
            "is_active",
            "parameters",
            "last_run_at",
            "next_run_at",
            "frontend_timing_hint",
            "created_at",
            "updated_at",
        ]

    def get_facility_id(self, obj):
        return str(obj.facility_id)

    def get_frontend_timing_hint(self, obj):
        return build_schedule_frontend_hint(obj)


class MLForecastResultSerializer(serializers.ModelSerializer):
    facility_id = serializers.SerializerMethodField()
    resource_catalog_id = serializers.SerializerMethodField()

    class Meta:
        model = MLForecastResult
        fields = [
            "facility_id",
            "resource_catalog_id",
            "prediction_horizon_days",
            "predicted_demand",
            "shareable_quantity",
            "restock",
            "restock_amount",
            "explanation",
            "decision_log",
            "request_candidates",
            "confidence_score",
        ]

    def get_facility_id(self, obj):
        return str(obj.facility_id)

    def get_resource_catalog_id(self, obj):
        return str(obj.resource_catalog_id)


class MLOutbreakResultSerializer(serializers.ModelSerializer):
    facility_id = serializers.SerializerMethodField()

    class Meta:
        model = MLOutbreakResult
        fields = [
            "facility_id",
            "prediction_horizon_days",
            "outbreak_probability",
            "risk_level",
            "explanation",
            "decision_log",
            "neighbors",
            "request_candidates",
        ]

    def get_facility_id(self, obj):
        return str(obj.facility_id)


class FacilityMLSettingSerializer(serializers.ModelSerializer):
    facility_id = serializers.SerializerMethodField()

    class Meta:
        model = FacilityMLSetting
        fields = [
            "facility_id",
            "max_neighbor_distance_km",
            "stock_threshold",
            "outbreak_low_threshold",
            "outbreak_high_threshold",
            "notification_cooldown_minutes",
            "max_active_jobs_per_type",
        ]

    def get_facility_id(self, obj):
        return str(obj.facility_id)


class FacilityMLSettingPatchSerializer(serializers.Serializer):
    max_neighbor_distance_km = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, min_value=0)
    stock_threshold = serializers.IntegerField(required=False, min_value=0)
    outbreak_low_threshold = serializers.DecimalField(max_digits=5, decimal_places=4, required=False, min_value=0, max_value=1)
    outbreak_high_threshold = serializers.DecimalField(max_digits=5, decimal_places=4, required=False, min_value=0, max_value=1)
    notification_cooldown_minutes = serializers.IntegerField(required=False, min_value=0)
    max_active_jobs_per_type = serializers.IntegerField(required=False, min_value=1)


class ServerBCallbackSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    job_type = serializers.ChoiceField(choices=MLJob.JobType.choices)
    external_job_id = serializers.CharField(required=False, allow_blank=False)
    model_version = serializers.CharField(required=False, allow_blank=True)
    prediction_horizon_days = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=["completed", "failed"])
    completed_at = serializers.DateTimeField(required=False, allow_null=False)
    results = serializers.ListField(child=serializers.JSONField(), required=False)
    neighbors = serializers.DictField(child=serializers.ListField(child=serializers.JSONField()), required=False)
    error = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs):
        required_common = [
            "external_job_id",
            "model_version",
            "completed_at",
            "results",
            "error",
        ]
        missing = [field for field in required_common if field not in attrs]
        if missing:
            raise serializers.ValidationError({"detail": f"missing required callback fields: {', '.join(missing)}"})

        if attrs["job_type"] == MLJob.JobType.OUTBREAK and "neighbors" not in attrs:
            raise serializers.ValidationError({"neighbors": "neighbors is required for outbreak callbacks."})

        if attrs["job_type"] == MLJob.JobType.FORECAST and "neighbors" in attrs:
            raise serializers.ValidationError({"neighbors": "neighbors is not allowed for forecast callbacks."})

        if attrs["status"] == "completed":
            if attrs.get("error") is not None:
                raise serializers.ValidationError({"error": "error must be null for completed callbacks."})
            if not isinstance(attrs.get("results"), list):
                raise serializers.ValidationError({"results": "results must be an array."})
        else:
            error_payload = attrs.get("error")
            if not isinstance(error_payload, dict):
                raise serializers.ValidationError({"error": "error must be an object for failed callbacks."})
            if not str(error_payload.get("code") or "").strip():
                raise serializers.ValidationError({"error": "error.code is required for failed callbacks."})
            if not str(error_payload.get("message") or "").strip():
                raise serializers.ValidationError({"error": "error.message is required for failed callbacks."})

        return attrs


class _BaseModelPredictSerializer(serializers.Serializer):
    facility_id = serializers.UUIDField(required=False)
    scheduled_time = serializers.DateTimeField(required=False, allow_null=True)
    prediction_horizon_days = serializers.IntegerField(min_value=1, default=1)
    model_version = serializers.CharField(required=False, allow_blank=False)
    input = serializers.JSONField(required=True)

    def validate_input(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("input must be a JSON object.")
        return value

    def _validate_rows(self, rows):
        if not isinstance(rows, list) or not rows:
            raise serializers.ValidationError({"input": "input.rows must be a non-empty array."})


class Model1PredictSerializer(_BaseModelPredictSerializer):
    def validate(self, attrs):
        input_payload = attrs.get("input") if isinstance(attrs.get("input"), dict) else {}
        rows = input_payload.get("rows")
        self._validate_rows(rows)

        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise serializers.ValidationError({"input": f"input.rows[{index}] must be an object."})
            if not row.get("facility_id"):
                raise serializers.ValidationError({"input": f"input.rows[{index}].facility_id is required."})
            if not row.get("resource_catalog_id"):
                raise serializers.ValidationError({"input": f"input.rows[{index}].resource_catalog_id is required."})

        return attrs


class Model2PredictSerializer(_BaseModelPredictSerializer):
    max_neighbors = serializers.IntegerField(min_value=1, default=20)

    def validate(self, attrs):
        input_payload = attrs.get("input") if isinstance(attrs.get("input"), dict) else {}
        rows = input_payload.get("rows")
        self._validate_rows(rows)

        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise serializers.ValidationError({"input": f"input.rows[{index}] must be an object."})
            if not row.get("facility_id"):
                raise serializers.ValidationError({"input": f"input.rows[{index}].facility_id is required."})

        neighbors = input_payload.get("neighbors", {})
        if neighbors is not None and not isinstance(neighbors, dict):
            raise serializers.ValidationError({"input": "input.neighbors must be an object when provided."})

        return attrs


class MLTrainingDatasetGenerateSerializer(serializers.Serializer):
    model_type = serializers.ChoiceField(choices=MLModelType.choices)
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    schema_version = serializers.CharField(required=False, allow_blank=True, default="v1")
    parameters = serializers.JSONField(required=False, default=dict)

    def validate_parameters(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("parameters must be a JSON object.")
        return value

    def validate(self, attrs):
        if attrs["date_to"] < attrs["date_from"]:
            raise serializers.ValidationError({"date_to": "date_to cannot be before date_from."})
        return attrs


class MLTrainingDatasetReviewSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class MLTrainingJobCreateSerializer(serializers.Serializer):
    model_type = serializers.ChoiceField(choices=MLModelType.choices, required=False)
    dataset_id = serializers.UUIDField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    parameters = serializers.JSONField(required=False, default=dict)

    def validate_parameters(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("parameters must be a JSON object.")
        return value

    def validate(self, attrs):
        dataset_id = attrs.get("dataset_id")
        date_from = attrs.get("date_from")
        date_to = attrs.get("date_to")
        model_type = attrs.get("model_type")

        if not dataset_id and (not date_from or not date_to or not model_type):
            raise serializers.ValidationError(
                {
                    "detail": (
                        "Provide dataset_id, or provide model_type with date_from/date_to."
                    )
                }
            )

        if date_from and date_to and date_to < date_from:
            raise serializers.ValidationError({"date_to": "date_to cannot be before date_from."})
        return attrs


class MLTrainingCallbackSerializer(serializers.Serializer):
    training_job_id = serializers.UUIDField(required=False)
    job_id = serializers.UUIDField(required=False)
    job_type = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=["trained", "completed", "failed"])
    external_job_id = serializers.CharField(required=False, allow_blank=True)
    version_name = serializers.CharField(required=False, allow_blank=True)
    model_version = serializers.CharField(required=False, allow_blank=True)
    approval_status = serializers.CharField(required=False, allow_blank=True)
    artifact_uri = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False, default=dict)
    metrics = serializers.JSONField(required=False, default=dict)
    error = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs):
        training_job_id = attrs.get("training_job_id") or attrs.get("job_id")
        if not training_job_id:
            raise serializers.ValidationError({"training_job_id": "training_job_id or job_id is required."})

        attrs["training_job_id"] = training_job_id
        return attrs


class MLModelVersionReviewSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class MLModelVersionRollbackSerializer(serializers.Serializer):
    target_version_id = serializers.UUIDField()


# ---------------------------------------------------------------------------
# Client-facing serializers — no raw ML feature fields, no scheduling fields.
# ---------------------------------------------------------------------------

class FacilityRefreshSerializer(serializers.Serializer):
    """Input for the on-demand facility refresh endpoint.

    The backend prepares all ML inputs internally; callers only specify what
    kind of refresh they want.
    """
    job_type = serializers.ChoiceField(
        choices=MLJob.JobType.choices,
        default=MLJob.JobType.FORECAST,
        required=False,
    )
    prediction_horizon_days = serializers.IntegerField(min_value=1, default=7, required=False)
