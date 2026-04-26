"""Requests app views."""
import logging
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions.base import (
    EnforceDomainContextMixin,
    IsHospitalAdmin,
    IsLogisticsStaff,
    IsVerifiedHospital,
    RequireHealthcareContext,
)
from common.permissions.runtime import has_any_permission, is_platform_operator
from common.utils.pagination import StandardResultsPagination
from common.utils.response import error_response, success_response

from .models import ResourceRequest
from .serializers import (
    ApprovalSerializer,
    CancelRequestSerializer,
    PaymentConfirmSerializer,
    ConfirmPaymentSerializer,
    CreateRequestSerializer,
    DispatchEventSerializer,
    ExpireRequestsSerializer,
    PaymentInitiateSerializer,
    PaymentReconciliationRunSerializer,
    RefundConfirmSerializer,
    RefundInitiateSerializer,
    RefundRequestSerializer,
    ReserveRequestSerializer,
    ResourceRequestSerializer,
    TransferConfirmSerializer,
    VerifyReturnSerializer,
)
from .models import RefundRequest
from .services import (
    approve_request,
    cancel_request,
    confirm_payment_transaction,
    confirm_payment,
    create_resource_request,
    dispatch_request,
    expire_requests,
    get_payment_report_summary,
    initiate_payment,
    initiate_refund,
    initiate_return,
    confirm_refund,
    reserve_request,
    process_sslcommerz_webhook,
    transfer_confirm,
    trigger_payment_reconciliation,
    verify_return,
)

logger = logging.getLogger("hrsp.requests")


def _coerce_public_browser_url(raw_url: str) -> str:
    candidate = str(raw_url or "").strip()
    if not candidate:
        return ""

    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def _resolve_payment_redirect_targets(request, return_url: str = "", cancel_url: str = "") -> tuple[str, str]:
    resolved_return_url = str(return_url or "").strip()
    resolved_cancel_url = str(cancel_url or "").strip()

    if resolved_return_url and resolved_cancel_url:
        return resolved_return_url, resolved_cancel_url

    header_return_url = ""
    for header_name in ("X-Frontend-Return-Url", "X-Frontend-Current-Url", "X-Current-Page-Url"):
        header_return_url = _coerce_public_browser_url(request.headers.get(header_name, ""))
        if header_return_url:
            break

    header_cancel_url = _coerce_public_browser_url(request.headers.get("X-Frontend-Cancel-Url", ""))
    referer_url = _coerce_public_browser_url(request.headers.get("Referer", ""))
    origin_url = _coerce_public_browser_url(request.headers.get("Origin", ""))
    configured_frontend_return_url = _coerce_public_browser_url(
        getattr(settings, "FRONTEND_PAYMENT_RETURN_URL", "")
    )
    configured_frontend_url = _coerce_public_browser_url(getattr(settings, "FRONTEND_URL", ""))

    inferred_page_url = (
        header_return_url
        or referer_url
        or configured_frontend_return_url
        or configured_frontend_url
        or origin_url
    )
    if not resolved_return_url:
        resolved_return_url = inferred_page_url

    if not resolved_cancel_url:
        resolved_cancel_url = header_cancel_url or resolved_return_url or inferred_page_url

    return resolved_return_url, resolved_cancel_url


def _ensure_permission(user, permission_codes):
    if has_any_permission(user, permission_codes, allow_role_fallback=False):
        return
    raise PermissionDenied("You do not have permission to perform this action.")


class ResourceRequestViewSet(EnforceDomainContextMixin, viewsets.ModelViewSet):
    serializer_class = ResourceRequestSerializer
    context_permission_class = RequireHealthcareContext
    permission_classes = [IsAuthenticated, IsVerifiedHospital]
    pagination_class = StandardResultsPagination

    def get_queryset(self):
        user = self.request.user
        base_qs = ResourceRequest.objects.select_related(
            "requesting_hospital",
            "supplying_hospital",
            "catalog_item",
            "dispatch_event__shipment",
        )
        if not user or not user.is_authenticated:
            return ResourceRequest.objects.none()
        if not has_any_permission(
            user,
            ("share.request.create", "share.request.approve", "hospital:request.view"),
            allow_role_fallback=False,
        ):
            return ResourceRequest.objects.none()
        if is_platform_operator(user, allow_role_fallback=False):
            return base_qs.all()
        if hasattr(user, "staff") and user.staff:
            hospital = user.staff.hospital
            return base_qs.filter(
                requesting_hospital=hospital
            ) | base_qs.filter(supplying_hospital=hospital)
        return ResourceRequest.objects.none()

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        return Response(success_response(data=self.get_serializer(qs, many=True).data))

    def retrieve(self, request, *args, **kwargs):
        return Response(success_response(data=self.get_serializer(self.get_object()).data))

    def create(self, request, *args, **kwargs):
        _ensure_permission(request.user, ("share.request.create",))
        s = CreateRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        hospital = request.user.staff.hospital if (hasattr(request.user, "staff") and request.user.staff) else None
        req = create_resource_request(hospital, s.validated_data, request.user)
        return Response(
            success_response(data=ResourceRequestSerializer(req).data),
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        return Response(success_response(data={"detail": "Use action endpoints to update state."}),
                        status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def destroy(self, request, *args, **kwargs):
        _ensure_permission(request.user, ("share.request.create",))
        req = self.get_object()
        cancel_reason = request.data.get("reason", "") if hasattr(request.data, "get") else ""
        cancel_request(req, request.user, reason=cancel_reason)
        return Response(success_response(data=ResourceRequestSerializer(req).data))

    @action(detail=True, methods=["post"], url_path="cancel", permission_classes=[IsAuthenticated, IsHospitalAdmin])
    def cancel_action(self, request, pk=None):
        _ensure_permission(request.user, ("share.request.create",))
        req = self.get_object()
        serializer = CancelRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated_req = cancel_request(req=req, actor=request.user, reason=serializer.validated_data.get("reason", ""))
        return Response(success_response(data=ResourceRequestSerializer(updated_req).data))

    @action(detail=True, methods=["post"], url_path="returns/initiate", permission_classes=[IsAuthenticated, IsHospitalAdmin])
    def initiate_return_action(self, request, pk=None):
        _ensure_permission(request.user, ("share.request.create",))
        req = self.get_object()
        serializer = CancelRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = initiate_return(req=req, actor=request.user, reason=serializer.validated_data.get("reason", ""))
        return Response(success_response(data=payload))

    @action(detail=True, methods=["post"], url_path="returns/verify", permission_classes=[IsAuthenticated, IsLogisticsStaff])
    def verify_return_alias_action(self, request, pk=None):
        _ensure_permission(request.user, ("share.request.approve",))
        req = self.get_object()
        serializer = VerifyReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        verify_return(
            req=req,
            return_token=d["return_token"],
            actor=request.user,
            quantity_returned=d.get("quantity_returned"),
            quantity_damaged=d.get("quantity_damaged", 0),
            quantity_lost=d.get("quantity_lost", 0),
            verification_notes=d.get("verification_notes", ""),
        )
        return Response(success_response(data=ResourceRequestSerializer(req).data))

    @action(detail=True, methods=["post"], url_path="approve", permission_classes=[IsAuthenticated, IsHospitalAdmin])
    def approve(self, request, pk=None):
        _ensure_permission(request.user, ("share.request.approve",))
        req = self.get_object()
        s = ApprovalSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        updated_req = approve_request(
            req=req,
            decision=d["decision"],
            quantity_approved=d.get("quantity_approved"),
            reason=d.get("reason", ""),
            waive_payment=d.get("waive_payment", False),
            actor=request.user,
        )
        return Response(success_response(data=ResourceRequestSerializer(updated_req).data))

    @action(detail=True, methods=["post"], url_path="dispatch", permission_classes=[IsAuthenticated, IsLogisticsStaff])
    def dispatch_action(self, request, pk=None):
        _ensure_permission(request.user, ("share.request.approve",))
        req = self.get_object()
        dispatch_event = dispatch_request(req=req, actor=request.user, notes=request.data.get("notes", ""))
        payload = DispatchEventSerializer(dispatch_event).data
        qr_payload = getattr(dispatch_event, "delivery_qr_payload", None)
        if qr_payload:
            payload["delivery_qr"] = qr_payload
        return Response(success_response(data=payload))

    @action(detail=False, methods=["post"], url_path="confirm-delivery", permission_classes=[IsAuthenticated])
    def confirm_delivery_action(self, request):
        return Response(
            error_response(
                code="gone",
                message="Legacy endpoint removed. Use /api/v1/requests/{id}/transfer-confirm/.",
            ),
            status=status.HTTP_410_GONE,
        )

    @action(detail=True, methods=["post"], url_path="confirm-payment", permission_classes=[IsAuthenticated])
    def confirm_payment_action(self, request, pk=None):
        _ensure_permission(request.user, ("hospital:payment.confirm",))
        req = self.get_object()
        s = ConfirmPaymentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        updated_req = confirm_payment(
            req=req,
            payment_status=d["payment_status"],
            payment_note=d.get("payment_note", ""),
            actor=request.user,
        )
        return Response(success_response(data=ResourceRequestSerializer(updated_req).data))

    @action(detail=True, methods=["post"], url_path="verify-return", permission_classes=[IsAuthenticated, IsLogisticsStaff])
    def verify_return_action(self, request, pk=None):
        _ensure_permission(request.user, ("share.request.approve",))
        req = self.get_object()
        s = VerifyReturnSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        verify_return(
            req=req,
            return_token=d["return_token"],
            actor=request.user,
            quantity_returned=d.get("quantity_returned"),
            quantity_damaged=d.get("quantity_damaged", 0),
            quantity_lost=d.get("quantity_lost", 0),
            verification_notes=d.get("verification_notes", ""),
        )
        return Response(success_response(data=ResourceRequestSerializer(req).data))

    @action(detail=True, methods=["post"], url_path="reserve", permission_classes=[IsAuthenticated, IsHospitalAdmin])
    def reserve_action(self, request, pk=None):
        _ensure_permission(request.user, ("share.request.approve",))
        req = self.get_object()
        serializer = ReserveRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = reserve_request(
            req=req,
            actor=request.user,
            requested_quantity=serializer.validated_data.get("requested_quantity"),
            strategy=serializer.validated_data.get("strategy", "fefo"),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
        )
        return Response(success_response(data=payload))

    @action(detail=True, methods=["post"], url_path="transfer-confirm", permission_classes=[IsAuthenticated])
    def transfer_confirm_action(self, request, pk=None):
        _ensure_permission(request.user, ("share.request.approve",))
        req = self.get_object()
        serializer = TransferConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        payload = transfer_confirm(
            req=req,
            actor=request.user,
            qr_payload=data["qr_payload"],
            quantity_received=data.get("quantity_received"),
            notes=data.get("notes", ""),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
        )
        return Response(success_response(data=payload))

    @action(detail=True, methods=["post"], url_path="payments/initiate", permission_classes=[IsAuthenticated, IsHospitalAdmin])
    def initiate_payment_action(self, request, pk=None):
        _ensure_permission(request.user, ("hospital:payment.initiate",))
        req = self.get_object()
        serializer = PaymentInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return_url, cancel_url = _resolve_payment_redirect_targets(
            request,
            return_url=data.get("return_url", ""),
            cancel_url=data.get("cancel_url", ""),
        )
        payload = initiate_payment(
            req=req,
            actor=request.user,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            gateway=data.get("gateway", "sslcommerz"),
            reservation_timeout_minutes=data.get("reservation_timeout_minutes", 30),
            return_url=return_url,
            cancel_url=cancel_url,
            callback_base_url=request.build_absolute_uri("/"),
        )
        return Response(success_response(data=payload))

    @action(detail=True, methods=["post"], url_path="payments/confirm", permission_classes=[IsAuthenticated, IsHospitalAdmin])
    def confirm_payment_transaction_action(self, request, pk=None):
        _ensure_permission(request.user, ("hospital:payment.confirm",))
        req = self.get_object()
        serializer = PaymentConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        payload = confirm_payment_transaction(
            req=req,
            actor=request.user,
            payment_status=data["payment_status"],
            payment_id=data.get("payment_id"),
            provider_transaction_id=data.get("provider_transaction_id", ""),
            raw_payload=data.get("raw_payload", {}),
        )
        return Response(success_response(data=payload))

    @action(detail=True, methods=["post"], url_path="refunds/initiate", permission_classes=[IsAuthenticated, IsHospitalAdmin])
    def initiate_refund_action(self, request, pk=None):
        _ensure_permission(request.user, ("hospital:payment.refund.initiate",))
        req = self.get_object()
        serializer = RefundInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        payload = initiate_refund(
            req=req,
            actor=request.user,
            cause=d["cause"],
            cause_note=d.get("cause_note", ""),
            idempotency_key=d.get("idempotency_key", "") or request.headers.get("Idempotency-Key", ""),
            returned_quantity=d.get("returned_quantity"),
            damaged_quantity=d.get("damaged_quantity", 0),
        )
        return Response(success_response(data=payload))

    @action(detail=True, methods=["post"], url_path="refunds/confirm", permission_classes=[IsAuthenticated, IsHospitalAdmin])
    def confirm_refund_action(self, request, pk=None):
        _ensure_permission(request.user, ("hospital:payment.refund.confirm",))
        req = self.get_object()
        serializer = RefundConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        payload = confirm_refund(
            req=req,
            actor=request.user,
            payment_status=data["payment_status"],
            provider_transaction_id=data.get("provider_transaction_id", ""),
        )
        return Response(success_response(data=payload))

    @action(detail=True, methods=["get"], url_path="refunds/status", permission_classes=[IsAuthenticated, IsHospitalAdmin])
    def refund_status_action(self, request, pk=None):
        _ensure_permission(request.user, ("hospital:payment.refund.initiate", "hospital:payment.refund.confirm"))
        req = self.get_object()
        refund_req = (
            RefundRequest.objects.filter(request=req)
            .exclude(refund_status__in=["REFUND_FAILED", "CANCELLED"])
            .order_by("-created_at")
            .first()
        )
        if not refund_req:
            refund_req = RefundRequest.objects.filter(request=req).order_by("-created_at").first()
        if not refund_req:
            return Response(success_response(data=None, message="No refund request found for this request."))
        return Response(success_response(data=RefundRequestSerializer(refund_req).data))

    @action(detail=False, methods=["post"], url_path="expire", permission_classes=[IsAuthenticated, IsHospitalAdmin])
    def expire_action(self, request):
        _ensure_permission(request.user, ("hospital:request.expire",))
        serializer = ExpireRequestsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = expire_requests(
            limit=serializer.validated_data.get("limit", 500),
            actor=request.user,
        )
        return Response(success_response(data=payload))

    @action(detail=False, methods=["get"], url_path="payments/report", permission_classes=[IsAuthenticated, IsHospitalAdmin])
    def payment_report_action(self, request):
        _ensure_permission(request.user, ("hospital:payment.report.view", "reports:payment.view"))
        hospital_id = request.query_params.get("hospital_id")
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        payload = get_payment_report_summary(
            hospital_id=hospital_id,
            date_from=date_from,
            date_to=date_to,
            actor=request.user,
        )
        meta = {
            "generated_at": timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "record_count": len(payload.get("transactions", [])),
        }
        return Response(success_response(data=payload, message="Payment report fetched successfully", meta=meta))

    @action(detail=False, methods=["post"], url_path="payments/reconcile", permission_classes=[IsAuthenticated, IsHospitalAdmin])
    def reconcile_payments_action(self, request):
        _ensure_permission(request.user, ("hospital:payment.reconcile.manage",))
        run = trigger_payment_reconciliation(actor=request.user)
        return Response(success_response(data=PaymentReconciliationRunSerializer(run).data))


class SSLCommerzWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @staticmethod
    def _extract_callback_payload(request) -> dict:
        incoming = request.data
        if hasattr(incoming, "dict"):
            payload = incoming.dict()
        elif isinstance(incoming, dict):
            payload = incoming
        else:
            payload = {}

        # Some gateway/browser flows pass callback fields via query string.
        for key in (
            "status",
            "tran_id",
            "bank_tran_id",
            "val_id",
            "verify_sign",
            "verify_key",
            "value_a",
            "value_b",
            "value_c",
            "value_d",
            "amount",
            "currency",
        ):
            if key not in payload:
                query_value = request.query_params.get(key)
                if query_value is not None and str(query_value).strip() != "":
                    payload[key] = query_value

        return payload

    def post(self, request):
        payload = self._extract_callback_payload(request)

        result = process_sslcommerz_webhook(payload=payload, headers=request.headers)

        # Browser success/fail callbacks can request a redirect after reconciliation.
        redirect_flag = str(request.query_params.get("redirect", "")).strip().lower()
        redirect_url = str(result.get("redirect_url", "")).strip()
        if redirect_flag in {"1", "true", "yes"} and redirect_url:
            return HttpResponseRedirect(redirect_url)

        return Response(success_response(data=result), status=status.HTTP_200_OK)

    def get(self, request):
        payload = self._extract_callback_payload(request)
        result = process_sslcommerz_webhook(payload=payload, headers=request.headers)

        redirect_flag = str(request.query_params.get("redirect", "")).strip().lower()
        redirect_url = str(result.get("redirect_url", "")).strip()
        if redirect_flag in {"1", "true", "yes"} and redirect_url:
            return HttpResponseRedirect(redirect_url)

        return Response(success_response(data=result), status=status.HTTP_200_OK)

