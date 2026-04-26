"""ML orchestration domain models."""
import uuid

from django.db import models
from django.utils import timezone


class MLModelType(models.TextChoices):
    MODEL1 = "model1", "Model 1"
    MODEL2 = "model2", "Model 2"


class MLJob(models.Model):
    class JobType(models.TextChoices):
        FORECAST = "forecast", "Forecast"
        OUTBREAK = "outbreak", "Outbreak"

    class ScopeType(models.TextChoices):
        FACILITY = "facility", "Facility"
        GLOBAL = "global", "Global"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SNAPSHOT_READY = "snapshot_ready", "Snapshot Ready"
        DISPATCHED = "dispatched", "Dispatched"
        RUNNING = "running", "Running"
        CALLBACK_RECEIVED = "callback_received", "Callback Received"
        COMPLETED = "completed", "Completed"
        PARTIAL_COMPLETED = "partial_completed", "Partial Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_type = models.CharField(max_length=20, choices=JobType.choices)
    scope_type = models.CharField(max_length=20, choices=ScopeType.choices, default=ScopeType.FACILITY)
    facility = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.PROTECT,
        related_name="ml_jobs",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING, db_index=True)
    scheduled_time = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    model_version = models.CharField(max_length=120, blank=True)
    parameters = models.JSONField(default=dict, blank=True)
    external_job_id = models.CharField(max_length=120, blank=True)
    callback_request_id = models.CharField(max_length=120, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    has_partial_failures = models.BooleanField(default=False)
    partial_failure_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=128, blank=True)
    payload_hash = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="ml_jobs_created",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ml_job"
        indexes = [
            models.Index(fields=["job_type", "status", "-created_at"]),
            models.Index(fields=["facility", "job_type", "-created_at"]),
            models.Index(fields=["scheduled_time"]),
            models.Index(fields=["status", "-updated_at"]),
        ]

    def __str__(self) -> str:
        return f"MLJob({self.id}, {self.job_type}, {self.status})"


class MLJobEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(MLJob, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=80)
    event_time = models.DateTimeField(default=timezone.now)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "ml_job_event"
        indexes = [
            models.Index(fields=["job", "-event_time"]),
            models.Index(fields=["event_type", "-event_time"]),
        ]


class MLJobIdempotency(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="ml_idempotency_records",
        null=True,
        blank=True,
    )
    endpoint = models.CharField(max_length=120)
    idempotency_key = models.CharField(max_length=128)
    payload_hash = models.CharField(max_length=64)
    response_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ml_job_idempotency"
        constraints = [
            models.UniqueConstraint(
                fields=["actor", "endpoint", "idempotency_key"],
                name="uniq_ml_job_idempotency",
            )
        ]
        indexes = [
            models.Index(fields=["endpoint", "-created_at"]),
        ]


class MLCallbackDedup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(MLJob, on_delete=models.CASCADE, related_name="callback_dedup_records")
    request_id = models.CharField(max_length=120)
    payload_hash = models.CharField(max_length=64)
    processed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ml_callback_dedup"
        constraints = [
            models.UniqueConstraint(fields=["job", "request_id"], name="uniq_ml_callback_dedup_key"),
        ]
        indexes = [
            models.Index(fields=["processed_at"]),
        ]


class FacilityMLSetting(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.OneToOneField(
        "hospitals.Hospital",
        on_delete=models.CASCADE,
        related_name="ml_setting",
    )
    max_neighbor_distance_km = models.DecimalField(max_digits=7, decimal_places=2, default=20)
    stock_threshold = models.PositiveIntegerField(default=20)
    outbreak_low_threshold = models.DecimalField(max_digits=5, decimal_places=4, default=0.4000)
    outbreak_high_threshold = models.DecimalField(max_digits=5, decimal_places=4, default=0.7000)
    notification_cooldown_minutes = models.PositiveIntegerField(default=60)
    max_active_jobs_per_type = models.PositiveIntegerField(default=3)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ml_facility_setting"


class MLSchedule(models.Model):
    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        CRON = "cron", "Cron"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_type = models.CharField(max_length=20, choices=MLJob.JobType.choices)
    facility = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.CASCADE,
        related_name="ml_schedules",
    )
    frequency = models.CharField(max_length=20, choices=Frequency.choices, default=Frequency.DAILY)
    run_time = models.TimeField(null=True, blank=True)
    cron_expression = models.CharField(max_length=120, blank=True)
    timezone = models.CharField(max_length=60, default="UTC")
    pre_run_offset_minutes = models.PositiveIntegerField(default=0)
    parameters = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="ml_schedules_created",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ml_schedule"
        indexes = [
            models.Index(fields=["facility", "job_type", "is_active"]),
            models.Index(fields=["is_active", "next_run_at"]),
        ]


class MLForecastResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(MLJob, on_delete=models.CASCADE, related_name="forecast_results")
    facility = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.CASCADE,
        related_name="ml_forecast_results",
    )
    resource_catalog = models.ForeignKey(
        "resources.ResourceCatalog",
        on_delete=models.CASCADE,
        related_name="ml_forecast_results",
    )
    prediction_horizon_days = models.PositiveIntegerField()
    predicted_demand = models.PositiveIntegerField()
    confidence_score = models.DecimalField(max_digits=5, decimal_places=4)
    shareable_quantity = models.IntegerField(default=0)
    restock = models.BooleanField(default=False)
    restock_amount = models.PositiveIntegerField(default=0)
    explanation = models.TextField(blank=True)
    decision_log = models.JSONField(default=dict, blank=True)
    request_candidates = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ml_forecast_result"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "facility", "resource_catalog"],
                name="uniq_ml_forecast_result_per_job_row",
            )
        ]
        indexes = [
            models.Index(fields=["facility", "-created_at"]),
            models.Index(fields=["job", "-created_at"]),
        ]


class MLOutbreakResult(models.Model):
    class RiskLevel(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(MLJob, on_delete=models.CASCADE, related_name="outbreak_results")
    facility = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.CASCADE,
        related_name="ml_outbreak_results",
    )
    prediction_horizon_days = models.PositiveIntegerField()
    outbreak_probability = models.DecimalField(max_digits=5, decimal_places=4)
    outbreak_flag = models.BooleanField(default=False)
    model_prob = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    final_confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    risk_level = models.CharField(max_length=10, choices=RiskLevel.choices)
    upazila = models.CharField(max_length=120, blank=True)
    ml_status = models.CharField(max_length=20, blank=True)
    likely_disease = models.CharField(max_length=200, blank=True)
    social_score = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    social_confirmation = models.CharField(max_length=300, blank=True)
    explanation = models.TextField(blank=True)
    decision_log = models.JSONField(default=dict, blank=True)
    neighbors = models.JSONField(default=list, blank=True)
    request_candidates = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ml_outbreak_result"
        constraints = [
            models.UniqueConstraint(fields=["job", "facility"], name="uniq_ml_outbreak_result_per_job_facility"),
        ]
        indexes = [
            models.Index(fields=["facility", "-created_at"]),
            models.Index(fields=["job", "-created_at"]),
        ]


class MLOutbreakNeighborCandidate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    outbreak_result = models.ForeignKey(
        MLOutbreakResult,
        on_delete=models.CASCADE,
        related_name="neighbor_candidates",
    )
    neighbor_facility = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.CASCADE,
        related_name="outbreak_neighbor_candidates",
    )
    distance_km = models.DecimalField(max_digits=7, decimal_places=2)
    rank = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ml_outbreak_neighbor_candidate"
        constraints = [
            models.UniqueConstraint(
                fields=["outbreak_result", "neighbor_facility"],
                name="uniq_ml_outbreak_neighbor_per_result",
            )
        ]
        indexes = [
            models.Index(fields=["outbreak_result", "rank"]),
        ]


class MLDispenseLog(models.Model):
    class SourceType(models.TextChoices):
        API = "api", "API"
        CSV_UPLOAD = "csv_upload", "CSV Upload"
        MANUAL = "manual", "Manual"
        INTERNAL_SALE = "internal_sale", "Internal Sale"
        DERIVED = "derived", "Derived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.CASCADE,
        related_name="ml_dispense_logs",
    )
    resource_catalog = models.ForeignKey(
        "resources.ResourceCatalog",
        on_delete=models.CASCADE,
        related_name="ml_dispense_logs",
    )
    event_date = models.DateField(db_index=True)
    quantity_sold = models.PositiveIntegerField()
    source_type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.API)
    source_endpoint = models.CharField(max_length=300, blank=True)
    external_event_id = models.CharField(max_length=160, blank=True)
    payload_hash = models.CharField(max_length=64)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ml_dispense_log"
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "external_event_id"],
                condition=~models.Q(external_event_id=""),
                name="uniq_ml_dispense_log_external_id",
            ),
            models.UniqueConstraint(
                fields=["facility", "payload_hash"],
                name="uniq_ml_dispense_log_payload_hash",
            ),
        ]
        indexes = [
            models.Index(fields=["facility", "event_date"]),
            models.Index(fields=["resource_catalog", "event_date"]),
            models.Index(fields=["source_type", "event_date"]),
        ]

    def __str__(self) -> str:
        return f"MLDispenseLog({self.facility_id}, {self.resource_catalog_id}, {self.event_date})"


class MLResultRowError(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(MLJob, on_delete=models.CASCADE, related_name="row_errors")
    job_type = models.CharField(max_length=20)
    source_row_index = models.PositiveIntegerField(default=0)
    facility_identifier = models.CharField(max_length=120, blank=True)
    resource_identifier = models.CharField(max_length=120, blank=True)
    error_code = models.CharField(max_length=80)
    error_message = models.TextField()
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ml_result_row_error"
        indexes = [
            models.Index(fields=["job", "-created_at"]),
            models.Index(fields=["error_code", "-created_at"]),
        ]


class MLTrainingDatasetSnapshot(models.Model):
    class ApprovalStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model_type = models.CharField(max_length=20, choices=MLModelType.choices)
    date_from = models.DateField()
    date_to = models.DateField()
    row_count = models.PositiveIntegerField(default=0)
    schema_version = models.CharField(max_length=40, default="v1")
    snapshot_prefix = models.CharField(max_length=255, blank=True)
    manifest = models.JSONField(default=dict, blank=True)
    parameters = models.JSONField(default=dict, blank=True)
    approval_status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
        db_index=True,
    )
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="ml_training_datasets_reviewed",
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="ml_training_datasets_created",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ml_training_dataset_snapshot"
        indexes = [
            models.Index(fields=["model_type", "-created_at"]),
            models.Index(fields=["approval_status", "-created_at"]),
        ]


class MLTrainingJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DISPATCHED = "dispatched", "Dispatched"
        RUNNING = "running", "Running"
        TRAINED = "trained", "Trained"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model_type = models.CharField(max_length=20, choices=MLModelType.choices)
    dataset_snapshot = models.ForeignKey(
        MLTrainingDatasetSnapshot,
        on_delete=models.PROTECT,
        related_name="training_jobs",
        null=True,
        blank=True,
    )
    date_from = models.DateField()
    date_to = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    parameters = models.JSONField(default=dict, blank=True)
    external_job_id = models.CharField(max_length=120, blank=True)
    model_version_name = models.CharField(max_length=120, blank=True)
    artifact_uri = models.CharField(max_length=300, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=128, blank=True)
    payload_hash = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="ml_training_jobs_created",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ml_training_job"
        indexes = [
            models.Index(fields=["model_type", "status", "-created_at"]),
        ]


class MLModelVersion(models.Model):
    class Status(models.TextChoices):
        TRAINED = "trained", "Trained"
        STORED = "stored", "Stored"
        REVIEWED = "reviewed", "Reviewed"
        APPROVED = "approved", "Approved"
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model_type = models.CharField(max_length=20, choices=MLModelType.choices)
    version_name = models.CharField(max_length=120)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.STORED, db_index=True)
    is_active = models.BooleanField(default=False)
    training_job = models.ForeignKey(
        MLTrainingJob,
        on_delete=models.SET_NULL,
        related_name="model_versions",
        null=True,
        blank=True,
    )
    artifact_uri = models.CharField(max_length=300, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    reviewed_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="ml_model_versions_reviewed",
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="ml_model_versions_approved",
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="ml_model_versions_activated",
        null=True,
        blank=True,
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ml_model_version"
        constraints = [
            models.UniqueConstraint(fields=["model_type", "version_name"], name="uniq_ml_model_version_name"),
            models.UniqueConstraint(
                fields=["model_type"],
                condition=models.Q(is_active=True),
                name="uniq_ml_model_active_version",
            ),
        ]
        indexes = [
            models.Index(fields=["model_type", "status", "-created_at"]),
        ]


class MLActiveModelConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model_type = models.CharField(max_length=20, choices=MLModelType.choices, unique=True)
    active_version = models.ForeignKey(
        MLModelVersion,
        on_delete=models.SET_NULL,
        related_name="active_model_configs",
        null=True,
        blank=True,
    )
    updated_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        related_name="ml_active_model_updates",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ml_active_model_config"
