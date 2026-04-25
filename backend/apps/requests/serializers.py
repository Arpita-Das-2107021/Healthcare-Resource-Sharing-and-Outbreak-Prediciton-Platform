"""Requests app serializers."""
from rest_framework import serializers

from .models import (
    DeliveryEvent,
    DispatchEvent,
    PaymentReconciliationRun,
    PaymentTransaction,
    RefundCause,
    RefundRequest,
    RefundStatus,
    ResourceRequest,
    ResourceRequestApproval,
)


class ResourceRequestSerializer(serializers.ModelSerializer):
    requesting_hospital_name = serializers.ReadOnlyField(source="requesting_hospital.name")
    supplying_hospital_name = serializers.ReadOnlyField(source="supplying_hospital.name")
    catalog_item_name = serializers.ReadOnlyField(source="catalog_item.name")
    latest_payment_id = serializers.SerializerMethodField()
    latest_payment_transaction_status = serializers.SerializerMethodField()
    shipment_id = serializers.SerializerMethodField()
    shipment_status = serializers.SerializerMethodField()
    completion_stage = serializers.SerializerMethodField()

    _NO_DISPATCH_EVENT = object()

    def _get_latest_payment_transaction(self, obj):
        cached = getattr(obj, "_latest_payment_transaction", None)
        if cached is not None:
            return cached

        tx = obj.payment_transactions.order_by("-created_at").first()
        obj._latest_payment_transaction = tx
        return tx

    def _get_dispatch_event(self, obj):
        cached = getattr(obj, "_dispatch_event", self._NO_DISPATCH_EVENT)
        if cached is not self._NO_DISPATCH_EVENT:
            return cached

        dispatch_event = None
        if hasattr(obj, "dispatch_event"):
            dispatch_event = obj.dispatch_event
        else:
            dispatch_event = DispatchEvent.objects.select_related("shipment").filter(request=obj).first()

        obj._dispatch_event = dispatch_event
        return dispatch_event

    def get_latest_payment_id(self, obj):
        tx = self._get_latest_payment_transaction(obj)
        return str(tx.id) if tx else None

    def get_latest_payment_transaction_status(self, obj):
        tx = self._get_latest_payment_transaction(obj)
        return tx.payment_status if tx else None

    def get_shipment_id(self, obj):
        dispatch_event = self._get_dispatch_event(obj)
        if not dispatch_event or not dispatch_event.shipment_id:
            return None
        return str(dispatch_event.shipment_id)

    def get_shipment_status(self, obj):
        dispatch_event = self._get_dispatch_event(obj)
        if not dispatch_event or dispatch_event.shipment is None:
            return None
        return dispatch_event.shipment.status

    def get_completion_stage(self, obj):
        dispatch_event = self._get_dispatch_event(obj)
        if not dispatch_event or dispatch_event.shipment is None:
            return None

        shipment = dispatch_event.shipment
        if shipment.receive_token_used_at is not None:
            return "RECEIVER_CONFIRMED"
        if shipment.dispatch_token_used_at is not None:
            return "SENDER_CONFIRMED"
        return None

    class Meta:
        model = ResourceRequest
        fields = (
            "id",
            "requesting_hospital",
            "requesting_hospital_name",
            "supplying_hospital",
            "supplying_hospital_name",
            "catalog_item",
            "catalog_item_name",
            "quantity_requested",
            "quantity_approved",
            "quantity_reserved",
            "quantity_transferred",
            "price_snapshot",
            "total_price",
            "status",
            "workflow_state",
            "shipment_id",
            "shipment_status",
            "completion_stage",
            "allow_partial_fulfillment",
            "payment_required",
            "payment_status",
            "payment_note",
            "latest_payment_id",
            "latest_payment_transaction_status",
            "expires_at",
            "expired_at",
            "cancellation_reason",
            "failed_reason",
            "deduplication_key",
            "priority",
            "notes",
            "requested_by",
            "needed_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "requesting_hospital_name",
            "supplying_hospital_name",
            "catalog_item_name",
            "quantity_approved",
            "quantity_reserved",
            "quantity_transferred",
            "price_snapshot",
            "total_price",
            "status",
            "workflow_state",
            "shipment_id",
            "shipment_status",
            "completion_stage",
            "payment_required",
            "payment_status",
            "payment_note",
            "latest_payment_id",
            "latest_payment_transaction_status",
            "requested_by",
            "expired_at",
            "cancellation_reason",
            "failed_reason",
            "created_at",
            "updated_at",
        )


class CreateRequestSerializer(serializers.Serializer):
    supplying_hospital = serializers.UUIDField()
    catalog_item = serializers.UUIDField()
    quantity_requested = serializers.IntegerField(min_value=1)
    priority = serializers.ChoiceField(choices=ResourceRequest.Priority.choices, default=ResourceRequest.Priority.NORMAL)
    deduplication_key = serializers.CharField(required=False, allow_blank=True, max_length=128)
    allow_partial_fulfillment = serializers.BooleanField(required=False, default=False)
    expires_in_minutes = serializers.IntegerField(required=False, min_value=1)
    notes = serializers.CharField(required=False, default="")
    needed_by = serializers.DateTimeField(required=False, allow_null=True)


class ApprovalSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approved", "rejected"])
    quantity_approved = serializers.IntegerField(min_value=0, required=False)
    allow_partial_fulfillment = serializers.BooleanField(required=False)
    waive_payment = serializers.BooleanField(required=False, default=False)
    reason = serializers.CharField(required=False, default="", allow_blank=True)


class ResourceRequestApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceRequestApproval
        fields = ("id", "request", "reviewed_by", "decision", "quantity_approved", "reason", "reviewed_at")
        read_only_fields = ("id", "reviewed_at")


class ConfirmDeliverySerializer(serializers.Serializer):
    qr_payload = serializers.CharField(required=False, allow_blank=True)
    qrPayload = serializers.CharField(required=False, allow_blank=True, write_only=True)
    quantity_received = serializers.IntegerField(min_value=1, required=False)
    notes = serializers.CharField(required=False, default="", allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("qr_payload"):
            attrs["qr_payload"] = attrs.get("qrPayload", "")
        attrs["qr_payload"] = str(attrs.get("qr_payload") or "").strip()
        if not attrs["qr_payload"]:
            raise serializers.ValidationError({"qrPayload": "qrPayload is required."})
        return attrs


class ConfirmPaymentSerializer(serializers.Serializer):
    payment_status = serializers.ChoiceField(choices=ResourceRequest.PaymentStatus.choices)
    payment_note = serializers.CharField(required=False, default="", allow_blank=True)


class VerifyReturnSerializer(serializers.Serializer):
    return_token = serializers.CharField()
    quantity_returned = serializers.IntegerField(required=False, min_value=0, allow_null=True)
    quantity_damaged = serializers.IntegerField(required=False, default=0, min_value=0)
    quantity_lost = serializers.IntegerField(required=False, default=0, min_value=0)
    verification_notes = serializers.CharField(required=False, default="", allow_blank=True)


class ReserveRequestSerializer(serializers.Serializer):
    strategy = serializers.ChoiceField(choices=["fefo"], default="fefo", required=False)
    requested_quantity = serializers.IntegerField(min_value=1, required=False)


class TransferConfirmSerializer(serializers.Serializer):
    qr_payload = serializers.CharField(required=False, allow_blank=True)
    qrPayload = serializers.CharField(required=False, allow_blank=True, write_only=True)
    quantity_received = serializers.IntegerField(min_value=1, required=False)
    notes = serializers.CharField(required=False, default="", allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("qr_payload"):
            attrs["qr_payload"] = attrs.get("qrPayload", "")
        attrs["qr_payload"] = str(attrs.get("qr_payload") or "").strip()
        if not attrs["qr_payload"]:
            raise serializers.ValidationError({"qrPayload": "qrPayload is required."})
        return attrs


class CancelRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, default="", allow_blank=True)


class ExpireRequestsSerializer(serializers.Serializer):
    limit = serializers.IntegerField(required=False, min_value=1, default=500)


class PaymentInitiateSerializer(serializers.Serializer):
    gateway = serializers.CharField(required=False, default="sslcommerz")
    reservation_timeout_minutes = serializers.IntegerField(required=False, min_value=1, default=30)
    return_url = serializers.URLField(required=False, allow_blank=True)
    cancel_url = serializers.URLField(required=False, allow_blank=True)


class PaymentConfirmSerializer(serializers.Serializer):
    payment_id = serializers.UUIDField(required=False)
    provider_transaction_id = serializers.CharField(required=False, allow_blank=True)
    payment_status = serializers.ChoiceField(choices=PaymentTransaction.PaymentStatus.choices)
    raw_payload = serializers.JSONField(required=False)


class RefundInitiateSerializer(serializers.Serializer):
    cause = serializers.ChoiceField(choices=RefundCause.choices)
    cause_note = serializers.CharField(required=False, default="", allow_blank=True, max_length=500)
    idempotency_key = serializers.CharField(required=False, default="", allow_blank=True, max_length=128)
    returned_quantity = serializers.IntegerField(required=False, min_value=0, allow_null=True)
    damaged_quantity = serializers.IntegerField(required=False, default=0, min_value=0)


class RefundConfirmSerializer(serializers.Serializer):
    provider_transaction_id = serializers.CharField(required=False, allow_blank=True)
    payment_status = serializers.ChoiceField(
        choices=[
            PaymentTransaction.PaymentStatus.REFUNDED,
            PaymentTransaction.PaymentStatus.REFUND_FAILED,
        ]
    )


class RefundRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = RefundRequest
        fields = [
            "id",
            "request",
            "payment_transaction",
            "cause",
            "cause_note",
            "policy_rule",
            "refund_percentage_applied",
            "refund_amount",
            "currency",
            "refund_status",
            "gateway_refund_id",
            "idempotency_key",
            "initiated_by",
            "retry_count",
            "sla_deadline_at",
            "escalated_at",
            "initiated_at",
            "processed_at",
            "completed_at",
            "failed_at",
            "failure_code",
            "failure_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PaymentTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentTransaction
        fields = [
            "id",
            "request",
            "provider",
            "provider_transaction_id",
            "gateway_session_id",
            "amount",
            "currency",
            "payment_status",
            "payer_hospital",
            "receiver_hospital",
            "initiated_at",
            "authorized_at",
            "completed_at",
            "failed_at",
            "failure_code",
            "failure_message",
            "idempotency_key",
            "raw_gateway_payload",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PaymentReconciliationRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentReconciliationRun
        fields = [
            "id",
            "provider",
            "started_at",
            "completed_at",
            "run_status",
            "checked_count",
            "corrected_count",
            "failed_count",
            "notes",
            "created_at",
        ]
        read_only_fields = fields


class DeliveryEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryEvent
        fields = ("id", "request", "confirmed_by", "quantity_received", "notes", "delivered_at")
        read_only_fields = ("id", "delivered_at")


class DispatchEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = DispatchEvent
        fields = ("id", "request", "dispatched_by", "shipment", "notes", "dispatched_at")
        read_only_fields = ("id", "dispatched_at")
