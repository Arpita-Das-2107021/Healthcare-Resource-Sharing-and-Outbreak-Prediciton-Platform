"""Requests domain models: ResourceRequest, ResourceRequestApproval, DispatchEvent, DeliveryEvent, DeliveryToken."""
import uuid
from decimal import Decimal

from django.db import models
from django.utils import timezone


class RefundCause(models.TextChoices):
    # Requester-initiated
    REQUESTER_CANCELLATION = "REQUESTER_CANCELLATION", "Requester Cancellation"
    REQUESTER_DUPLICATE = "REQUESTER_DUPLICATE", "Requester Duplicate"
    # Supplier-initiated
    SUPPLIER_CANCELLATION = "SUPPLIER_CANCELLATION", "Supplier Cancellation"
    SUPPLIER_FAILURE = "SUPPLIER_FAILURE", "Supplier Failure"
    SUPPLIER_QUALITY_FAILURE = "SUPPLIER_QUALITY_FAILURE", "Supplier Quality Failure"
    SUPPLIER_NON_DELIVERY = "SUPPLIER_NON_DELIVERY", "Supplier Non-Delivery"
    # System/platform
    SYSTEM_TIMEOUT = "SYSTEM_TIMEOUT", "System Timeout"
    PAYMENT_GATEWAY_FAILURE = "PAYMENT_GATEWAY_FAILURE", "Payment Gateway Failure"
    INVENTORY_TRANSFER_FAILURE = "INVENTORY_TRANSFER_FAILURE", "Inventory Transfer Failure"
    # Compliance/fraud — these cause 0% refund
    FRAUD_SUSPECTED = "FRAUD_SUSPECTED", "Fraud Suspected"
    POLICY_VIOLATION = "POLICY_VIOLATION", "Policy Violation"
    # Operational
    LOGISTICS_FAILURE = "LOGISTICS_FAILURE", "Logistics Failure"
    FORCE_MAJEURE = "FORCE_MAJEURE", "Force Majeure"


class RefundStatus(models.TextChoices):
    PENDING_POLICY_EVALUATION = "PENDING_POLICY_EVALUATION", "Pending Policy Evaluation"
    PENDING_RETURN_VERIFICATION = "PENDING_RETURN_VERIFICATION", "Pending Return Verification"
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    REFUNDED = "REFUNDED", "Refunded"
    REFUND_FAILED = "REFUND_FAILED", "Refund Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class ResourceRequest(models.Model):
    """
    A hospital requesting a resource from another hospital.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        DISPATCHED = "dispatched", "Dispatched"
        FULFILLED = "fulfilled", "Fulfilled"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    class WorkflowState(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        RESERVED = "RESERVED", "Reserved"
        PAYMENT_PENDING = "PAYMENT_PENDING", "Payment Pending"
        PAYMENT_COMPLETED = "PAYMENT_COMPLETED", "Payment Completed"
        IN_TRANSIT = "IN_TRANSIT", "In Transit"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"
        EXPIRED = "EXPIRED", "Expired"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PENDING_MANUAL_VERIFICATION = "pending_manual_verification", "Pending Manual Verification"
        PAID = "paid", "Paid"
        PENDING = "PENDING", "Pending"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        REFUND_PENDING = "REFUND_PENDING", "Refund Pending"
        REFUNDED = "REFUNDED", "Refunded"
        REFUND_FAILED = "REFUND_FAILED", "Refund Failed"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requesting_hospital = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.PROTECT,
        related_name="outgoing_requests",
    )
    supplying_hospital = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.PROTECT,
        related_name="incoming_requests",
    )
    catalog_item = models.ForeignKey(
        "resources.ResourceCatalog",
        on_delete=models.PROTECT,
        related_name="requests",
    )
    quantity_requested = models.PositiveIntegerField()
    quantity_approved = models.PositiveIntegerField(null=True, blank=True)
    quantity_reserved = models.PositiveIntegerField(default=0)
    quantity_transferred = models.PositiveIntegerField(default=0)
    price_snapshot = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    workflow_state = models.CharField(
        max_length=30,
        choices=WorkflowState.choices,
        default=WorkflowState.PENDING,
        db_index=True,
    )
    allow_partial_fulfillment = models.BooleanField(default=False)
    payment_required = models.BooleanField(default=False)
    payment_status = models.CharField(
        max_length=40,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
    )
    payment_note = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    failed_reason = models.TextField(blank=True)
    deduplication_key = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL)
    notes = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        null=True,
        related_name="resource_requests_made",
    )
    needed_by = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "requests_resourcerequest"
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["workflow_state", "-created_at"]),
            models.Index(fields=["requesting_hospital", "status"]),
            models.Index(fields=["supplying_hospital", "status"]),
        ]

    def __str__(self) -> str:
        return f"Request({self.requesting_hospital}->{self.supplying_hospital}, {self.catalog_item}, {self.status})"


class ResourceRequestApproval(models.Model):
    """Record of who approved/rejected a request and why."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.OneToOneField(
        ResourceRequest,
        on_delete=models.CASCADE,
        related_name="approval",
    )
    reviewed_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        null=True,
        related_name="reviewed_requests",
    )
    decision = models.CharField(max_length=20, choices=[("approved", "Approved"), ("rejected", "Rejected")])
    quantity_approved = models.PositiveIntegerField(null=True, blank=True)
    reason = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "requests_approval"

    def __str__(self) -> str:
        return f"Approval({self.request}, {self.decision})"


class DispatchEvent(models.Model):
    """Logged when a request is physically dispatched."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.OneToOneField(
        ResourceRequest,
        on_delete=models.CASCADE,
        related_name="dispatch_event",
    )
    dispatched_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        null=True,
        related_name="dispatch_events",
    )
    shipment = models.ForeignKey(
        "shipments.Shipment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispatch_events",
    )
    notes = models.TextField(blank=True)
    dispatched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "requests_dispatchevent"


class DeliveryToken(models.Model):
    """
    One-time token the receiving hospital uses to confirm delivery.
    `token` stores only the SHA-256 hash of the raw token value.
    Expires after EXPIRY_HOURS.
    """

    EXPIRY_HOURS = 48

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.OneToOneField(
        ResourceRequest,
        on_delete=models.CASCADE,
        related_name="delivery_token",
    )
    shipment = models.ForeignKey(
        "shipments.Shipment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_tokens",
    )
    sender_user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_tokens_sent",
    )
    intended_receiver_user = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_tokens_intended",
    )
    token = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    workflow_status = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        db_table = "requests_deliverytoken"

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and timezone.now() < self.expires_at


class DeliveryEvent(models.Model):
    """Logged when a request is confirmed as delivered."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.OneToOneField(
        ResourceRequest,
        on_delete=models.CASCADE,
        related_name="delivery_event",
    )
    confirmed_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        null=True,
        related_name="delivery_events",
    )
    quantity_received = models.PositiveIntegerField()
    notes = models.TextField(blank=True)
    delivered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "requests_deliveryevent"


class ResourceRequestReservation(models.Model):
    class ReservationStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        RELEASED = "RELEASED", "Released"
        CONSUMED = "CONSUMED", "Consumed"
        EXPIRED = "EXPIRED", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        ResourceRequest,
        on_delete=models.CASCADE,
        related_name="reservations",
    )
    inventory_batch = models.ForeignKey(
        "resources.ResourceInventoryBatch",
        on_delete=models.CASCADE,
        related_name="request_reservations",
    )
    reserved_quantity = models.PositiveIntegerField()
    unit_price_at_reservation = models.DecimalField(max_digits=10, decimal_places=2)
    reservation_status = models.CharField(
        max_length=20,
        choices=ReservationStatus.choices,
        default=ReservationStatus.ACTIVE,
    )
    reserved_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    released_at = models.DateTimeField(null=True, blank=True)
    release_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "requests_resourcerequestreservation"
        indexes = [
            models.Index(fields=["request", "reservation_status"]),
            models.Index(fields=["inventory_batch", "reservation_status"]),
            models.Index(fields=["expires_at"]),
        ]


class ResourceRequestStateTransition(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        ResourceRequest,
        on_delete=models.CASCADE,
        related_name="state_transitions",
    )
    from_state = models.CharField(max_length=30)
    to_state = models.CharField(max_length=30)
    transition_reason = models.TextField(blank=True)
    performed_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        null=True,
        related_name="request_state_transitions",
    )
    performed_at = models.DateTimeField(default=timezone.now)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "requests_state_transition"
        indexes = [
            models.Index(fields=["request", "-performed_at"]),
            models.Index(fields=["to_state", "-performed_at"]),
        ]


class RequestOperationIdempotency(models.Model):
    class OperationType(models.TextChoices):
        RESERVATION_CREATE = "reservation_create", "Reservation Create"
        PAYMENT_INITIATE = "payment_initiate", "Payment Initiate"
        TRANSFER_CONFIRM = "transfer_confirm", "Transfer Confirm"
        REFUND_INITIATE = "refund_initiate", "Refund Initiate"
        REFUND_CONFIRM = "refund_confirm", "Refund Confirm"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        ResourceRequest,
        on_delete=models.CASCADE,
        related_name="idempotency_records",
    )
    operation_type = models.CharField(max_length=40, choices=OperationType.choices)
    idempotency_key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    response_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "requests_operation_idempotency"
        constraints = [
            models.UniqueConstraint(
                fields=["operation_type", "idempotency_key"],
                name="uniq_request_operation_idempotency",
            ),
        ]
        indexes = [
            models.Index(fields=["request", "operation_type", "-created_at"]),
        ]


class PaymentTransaction(models.Model):
    class Provider(models.TextChoices):
        SSLCOMMERZ = "SSLCOMMERZ", "SSLCOMMERZ"
        OTHER = "OTHER", "Other"

    class PaymentStatus(models.TextChoices):
        INITIATED = "INITIATED", "Initiated"
        PENDING = "PENDING", "Pending"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        REFUND_PENDING = "REFUND_PENDING", "Refund Pending"
        REFUNDED = "REFUNDED", "Refunded"
        REFUND_FAILED = "REFUND_FAILED", "Refund Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        ResourceRequest,
        on_delete=models.CASCADE,
        related_name="payment_transactions",
    )
    provider = models.CharField(max_length=30, choices=Provider.choices, default=Provider.SSLCOMMERZ)
    provider_transaction_id = models.CharField(max_length=150, null=True, blank=True)
    gateway_session_id = models.CharField(max_length=150, null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="BDT")
    payment_status = models.CharField(max_length=30, choices=PaymentStatus.choices, default=PaymentStatus.INITIATED)
    payer_hospital = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.PROTECT,
        related_name="payments_sent",
    )
    receiver_hospital = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.PROTECT,
        related_name="payments_received",
    )
    initiated_at = models.DateTimeField(default=timezone.now)
    authorized_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=80, blank=True)
    failure_message = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=128, unique=True)
    raw_gateway_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "requests_paymenttransaction"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_transaction_id"],
                condition=models.Q(provider_transaction_id__isnull=False),
                name="uniq_provider_transaction_id",
            ),
            # Prevents two active (non-terminal) payment rows for the same request+provider.
            models.UniqueConstraint(
                fields=["request", "provider"],
                condition=~models.Q(payment_status__in=["FAILED", "REFUNDED"]),
                name="uniq_payment_tx_one_active_per_request",
            ),
        ]
        indexes = [
            models.Index(fields=["payer_hospital", "-created_at"]),
            models.Index(fields=["receiver_hospital", "-created_at"]),
            models.Index(fields=["payment_status", "-created_at"]),
        ]


class PaymentLedgerEntry(models.Model):
    class EntryType(models.TextChoices):
        SENT = "SENT", "Sent"
        RECEIVED = "RECEIVED", "Received"
        REFUND_SENT = "REFUND_SENT", "Refund Sent"
        REFUND_RECEIVED = "REFUND_RECEIVED", "Refund Received"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment_transaction = models.ForeignKey(
        PaymentTransaction,
        on_delete=models.CASCADE,
        related_name="ledger_entries",
    )
    hospital = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.PROTECT,
        related_name="payment_ledger_entries",
    )
    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "requests_paymentledgerentry"


class PaymentGatewayWebhookEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=30)
    event_type = models.CharField(max_length=50)
    provider_transaction_id = models.CharField(max_length=150, null=True, blank=True)
    signature_valid = models.BooleanField(default=False)
    payload = models.JSONField(default=dict)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_status = models.CharField(max_length=20, default="PENDING")
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "requests_paymentwebhookevent"
        constraints = [
            # Prevents processing the same gateway event twice.
            models.UniqueConstraint(
                fields=["provider", "provider_transaction_id", "event_type"],
                condition=models.Q(provider_transaction_id__isnull=False),
                name="uniq_webhook_event_dedup",
            ),
        ]
        indexes = [
            models.Index(fields=["provider", "provider_transaction_id"]),
            models.Index(fields=["processing_status", "-created_at"]),
        ]


class PaymentReconciliationRun(models.Model):
    class RunStatus(models.TextChoices):
        RUNNING = "RUNNING", "Running"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=30, default=PaymentTransaction.Provider.SSLCOMMERZ)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    run_status = models.CharField(max_length=20, choices=RunStatus.choices, default=RunStatus.RUNNING)
    checked_count = models.IntegerField(default=0)
    corrected_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "requests_paymentreconciliationrun"
        indexes = [
            models.Index(fields=["provider", "-created_at"]),
            models.Index(fields=["run_status", "-created_at"]),
        ]


class InventoryDriftAlert(models.Model):
    class Severity(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"

    class AlertStatus(models.TextChoices):
        OPEN = "OPEN", "Open"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
        RESOLVED = "RESOLVED", "Resolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    hospital = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.CASCADE,
        related_name="inventory_drift_alerts",
    )
    resource_catalog = models.ForeignKey(
        "resources.ResourceCatalog",
        on_delete=models.CASCADE,
        related_name="inventory_drift_alerts",
    )
    request = models.ForeignKey(
        ResourceRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_drift_alerts",
    )
    expected_quantity = models.IntegerField()
    reported_quantity = models.IntegerField()
    drift_quantity = models.IntegerField()
    severity = models.CharField(max_length=20, choices=Severity.choices)
    alert_status = models.CharField(max_length=20, choices=AlertStatus.choices, default=AlertStatus.OPEN)
    detected_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "requests_inventorydriftalert"
        indexes = [
            models.Index(fields=["hospital", "alert_status", "-detected_at"]),
            models.Index(fields=["resource_catalog", "-detected_at"]),
        ]


class RequestWorkflowAuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        ResourceRequest,
        on_delete=models.CASCADE,
        related_name="workflow_audit_logs",
    )
    action_type = models.CharField(max_length=50)
    action_status = models.CharField(max_length=20)
    actor_type = models.CharField(max_length=20)
    actor_id = models.UUIDField(null=True, blank=True)
    correlation_id = models.CharField(max_length=128, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "requests_workflowauditlog"
        indexes = [
            models.Index(fields=["request", "-created_at"]),
            models.Index(fields=["action_type", "-created_at"]),
            models.Index(fields=["correlation_id"]),
        ]


class ExternalInventoryReservation(models.Model):
    class ReservationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RESERVED = "RESERVED", "Reserved"
        RELEASED = "RELEASED", "Released"
        CONFIRMED_TRANSFER = "CONFIRMED_TRANSFER", "Confirmed Transfer"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        ResourceRequest,
        on_delete=models.CASCADE,
        related_name="external_reservations",
    )
    supplying_hospital = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.CASCADE,
        related_name="external_inventory_reservations",
    )
    external_reservation_id = models.CharField(max_length=150)
    resource_catalog = models.ForeignKey(
        "resources.ResourceCatalog",
        on_delete=models.CASCADE,
        related_name="external_inventory_reservations",
    )
    reserved_quantity = models.PositiveIntegerField(default=0)
    reservation_status = models.CharField(max_length=20, choices=ReservationStatus.choices)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "requests_externalinventoryreservation"
        constraints = [
            models.UniqueConstraint(
                fields=["supplying_hospital", "external_reservation_id"],
                name="uniq_external_reservation_id_per_hospital",
            ),
        ]
        indexes = [
            models.Index(fields=["request", "reservation_status"]),
            models.Index(fields=["expires_at"]),
        ]


class ExternalInventoryAPICallLog(models.Model):
    class CallStatus(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        TIMEOUT = "TIMEOUT", "Timeout"
        FAILED = "FAILED", "Failed"
        RETRYING = "RETRYING", "Retrying"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    hospital = models.ForeignKey(
        "hospitals.Hospital",
        on_delete=models.CASCADE,
        related_name="external_inventory_api_logs",
    )
    request = models.ForeignKey(
        ResourceRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="external_inventory_api_logs",
    )
    operation = models.CharField(max_length=40)
    endpoint = models.CharField(max_length=500)
    http_method = models.CharField(max_length=10)
    request_payload = models.JSONField(default=dict, blank=True)
    response_status_code = models.IntegerField(null=True, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    timeout_ms = models.IntegerField(default=10000)
    retry_attempt = models.IntegerField(default=0)
    call_status = models.CharField(max_length=20, choices=CallStatus.choices)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "requests_externalinventoryapicalllog"
        indexes = [
            models.Index(fields=["hospital", "-created_at"]),
            models.Index(fields=["request", "-created_at"]),
            models.Index(fields=["operation", "-created_at"]),
        ]


class RefundPolicyRule(models.Model):
    """
    Configurable table mapping (workflow_state, cause) → refund outcome.
    NULL cause means "default rule for this stage".
    Lookup priority: exact (state, cause) > (state, NULL) > system fallback (0%, manual).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow_state = models.CharField(max_length=30)
    cause = models.CharField(max_length=50, null=True, blank=True)
    refund_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    requires_return_verification = models.BooleanField(default=False)
    gateway_supported = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "requests_refundpolicyrule"
        constraints = [
            models.UniqueConstraint(
                fields=["workflow_state", "cause"],
                condition=models.Q(is_active=True),
                name="uniq_refund_policy_rule_active",
            ),
        ]
        indexes = [
            models.Index(fields=["workflow_state", "cause"]),
        ]

    def __str__(self) -> str:
        return f"RefundPolicyRule({self.workflow_state}, {self.cause or 'default'}, {self.refund_percentage}%)"


class RefundRequest(models.Model):
    """
    Authoritative refund lifecycle entity. PaymentTransaction.payment_status is a summary mirror only.
    One active RefundRequest per PaymentTransaction is enforced by the partial unique index.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment_transaction = models.ForeignKey(
        PaymentTransaction,
        on_delete=models.CASCADE,
        related_name="refund_requests",
    )
    request = models.ForeignKey(
        ResourceRequest,
        on_delete=models.CASCADE,
        related_name="refund_requests",
    )
    cause = models.CharField(max_length=50, choices=RefundCause.choices)
    cause_note = models.TextField(blank=True)
    policy_rule = models.ForeignKey(
        RefundPolicyRule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="refund_requests",
    )
    refund_percentage_applied = models.DecimalField(max_digits=5, decimal_places=2)
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="BDT")
    refund_status = models.CharField(
        max_length=35,
        choices=RefundStatus.choices,
        default=RefundStatus.PENDING_POLICY_EVALUATION,
    )
    gateway_refund_id = models.CharField(max_length=150, blank=True)
    gateway_response = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(max_length=128, unique=True)
    initiated_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="initiated_refund_requests",
    )
    retry_count = models.IntegerField(default=0)
    sla_deadline_at = models.DateTimeField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    initiated_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=80, blank=True)
    failure_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Non-terminal statuses; a second RefundRequest for the same payment is
    # only allowed once all prior ones have reached REFUND_FAILED or CANCELLED.
    _ACTIVE_STATUSES = [
        RefundStatus.PENDING_POLICY_EVALUATION,
        RefundStatus.PENDING_RETURN_VERIFICATION,
        RefundStatus.PENDING,
        RefundStatus.PROCESSING,
    ]

    class Meta:
        db_table = "requests_refundrequest"
        constraints = [
            models.UniqueConstraint(
                fields=["payment_transaction"],
                condition=~models.Q(
                    refund_status__in=["REFUND_FAILED", "CANCELLED"]
                ),
                name="uniq_refund_request_one_active_per_payment",
            ),
        ]
        indexes = [
            models.Index(fields=["refund_status", "-created_at"]),
            models.Index(fields=["request", "-created_at"]),
            models.Index(fields=["sla_deadline_at"]),
        ]

    def __str__(self) -> str:
        return f"RefundRequest({self.request_id}, {self.cause}, {self.refund_status})"


class PartialReturnRecord(models.Model):
    """
    Tracks quantities returned, damaged, and lost for a physical return.
    Required before gateway refund is triggered when RefundPolicyRule.requires_return_verification=True.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        ResourceRequest,
        on_delete=models.CASCADE,
        related_name="partial_return_records",
    )
    refund_request = models.ForeignKey(
        RefundRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="partial_return_records",
    )
    shipment = models.ForeignKey(
        "shipments.Shipment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="partial_return_records",
    )
    quantity_returned = models.PositiveIntegerField(default=0)
    quantity_damaged = models.PositiveIntegerField(default=0)
    quantity_lost = models.PositiveIntegerField(default=0)
    return_verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(
        "authentication.UserAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_return_records",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_notes = models.TextField(blank=True)
    return_token_used = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "requests_partialreturnrecord"
        indexes = [
            models.Index(fields=["request", "return_verified"]),
            models.Index(fields=["refund_request"]),
        ]

    def __str__(self) -> str:
        return f"PartialReturnRecord({self.request_id}, returned={self.quantity_returned}, verified={self.return_verified})"
