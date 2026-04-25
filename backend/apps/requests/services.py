"""Requests service layer."""
import base64
import ipaddress
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from functools import wraps
from decimal import Decimal
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import requests as http_requests
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from common.services.workflow_lock import ensure_request_workflow_is_mutable
from common.permissions.runtime import has_any_permission
from common.utils.tokens import generate_hex_token
from apps.badges.events import (
    DispatchCompletedEvent,
    RequestApprovedEvent,
    RequestCancelledEvent,
    RequestCreatedEvent,
    RequestDispatchedEvent,
    RequestExpiredEvent,
    RequestRejectedEvent,
)
from apps.badges.publisher import publish_badge_event
from apps.resources.share_state import share_capacity_snapshot_for_catalog_item

from .models import (
    DeliveryEvent,
    DeliveryToken,
    DispatchEvent,
    PartialReturnRecord,
    PaymentGatewayWebhookEvent,
    PaymentLedgerEntry,
    PaymentReconciliationRun,
    PaymentTransaction,
    RefundCause,
    RefundPolicyRule,
    RefundRequest,
    RefundStatus,
    RequestOperationIdempotency,
    ResourceRequest,
    ResourceRequestApproval,
    ResourceRequestReservation,
    ResourceRequestStateTransition,
    RequestWorkflowAuditLog,
)

logger = logging.getLogger("hrsp.requests")


TERMINAL_REQUEST_STATUSES = {
    ResourceRequest.Status.REJECTED,
    ResourceRequest.Status.CANCELLED,
    ResourceRequest.Status.DELIVERED,
    ResourceRequest.Status.FULFILLED,
}


SLA_ACTIVE_WORKFLOW_STATES = {
    ResourceRequest.WorkflowState.PENDING,
    ResourceRequest.WorkflowState.APPROVED,
    ResourceRequest.WorkflowState.RESERVED,
    ResourceRequest.WorkflowState.PAYMENT_PENDING,
    ResourceRequest.WorkflowState.PAYMENT_COMPLETED,
    ResourceRequest.WorkflowState.IN_TRANSIT,
}


SLA_TERMINAL_WORKFLOW_STATES = {
    ResourceRequest.WorkflowState.COMPLETED,
    ResourceRequest.WorkflowState.CANCELLED,
    ResourceRequest.WorkflowState.EXPIRED,
    ResourceRequest.WorkflowState.FAILED,
}


REQUEST_EXPIRY_ACTIVE_WORKFLOW_STATES = (
    ResourceRequest.WorkflowState.PENDING,
    ResourceRequest.WorkflowState.APPROVED,
    ResourceRequest.WorkflowState.RESERVED,
    ResourceRequest.WorkflowState.PAYMENT_PENDING,
    ResourceRequest.WorkflowState.PAYMENT_COMPLETED,
)


REQUEST_COMPLETION_PERMISSION_CODES = (
    "share.request.approve",
)


DISALLOWED_PAYMENT_CALLBACK_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "backend",
    "host.docker.internal",
}


def _inventory_gateway(facility=None):
    from apps.inventory_module.services import get_inventory_gateway

    return get_inventory_gateway(facility=facility)


def _publish_badge_event_after_commit(event) -> None:
    def _publish() -> None:
        try:
            publish_badge_event(event)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to publish request badge event", extra={"event_id": event.event_id})

    transaction.on_commit(_publish)


def _map_status_to_workflow(status: str) -> str:
    mapping = {
        ResourceRequest.Status.PENDING: ResourceRequest.WorkflowState.PENDING,
        ResourceRequest.Status.APPROVED: ResourceRequest.WorkflowState.RESERVED,
        ResourceRequest.Status.REJECTED: ResourceRequest.WorkflowState.FAILED,
        ResourceRequest.Status.DISPATCHED: ResourceRequest.WorkflowState.IN_TRANSIT,
        ResourceRequest.Status.DELIVERED: ResourceRequest.WorkflowState.COMPLETED,
        ResourceRequest.Status.FULFILLED: ResourceRequest.WorkflowState.COMPLETED,
        ResourceRequest.Status.CANCELLED: ResourceRequest.WorkflowState.CANCELLED,
    }
    return mapping.get(status, ResourceRequest.WorkflowState.PENDING)


def _is_request_sla_active(req: ResourceRequest) -> bool:
    return req.workflow_state in SLA_ACTIVE_WORKFLOW_STATES


def _stop_request_sla_timer(req: ResourceRequest, *, stopped_at=None) -> list[str]:
    if _is_request_sla_active(req):
        return []

    stop_timestamp = stopped_at or timezone.now()
    update_fields = []

    if req.expires_at is not None:
        req.expires_at = None
        update_fields.append("expires_at")

    if req.workflow_state in SLA_TERMINAL_WORKFLOW_STATES and req.expired_at is None:
        req.expired_at = stop_timestamp
        update_fields.append("expired_at")

    return update_fields


def _stop_shipment_sla_timer(shipment) -> list[str]:
    if shipment is None:
        return []

    update_fields = []
    if getattr(shipment, "token_expires_at", None) is not None:
        shipment.token_expires_at = None
        update_fields.append("token_expires_at")
    return update_fields


def _is_payment_required(requesting_hospital, supplying_hospital) -> bool:
    req_class = getattr(requesting_hospital, "facility_classification", "GOVT")
    sup_class = getattr(supplying_hospital, "facility_classification", "GOVT")
    return not (req_class == "GOVT" and sup_class == "GOVT")


def _normalize_localhost_url_for_sslcommerz(raw_url: str) -> str:
    if not raw_url:
        return raw_url

    parsed = urlsplit(raw_url)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return raw_url

    rewrite_localhost = bool(getattr(settings, "SSLCZ_REWRITE_LOCALHOST", False))
    if not rewrite_localhost:
        return raw_url

    replacement_host = str(getattr(settings, "SSLCZ_LOCALHOST", "localhost")).strip() or "localhost"
    netloc = replacement_host
    if parsed.port and ":" not in replacement_host:
        netloc = f"{replacement_host}:{parsed.port}"

    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _ensure_payment_redirect_query_params(raw_url: str, request_id: str, status_hint: str) -> str:
    if not raw_url:
        return raw_url

    parsed = urlsplit(raw_url)
    query = parse_qs(parsed.query, keep_blank_values=True)

    if not query.get("payment_request_id"):
        query["payment_request_id"] = [str(request_id)]
    if not query.get("status"):
        query["status"] = [status_hint]

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query, doseq=True),
            parsed.fragment,
        )
    )


def _append_query_params(raw_url: str, params: dict[str, str]) -> str:
    if not raw_url:
        return raw_url

    parsed = urlsplit(raw_url)
    query = parse_qs(parsed.query, keep_blank_values=True)

    for key, value in (params or {}).items():
        key_text = str(key or "").strip()
        if not key_text:
            continue
        value_text = str(value or "").strip()
        if key_text not in query and value_text:
            query[key_text] = [value_text]

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query, doseq=True),
            parsed.fragment,
        )
    )


def _sslcommerz_base_url() -> str:
    test_mode = bool(getattr(settings, "SSLCZ_TESTMODE", True))
    if test_mode:
        return "https://sandbox.sslcommerz.com"
    return "https://securepay.sslcommerz.com"


def _is_non_public_callback_host(hostname: str) -> bool:
    host = str(hostname or "").strip().lower()
    if not host:
        return True
    if host in DISALLOWED_PAYMENT_CALLBACK_HOSTS:
        return True
    if host.endswith(".local") or host.endswith(".internal"):
        return True

    try:
        ip_addr = ipaddress.ip_address(host)
    except ValueError:
        return False

    return (
        ip_addr.is_loopback
        or ip_addr.is_private
        or ip_addr.is_link_local
        or ip_addr.is_unspecified
        or ip_addr.is_reserved
        or ip_addr.is_multicast
    )


def _is_ngrok_callback_candidate(raw_url: str) -> bool:
    candidate = str(raw_url or "").strip()
    if not candidate:
        return False

    candidate = _normalize_localhost_url_for_sslcommerz(candidate)
    host = str(urlsplit(candidate).hostname or "").strip().lower()
    return "ngrok" in host


def _discover_ngrok_public_base_url() -> str:
    ngrok_api_url = str(getattr(settings, "NGROK_API_URL", "")).strip()
    if not ngrok_api_url:
        return ""

    timeout_seconds = int(getattr(settings, "NGROK_API_TIMEOUT_SECONDS", 3) or 3)
    timeout_seconds = max(timeout_seconds, 1)

    try:
        response = http_requests.get(ngrok_api_url, timeout=timeout_seconds)
        response.raise_for_status()
        payload = response.json()
    except (http_requests.RequestException, ValueError) as exc:
        logger.debug("Unable to read ngrok tunnel metadata from %s: %s", ngrok_api_url, exc)
        return ""

    if not isinstance(payload, dict):
        return ""

    tunnels = payload.get("tunnels")
    if not isinstance(tunnels, list):
        return ""

    fallback_candidate = ""
    for tunnel in tunnels:
        if not isinstance(tunnel, dict):
            continue

        public_url = str(tunnel.get("public_url", "")).strip()
        if not public_url:
            continue

        parsed = urlsplit(public_url)
        if parsed.scheme not in {"http", "https"}:
            continue

        host = str(parsed.hostname or "").strip()
        if _is_non_public_callback_host(host):
            continue

        normalized_base = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        if parsed.scheme == "https":
            return normalized_base

        if not fallback_candidate:
            fallback_candidate = normalized_base

    return fallback_candidate


def _resolve_sslcommerz_public_base_url(preferred_base_url: str = "") -> str:
    ngrok_base_url = _discover_ngrok_public_base_url()
    payment_public_base_url = str(getattr(settings, "PAYMENT_PUBLIC_BASE_URL", "")).strip()
    legacy_callback_base_url = str(getattr(settings, "SSLCZ_CALLBACK_BASE_URL", "")).strip()

    candidates = [
        ("PAYMENT_PUBLIC_BASE_URL", payment_public_base_url),
        ("SSLCZ_CALLBACK_BASE_URL", legacy_callback_base_url),
        ("NGROK_API_URL", ngrok_base_url),
        ("request_host", str(preferred_base_url or "").strip()),
    ]

    # Ngrok URLs rotate frequently. If ngrok is used in env config, prefer the
    # currently active ngrok tunnel metadata over static ngrok URLs.
    prefer_dynamic_ngrok = _is_ngrok_callback_candidate(payment_public_base_url) or _is_ngrok_callback_candidate(
        legacy_callback_base_url
    )
    if prefer_dynamic_ngrok and ngrok_base_url:
        candidates = [
            ("NGROK_API_URL", ngrok_base_url),
            ("PAYMENT_PUBLIC_BASE_URL", payment_public_base_url),
            ("SSLCZ_CALLBACK_BASE_URL", legacy_callback_base_url),
            ("request_host", str(preferred_base_url or "").strip()),
        ]

    invalid_reasons = []
    for source_name, raw_candidate in candidates:
        if not raw_candidate:
            continue

        candidate = _normalize_localhost_url_for_sslcommerz(raw_candidate)
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"}:
            invalid_reasons.append(f"{source_name} must start with http:// or https://")
            continue

        host = str(parsed.hostname or "").strip()
        if _is_non_public_callback_host(host):
            invalid_reasons.append(f"{source_name} host '{host}' is not publicly reachable")
            continue

        normalized_base = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        return normalized_base

    raise ValidationError(
        {
            "detail": "Public payment callback URL is not configured.",
            "hint": (
                "Set PAYMENT_PUBLIC_BASE_URL to a tunnel/public backend domain for SSLCommerz callbacks "
                "or configure NGROK_API_URL for automatic ngrok tunnel discovery."
            ),
            "examples": [
                "https://abc123.ngrok-free.app",
                "https://api.example.com",
            ],
            "invalid_sources": invalid_reasons,
        }
    )


def _sslcommerz_callback_url(preferred_base_url: str = "") -> str:
    normalized_base = _resolve_sslcommerz_public_base_url(preferred_base_url)
    if not normalized_base.endswith("/"):
        normalized_base = f"{normalized_base}/"
    return urljoin(normalized_base, "api/v1/requests/payments/webhooks/sslcommerz/")


def _sslcommerz_create_session(
    payment: PaymentTransaction,
    req: ResourceRequest,
    return_url: str = "",
    cancel_url: str = "",
    callback_base_url: str = "",
) -> dict:
    store_id = str(getattr(settings, "SSLCZ_STORE_ID", "")).strip()
    store_password = str(getattr(settings, "SSLCZ_STORE_PASSWORD", "")).strip()
    if not store_id or not store_password:
        raise ValidationError({"detail": "SSLCommerz credentials are not configured."})

    callback_url = _sslcommerz_callback_url(callback_base_url)
    browser_callback_url = callback_url

    # Always route gateway callbacks to backend so payment state is reconciled server-side.
    # Frontend URLs are used only as post-processing redirect targets.
    callback_success_url = _ensure_payment_redirect_query_params(browser_callback_url, str(req.id), "success")
    callback_success_url = _append_query_params(callback_success_url, {"redirect": "1"})
    callback_fail_url = _ensure_payment_redirect_query_params(browser_callback_url, str(req.id), "failed")
    callback_fail_url = _append_query_params(callback_fail_url, {"redirect": "1"})
    callback_cancel_url = _ensure_payment_redirect_query_params(browser_callback_url, str(req.id), "cancel")
    callback_cancel_url = _append_query_params(callback_cancel_url, {"redirect": "1"})

    frontend_return_url = _normalize_localhost_url_for_sslcommerz(return_url) if str(return_url or "").strip() else ""
    frontend_cancel_url = _normalize_localhost_url_for_sslcommerz(cancel_url) if str(cancel_url or "").strip() else ""
    if frontend_return_url:
        frontend_return_url = _ensure_payment_redirect_query_params(frontend_return_url, str(req.id), "success")
    if frontend_cancel_url:
        frontend_cancel_url = _ensure_payment_redirect_query_params(frontend_cancel_url, str(req.id), "cancel")
    elif frontend_return_url:
        frontend_cancel_url = _ensure_payment_redirect_query_params(frontend_return_url, str(req.id), "cancel")

    tran_id = f"HRSP{payment.id.hex[:26]}"

    requester = req.requesting_hospital
    supplier = req.supplying_hospital

    init_payload = {
        "store_id": store_id,
        "store_passwd": store_password,
        "total_amount": str(payment.amount),
        "currency": payment.currency,
        "tran_id": tran_id,
        "success_url": callback_success_url,
        "fail_url": callback_fail_url,
        "cancel_url": callback_cancel_url,
        "ipn_url": callback_url,
        "shipping_method": "NO",
        "product_name": str(getattr(req.catalog_item, "name", "Resource Transfer") or "Resource Transfer"),
        "product_category": "Healthcare Resource",
        "product_profile": "general",
        "cus_name": str(getattr(requester, "name", "Requesting Hospital") or "Requesting Hospital"),
        "cus_email": str(getattr(requester, "email", "payments@hrsp.local") or "payments@hrsp.local"),
        "cus_add1": str(getattr(requester, "address", "N/A") or "N/A"),
        "cus_city": str(getattr(requester, "city", "Dhaka") or "Dhaka"),
        "cus_postcode": "1000",
        "cus_country": str(getattr(requester, "country", "Bangladesh") or "Bangladesh"),
        "cus_phone": str(getattr(requester, "phone", "01700000000") or "01700000000"),
        "ship_name": str(getattr(supplier, "name", "Supplying Hospital") or "Supplying Hospital"),
        "ship_add1": str(getattr(supplier, "address", "N/A") or "N/A"),
        "ship_city": str(getattr(supplier, "city", "Dhaka") or "Dhaka"),
        "ship_postcode": "1000",
        "ship_country": str(getattr(supplier, "country", "Bangladesh") or "Bangladesh"),
        "value_a": str(payment.id),
        "value_b": str(req.id),
        "value_c": str(req.requesting_hospital_id),
        "value_d": str(req.supplying_hospital_id),
    }

    timeout_seconds = int(getattr(settings, "SSLCZ_REQUEST_TIMEOUT_SECONDS", 20) or 20)
    endpoint = f"{_sslcommerz_base_url()}/gwprocess/v4/api.php"
    try:
        response = http_requests.post(endpoint, data=init_payload, timeout=timeout_seconds)
    except http_requests.RequestException as exc:
        logger.exception("SSLCommerz initiation request failed for request_id=%s", req.id)
        raise ValidationError({"detail": "Unable to reach SSLCommerz gateway."}) from exc

    try:
        gateway_payload = response.json()
    except ValueError:
        gateway_payload = {"raw": (response.text or "")[:2000]}

    if not isinstance(gateway_payload, dict):
        gateway_payload = {"raw": str(gateway_payload)}

    provider_status = str(gateway_payload.get("status", "")).strip().upper()
    redirect_url = str(gateway_payload.get("GatewayPageURL", "")).strip()
    if response.status_code >= 400 or provider_status not in {"SUCCESS", "SUCCESSFULL", "VALID"} or not redirect_url:
        provider_message = str(gateway_payload.get("failedreason", "")).strip() or "SSLCommerz initiation failed."
        raise ValidationError(
            {
                "detail": "SSLCommerz session creation failed.",
                "provider_status": provider_status or f"HTTP_{response.status_code}",
                "provider_message": provider_message,
            }
        )

    safe_request_payload = {key: value for key, value in init_payload.items() if key != "store_passwd"}
    safe_request_payload["frontend_return_url"] = frontend_return_url
    safe_request_payload["frontend_cancel_url"] = frontend_cancel_url
    return {
        "tran_id": tran_id,
        "session_key": str(gateway_payload.get("sessionkey", "")).strip(),
        "redirect_url": redirect_url,
        "request_payload": safe_request_payload,
        "response_payload": gateway_payload,
    }


def _resolve_sslcommerz_redirect_url(
    payment: PaymentTransaction,
    payment_status: str,
    callback_payload: dict,
) -> str:
    callback_status = str(callback_payload.get("status", "")).strip().upper()
    status_hint = str(callback_payload.get("status", "")).strip().lower() or "pending"

    raw_gateway_payload = payment.raw_gateway_payload if isinstance(payment.raw_gateway_payload, dict) else {}
    request_payload = raw_gateway_payload.get("request", {}) if isinstance(raw_gateway_payload.get("request"), dict) else {}

    success_target = str(request_payload.get("frontend_return_url", "")).strip()
    cancel_target = str(request_payload.get("frontend_cancel_url", "")).strip()

    if not success_target:
        success_target = str(raw_gateway_payload.get("return_url", "")).strip()
    if not cancel_target:
        cancel_target = str(raw_gateway_payload.get("cancel_url", "")).strip()

    configured_payment_return_url = str(getattr(settings, "FRONTEND_PAYMENT_RETURN_URL", "")).strip()
    if configured_payment_return_url:
        configured_payment_return_url = _normalize_localhost_url_for_sslcommerz(configured_payment_return_url)
        parsed_payment_return_url = urlsplit(configured_payment_return_url)
        if parsed_payment_return_url.scheme not in {"http", "https"} or not parsed_payment_return_url.netloc:
            configured_payment_return_url = ""

    configured_frontend_origin = str(getattr(settings, "FRONTEND_URL", "")).strip()
    if configured_frontend_origin:
        configured_frontend_origin = _normalize_localhost_url_for_sslcommerz(configured_frontend_origin)

    def _is_frontend_root_target(target_url: str) -> bool:
        candidate = str(target_url or "").strip()
        if not candidate or not configured_frontend_origin:
            return False

        normalized_candidate = _normalize_localhost_url_for_sslcommerz(candidate)
        candidate_parts = urlsplit(normalized_candidate)
        origin_parts = urlsplit(configured_frontend_origin)

        if candidate_parts.scheme != origin_parts.scheme or candidate_parts.netloc != origin_parts.netloc:
            return False

        return (candidate_parts.path or "/") == "/"

    if configured_payment_return_url:
        if not success_target or _is_frontend_root_target(success_target):
            success_target = configured_payment_return_url
        if not cancel_target or _is_frontend_root_target(cancel_target):
            cancel_target = configured_payment_return_url

    if payment_status == PaymentTransaction.PaymentStatus.SUCCESS:
        redirect_target = success_target
        status_hint = "success"
    elif payment_status == PaymentTransaction.PaymentStatus.FAILED:
        redirect_target = cancel_target or success_target
        if callback_status in {"FAILED", "INVALID"}:
            status_hint = "failed"
        elif callback_status in {"CANCELLED", "UNATTEMPTED", "EXPIRED"}:
            status_hint = "cancel"
        else:
            status_hint = "failed"
    else:
        redirect_target = cancel_target or success_target

    if not redirect_target:
        fallback_frontend_url = configured_payment_return_url or str(getattr(settings, "FRONTEND_URL", "")).strip()
        if not fallback_frontend_url:
            return ""

        normalized_fallback = _normalize_localhost_url_for_sslcommerz(fallback_frontend_url)
        parsed_fallback = urlsplit(normalized_fallback)
        if parsed_fallback.scheme not in {"http", "https"} or not parsed_fallback.netloc:
            return ""

        return _ensure_payment_redirect_query_params(normalized_fallback, str(payment.request_id), status_hint)

    normalized = _normalize_localhost_url_for_sslcommerz(redirect_target)
    return _ensure_payment_redirect_query_params(normalized, str(payment.request_id), status_hint)


def _validate_sslcommerz_callback_mapping(
    payment: PaymentTransaction,
    callback_payload: dict,
    validation_payload: dict | None = None,
) -> None:
    callback_payment_id = str(callback_payload.get("value_a", "")).strip()
    callback_request_id = str(callback_payload.get("value_b", "")).strip()
    callback_tran_id = str(callback_payload.get("tran_id", "")).strip()
    callback_val_id = str(callback_payload.get("val_id", "")).strip()
    expected_tran_id = str(payment.provider_transaction_id or "").strip()

    if callback_payment_id and callback_payment_id != str(payment.id):
        raise ValidationError(
            {
                "detail": "SSLCommerz callback payment mapping mismatch.",
                "expected_payment_id": str(payment.id),
                "callback_payment_id": callback_payment_id,
            }
        )

    if callback_request_id and callback_request_id != str(payment.request_id):
        raise ValidationError(
            {
                "detail": "SSLCommerz callback request mapping mismatch.",
                "expected_request_id": str(payment.request_id),
                "callback_request_id": callback_request_id,
            }
        )

    if expected_tran_id and not callback_tran_id:
        raise ValidationError({"detail": "SSLCommerz callback missing tran_id."})

    if expected_tran_id and callback_tran_id and callback_tran_id != expected_tran_id:
        raise ValidationError(
            {
                "detail": "SSLCommerz tran_id mismatch.",
                "expected_tran_id": expected_tran_id,
                "callback_tran_id": callback_tran_id,
            }
        )

    if not validation_payload:
        return

    validated_tran_id = str(validation_payload.get("tran_id", "")).strip()
    validated_val_id = str(validation_payload.get("val_id", "")).strip()
    validated_status = str(validation_payload.get("status", "")).strip().upper()

    if validated_status and validated_status not in {"VALID", "VALIDATED"}:
        raise ValidationError(
            {
                "detail": "SSLCommerz validation API returned non-valid status.",
                "validated_status": validated_status,
            }
        )

    if validated_tran_id and expected_tran_id and validated_tran_id != expected_tran_id:
        raise ValidationError(
            {
                "detail": "SSLCommerz validation tran_id mismatch.",
                "expected_tran_id": expected_tran_id,
                "validated_tran_id": validated_tran_id,
            }
        )

    if callback_tran_id and validated_tran_id and callback_tran_id != validated_tran_id:
        raise ValidationError(
            {
                "detail": "SSLCommerz callback and validation tran_id mismatch.",
                "callback_tran_id": callback_tran_id,
                "validated_tran_id": validated_tran_id,
            }
        )

    if callback_val_id and validated_val_id and callback_val_id != validated_val_id:
        raise ValidationError(
            {
                "detail": "SSLCommerz callback and validation val_id mismatch.",
                "callback_val_id": callback_val_id,
                "validated_val_id": validated_val_id,
            }
        )


def _sslcommerz_validate_session(val_id: str) -> dict:
    token = str(val_id or "").strip()
    if not token:
        raise ValidationError({"detail": "val_id is required for SSLCommerz validation."})

    store_id = str(getattr(settings, "SSLCZ_STORE_ID", "")).strip()
    store_password = str(getattr(settings, "SSLCZ_STORE_PASSWORD", "")).strip()
    if not store_id or not store_password:
        raise ValidationError({"detail": "SSLCommerz credentials are not configured."})

    timeout_seconds = int(getattr(settings, "SSLCZ_REQUEST_TIMEOUT_SECONDS", 20) or 20)
    endpoint = f"{_sslcommerz_base_url()}/validator/api/validationserverAPI.php"
    query_params = {
        "val_id": token,
        "store_id": store_id,
        "store_passwd": store_password,
        "v": "1",
        "format": "json",
    }
    try:
        response = http_requests.get(endpoint, params=query_params, timeout=timeout_seconds)
    except http_requests.RequestException as exc:
        logger.exception("SSLCommerz validation request failed for val_id=%s", token)
        raise ValidationError({"detail": "Unable to validate SSLCommerz transaction."}) from exc

    try:
        validation_payload = response.json()
    except ValueError:
        validation_payload = {"raw": (response.text or "")[:2000]}

    if not isinstance(validation_payload, dict):
        validation_payload = {"raw": str(validation_payload)}

    provider_status = str(validation_payload.get("status", "")).strip().upper()
    if response.status_code >= 400 or provider_status not in {"VALID", "VALIDATED"}:
        provider_message = str(validation_payload.get("status", "")).strip() or f"HTTP_{response.status_code}"
        raise ValidationError(
            {
                "detail": "SSLCommerz transaction validation failed.",
                "provider_status": provider_status or f"HTTP_{response.status_code}",
                "provider_message": provider_message,
            }
        )

    return validation_payload


def _map_sslcommerz_callback_to_payment_status(callback_status: str) -> str:
    normalized = str(callback_status or "").strip().upper()
    if normalized in {"VALID", "VALIDATED", "SUCCESS"}:
        return PaymentTransaction.PaymentStatus.SUCCESS
    if normalized in {"FAILED", "CANCELLED", "UNATTEMPTED", "EXPIRED", "INVALID"}:
        return PaymentTransaction.PaymentStatus.FAILED
    return PaymentTransaction.PaymentStatus.PENDING


def _payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _hash_delivery_token(token_value: str) -> str:
    return hashlib.sha256(str(token_value or "").encode("utf-8")).hexdigest()


def _delivery_qr_signature_secret() -> str:
    configured_secret = str(getattr(settings, "REQUEST_DELIVERY_QR_SIGNATURE_SECRET", "")).strip()
    if configured_secret:
        return configured_secret
    return str(getattr(settings, "SECRET_KEY", "")).strip()


def _invalid_qr_validation_error() -> ValidationError:
    return ValidationError({"detail": "Invalid or tampered QR code"})


def _sign_delivery_qr_payload(canonical_payload: str) -> str:
    secret = _delivery_qr_signature_secret()
    if not secret:
        raise ValidationError({"detail": "QR signing secret is not configured."})

    return hmac.new(
        secret.encode("utf-8"),
        canonical_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _encode_delivery_qr_payload(canonical_payload: str, signature: str) -> str:
    encoded = base64.urlsafe_b64encode(f"{canonical_payload}.{signature}".encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")


def _decode_delivery_qr_payload(qr_payload: str) -> tuple[str, str]:
    opaque_payload = str(qr_payload or "").strip()
    if not opaque_payload:
        raise ValidationError({"qrPayload": "qrPayload is required."})

    padded_payload = opaque_payload + ("=" * ((4 - len(opaque_payload) % 4) % 4))
    try:
        decoded_payload = base64.urlsafe_b64decode(padded_payload.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise _invalid_qr_validation_error() from exc

    if "." not in decoded_payload:
        raise _invalid_qr_validation_error()

    canonical_payload, signature = decoded_payload.rsplit(".", 1)
    if not canonical_payload or not signature:
        raise _invalid_qr_validation_error()

    return canonical_payload, signature


def _parse_delivery_qr_canonical_payload(canonical_payload: str) -> dict:
    parts = canonical_payload.split(":", 3)
    if len(parts) != 4:
        raise _invalid_qr_validation_error()

    request_id_raw, shipment_id_raw, receiver_user_id_raw, expires_at_raw = parts
    try:
        request_id = str(uuid.UUID(request_id_raw))
        shipment_id = str(uuid.UUID(shipment_id_raw))
        receiver_user_id = str(uuid.UUID(receiver_user_id_raw))
    except Exception as exc:  # noqa: BLE001
        raise _invalid_qr_validation_error() from exc

    expires_at = parse_datetime(expires_at_raw)
    if expires_at is None:
        raise _invalid_qr_validation_error()
    if timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at, timezone.get_current_timezone())

    return {
        "request_id": request_id,
        "shipment_id": shipment_id,
        "receiver_user_id": receiver_user_id,
        "expires_at": expires_at,
        "expires_at_raw": expires_at_raw,
        "canonical_payload": canonical_payload,
    }


def build_delivery_qr_payload(*, req: ResourceRequest, shipment_id, receiver_user_id, expires_at) -> dict:
    expires_at_iso = expires_at.isoformat()
    canonical_payload = f"{req.id}:{shipment_id}:{receiver_user_id}:{expires_at_iso}"
    signature = _sign_delivery_qr_payload(canonical_payload)
    return {
        "qrPayload": _encode_delivery_qr_payload(canonical_payload, signature),
        "expiresAt": expires_at_iso,
    }


def _normalize_qr_payload(qr_payload: str) -> str:
    return str(qr_payload or "").strip()


def _actor_is_super_admin(actor) -> bool:
    return has_any_permission(
        actor,
        (
            "platform:hospital.manage",
            "hospital:request.supervise",
        ),
        allow_role_fallback=False,
    )


def _actor_hospital_id(actor):
    return getattr(getattr(actor, "staff", None), "hospital_id", None)


def _ensure_supplier_hospital_actor(req: ResourceRequest, actor, action_name: str) -> None:
    if _actor_is_super_admin(actor):
        return
    actor_hospital_id = _actor_hospital_id(actor)
    if not actor_hospital_id or str(actor_hospital_id) != str(req.supplying_hospital_id):
        raise PermissionDenied(f"Only the supplying hospital can {action_name} this request.")


def _ensure_requesting_hospital_actor(req: ResourceRequest, actor, action_name: str) -> None:
    if _actor_is_super_admin(actor):
        return
    actor_hospital_id = _actor_hospital_id(actor)
    if not actor_hospital_id or str(actor_hospital_id) != str(req.requesting_hospital_id):
        raise PermissionDenied(f"Only the requesting hospital can {action_name} this request.")


def _ensure_cancel_actor(req: ResourceRequest, actor, action_name: str) -> None:
    if _actor_is_super_admin(actor):
        return
    actor_hospital_id = _actor_hospital_id(actor)
    if not actor_hospital_id:
        raise PermissionDenied(f"Only involved hospitals can {action_name} this request.")
    if str(actor_hospital_id) not in {str(req.requesting_hospital_id), str(req.supplying_hospital_id)}:
        raise PermissionDenied(f"Only involved hospitals can {action_name} this request.")


def _ensure_completion_permission(actor) -> None:
    if has_any_permission(
        actor,
        REQUEST_COMPLETION_PERMISSION_CODES,
        allow_role_fallback=False,
    ):
        return
    raise PermissionDenied("You do not have permission to confirm request completion.")


def _resolve_dispatch_context(qr_payload: str, req: ResourceRequest | None = None):
    canonical_payload, provided_signature = _decode_delivery_qr_payload(qr_payload)
    expected_signature = _sign_delivery_qr_payload(canonical_payload)
    if not hmac.compare_digest(provided_signature.lower(), expected_signature.lower()):
        raise _invalid_qr_validation_error()

    qr_details = _parse_delivery_qr_canonical_payload(canonical_payload)
    if timezone.now() >= qr_details["expires_at"]:
        raise ValidationError({"detail": "QR code has expired."})

    if req is not None and str(req.id) != qr_details["request_id"]:
        raise _invalid_qr_validation_error()

    delivery_token_qs = DeliveryToken.objects.select_related(
        "request",
        "shipment",
        "intended_receiver_user",
    ).filter(request_id=qr_details["request_id"])
    if req is not None:
        delivery_token_qs = delivery_token_qs.filter(request=req)

    delivery_token = delivery_token_qs.first()
    if delivery_token is None:
        raise NotFound("Delivery workflow context not found.")

    dispatch_event = DispatchEvent.objects.select_related("request", "shipment").filter(
        request_id=qr_details["request_id"]
    ).first()
    if dispatch_event is None or dispatch_event.shipment is None:
        raise ValidationError({"detail": "No shipment found for this QR code."})

    if str(dispatch_event.shipment_id) != qr_details["shipment_id"]:
        raise _invalid_qr_validation_error()

    if delivery_token.shipment_id and str(delivery_token.shipment_id) != qr_details["shipment_id"]:
        raise _invalid_qr_validation_error()

    if (
        delivery_token.intended_receiver_user_id
        and str(delivery_token.intended_receiver_user_id) != qr_details["receiver_user_id"]
    ):
        raise _invalid_qr_validation_error()

    return dispatch_event, delivery_token, qr_details


def _build_completion_payload(
    req: ResourceRequest,
    shipment,
    *,
    completion_stage: str,
    delivery_event: DeliveryEvent | None,
) -> dict:
    return {
        "request_id": str(req.id),
        "request_status": req.status,
        "workflow_state": req.workflow_state,
        "expires_at": req.expires_at.isoformat() if req.expires_at else None,
        "sla_active": _is_request_sla_active(req),
        "sla_end_time": req.expired_at.isoformat() if req.expired_at else None,
        "completion_stage": completion_stage,
        "delivery_event_id": str(delivery_event.id) if delivery_event else None,
        "shipment_id": str(shipment.id),
        "shipment_status": shipment.status,
        "sender_confirmed": bool(shipment.dispatch_token_used_at),
        "receiver_confirmed": bool(shipment.receive_token_used_at),
        "sender_confirmed_at": (
            shipment.dispatch_token_used_at.isoformat() if shipment.dispatch_token_used_at else None
        ),
        "receiver_confirmed_at": (
            shipment.receive_token_used_at.isoformat() if shipment.receive_token_used_at else None
        ),
    }


def _lock_and_validate_delivery_token(*, token_record: DeliveryToken | None, qr_details: dict) -> DeliveryToken:
    if token_record is None:
        raise ValidationError({"detail": "Delivery workflow context not found."})

    locked_token = DeliveryToken.objects.select_for_update().get(id=token_record.id)

    if str(locked_token.request_id) != str(qr_details.get("request_id", "")):
        raise _invalid_qr_validation_error()

    if locked_token.shipment_id and str(locked_token.shipment_id) != str(qr_details.get("shipment_id", "")):
        raise _invalid_qr_validation_error()

    if (
        locked_token.intended_receiver_user_id
        and str(locked_token.intended_receiver_user_id) != str(qr_details.get("receiver_user_id", ""))
    ):
        raise _invalid_qr_validation_error()

    if timezone.now() >= locked_token.expires_at:
        raise ValidationError({"detail": "QR code has expired."})

    qr_expires_at = qr_details.get("expires_at")
    if qr_expires_at and timezone.now() >= qr_expires_at:
        raise ValidationError({"detail": "QR code has expired."})

    if locked_token.used_at is not None:
        raise ValidationError({"detail": "QR already used."})

    if str(locked_token.workflow_status or "").upper() in {"COMPLETED", "CANCELLED", "FAILED", "EXPIRED"}:
        raise ValidationError({"detail": "Workflow already closed."})

    return locked_token


def _confirm_sender_completion_step(
    req: ResourceRequest,
    shipment,
    *,
    qr_details: dict,
    actor,
    delivery_token_record: DeliveryToken,
) -> dict:
    from apps.shipments.models import Shipment

    with transaction.atomic():
        locked_req = ResourceRequest.objects.select_for_update().get(id=req.id)
        locked_shipment = Shipment.objects.select_for_update().get(id=shipment.id)
        locked_delivery_token = _lock_and_validate_delivery_token(
            token_record=delivery_token_record,
            qr_details=qr_details,
        )
        ensure_request_workflow_is_mutable(locked_req)

        _ensure_supplier_hospital_actor(locked_req, actor, "confirm sender completion")

        if locked_req.status != ResourceRequest.Status.DISPATCHED:
            raise ValidationError({"detail": "Request is not in dispatched state."})

        if locked_shipment.dispatch_token_used_at is not None:
            raise ValidationError({"detail": "Sender completion already confirmed."})

        if locked_shipment.token_expires_at and timezone.now() >= locked_shipment.token_expires_at:
            raise ValidationError({"detail": "Dispatch token has expired."})

        locked_shipment.dispatch_token_used_at = timezone.now()
        locked_shipment.save(update_fields=["dispatch_token_used_at", "updated_at"])

        locked_delivery_token.workflow_status = locked_req.workflow_state
        locked_delivery_token.save(update_fields=["workflow_status"])

        _write_workflow_audit(
            locked_req,
            "sender_completion_confirmed",
            "success",
            actor,
            details={
                "shipment_id": str(locked_shipment.id),
                "delivery_token_id": str(locked_delivery_token.id),
            },
        )

    return _build_completion_payload(
        locked_req,
        locked_shipment,
        completion_stage="SENDER_CONFIRMED",
        delivery_event=None,
    )


def _confirm_receiver_completion_step(
    req: ResourceRequest,
    shipment,
    *,
    qr_details: dict,
    quantity_received: int | None,
    notes: str,
    actor,
    delivery_token_record: DeliveryToken,
) -> dict:
    from apps.shipments.models import Shipment, ShipmentTracking
    from apps.resources.models import ResourceCatalog, ResourceInventory, ResourceInventoryBatch, ResourceTransaction

    required_codes = ("share.request.approve", "inventory.batch.view", "inventory.cost.view")
    missing_codes = [
        code for code in required_codes if not has_any_permission(actor, (code,), allow_role_fallback=False)
    ]
    if missing_codes:
        raise PermissionDenied(
            f"Missing required permission(s): {', '.join(missing_codes)}."
        )

    with transaction.atomic():
        locked_req = ResourceRequest.objects.select_for_update().get(id=req.id)
        locked_shipment = Shipment.objects.select_for_update().get(id=shipment.id)
        locked_delivery_token = _lock_and_validate_delivery_token(
            token_record=delivery_token_record,
            qr_details=qr_details,
        )
        ensure_request_workflow_is_mutable(locked_req)

        expected_receiver_id = str(qr_details.get("receiver_user_id", ""))
        if str(actor.id) != expected_receiver_id:
            raise PermissionDenied("This package is not assigned to you")

        if (
            locked_delivery_token.intended_receiver_user_id
            and str(locked_delivery_token.intended_receiver_user_id) != str(actor.id)
        ):
            raise PermissionDenied("This package is not assigned to you")

        if str(locked_shipment.id) != str(qr_details.get("shipment_id", "")):
            raise _invalid_qr_validation_error()

        actor_hospital_id = _actor_hospital_id(actor)
        if not actor_hospital_id or str(actor_hospital_id) != str(locked_req.requesting_hospital_id):
            raise PermissionDenied("This delivery does not belong to your healthcare facility")

        if locked_req.status != ResourceRequest.Status.DISPATCHED:
            raise ValidationError({"detail": "Request is not in dispatched state."})

        if locked_shipment.token_expires_at and timezone.now() >= locked_shipment.token_expires_at:
            raise ValidationError({"detail": "Dispatch token has expired."})

        if locked_shipment.receive_token_used_at is not None:
            raise ValidationError({"detail": "Receiver completion already confirmed."})

        effective_quantity_received = quantity_received
        if effective_quantity_received is None:
            effective_quantity_received = (
                locked_req.quantity_reserved
                or locked_req.quantity_approved
                or locked_req.quantity_requested
            )

        effective_quantity_received = int(effective_quantity_received or 0)
        if effective_quantity_received <= 0:
            raise ValidationError({"detail": "quantity_received must be greater than zero."})

        approved_quantity = locked_req.quantity_approved or locked_req.quantity_requested
        if effective_quantity_received > approved_quantity:
            raise ValidationError({"detail": "Received quantity cannot exceed approved quantity."})

        max_transferable = locked_req.quantity_reserved or approved_quantity
        if effective_quantity_received > max_transferable:
            raise ValidationError({"detail": "Received quantity cannot exceed reserved quantity."})

        from_state = locked_req.workflow_state
        source_inventory = ResourceInventory.objects.select_for_update().select_related(
            "catalog_item",
            "catalog_item__resource_type",
        ).get(catalog_item_id=locked_req.catalog_item_id)

        _ensure_default_batch(source_inventory)
        source_batches = list(
            ResourceInventoryBatch.objects.select_for_update()
            .filter(inventory=source_inventory)
            .order_by("expires_at", "acquired_at")
        )
        _backfill_batch_reserved_from_inventory(source_inventory, source_batches)

        active_reservations = list(
            ResourceRequestReservation.objects.select_for_update().filter(
                request=locked_req,
                reservation_status=ResourceRequestReservation.ReservationStatus.ACTIVE,
            )
        )

        reserved_before_transfer = int(locked_req.quantity_reserved or 0)
        if reserved_before_transfer <= 0 and active_reservations:
            reserved_before_transfer = sum(int(r.reserved_quantity or 0) for r in active_reservations)
        if reserved_before_transfer <= 0:
            reserved_before_transfer = approved_quantity

        if effective_quantity_received > reserved_before_transfer:
            raise ValidationError({"detail": "Received quantity cannot exceed reserved quantity."})

        auto_sender_confirmed = False
        if locked_shipment.dispatch_token_used_at is None:
            auto_sender_confirmed = True
            locked_shipment.dispatch_token_used_at = timezone.now()

        residual_reserved = max(0, reserved_before_transfer - effective_quantity_received)
        _decrement_batch_reserved(source_batches, effective_quantity_received, consume_available=True)
        if residual_reserved > 0:
            _decrement_batch_reserved(source_batches, residual_reserved, consume_available=False)

        reservation_status = ResourceRequestReservation.ReservationStatus.CONSUMED
        reservation_updates = {"updated_at": timezone.now()}
        if residual_reserved > 0:
            reservation_status = ResourceRequestReservation.ReservationStatus.RELEASED
            reservation_updates.update(
                {
                    "released_at": timezone.now(),
                    "release_reason": "partial_transfer_release",
                }
            )

        ResourceRequestReservation.objects.filter(
            request=locked_req,
            reservation_status=ResourceRequestReservation.ReservationStatus.ACTIVE,
        ).update(
            reservation_status=reservation_status,
            **reservation_updates,
        )

        _sync_inventory_aggregate_from_batches(source_inventory, source_batches)
        _assert_inventory_batch_free_invariant(source_inventory, source_batches)

        ResourceTransaction.objects.create(
            inventory=source_inventory,
            transaction_type=ResourceTransaction.TransactionType.TRANSFER_OUT,
            quantity_delta=-effective_quantity_received,
            balance_after=source_inventory.quantity_available,
            reference_id=locked_req.id,
            notes="inventory_gateway_transfer_out",
            performed_by=actor,
        )

        catalog_item = source_inventory.catalog_item
        target_catalog, _ = ResourceCatalog.objects.get_or_create(
            hospital_id=locked_req.requesting_hospital_id,
            resource_type=catalog_item.resource_type,
            name=catalog_item.name,
            defaults={
                "unit_of_measure": catalog_item.unit_of_measure,
                "description": catalog_item.description,
            },
        )
        target_inventory, _ = ResourceInventory.objects.get_or_create(
            catalog_item=target_catalog,
            defaults={
                "quantity_available": 0,
                "price_per_unit": source_inventory.price_per_unit,
                "currency": source_inventory.currency,
                "reserved_quantity": 0,
                "quantity_reserved": 0,
            },
        )
        target_inventory = ResourceInventory.objects.select_for_update().get(pk=target_inventory.pk)

        transfer_batch, created_transfer_batch = ResourceInventoryBatch.objects.select_for_update().get_or_create(
            inventory=target_inventory,
            batch_number=f"TRANSFER-{locked_req.id}",
            defaults={
                "quantity_acquired": 0,
                "quantity_available_in_batch": 0,
                "quantity_reserved_in_batch": 0,
                "unit_price_at_acquisition": source_inventory.price_per_unit,
                "currency": source_inventory.currency,
                "manufacturer": "",
                "acquired_at": timezone.now(),
                "expires_at": None,
                "source_reference": f"request_transfer:{locked_req.id}",
            },
        )
        transfer_batch.quantity_acquired = int(transfer_batch.quantity_acquired or 0) + effective_quantity_received
        transfer_batch.quantity_available_in_batch = (
            int(transfer_batch.quantity_available_in_batch or 0) + effective_quantity_received
        )
        transfer_batch_update_fields = ["quantity_acquired", "quantity_available_in_batch", "updated_at"]
        if created_transfer_batch and transfer_batch.source_reference != f"request_transfer:{locked_req.id}":
            transfer_batch.source_reference = f"request_transfer:{locked_req.id}"
            transfer_batch_update_fields.append("source_reference")
        transfer_batch.save(update_fields=transfer_batch_update_fields)

        target_batches = list(
            ResourceInventoryBatch.objects.select_for_update()
            .filter(inventory=target_inventory)
            .order_by("expires_at", "acquired_at")
        )
        _sync_inventory_aggregate_from_batches(target_inventory, target_batches)
        _assert_inventory_batch_free_invariant(target_inventory, target_batches)

        ResourceTransaction.objects.create(
            inventory=target_inventory,
            transaction_type=ResourceTransaction.TransactionType.TRANSFER_IN,
            quantity_delta=effective_quantity_received,
            balance_after=target_inventory.quantity_available,
            reference_id=locked_req.id,
            notes="inventory_gateway_transfer_in",
            performed_by=actor,
        )

        transfer_result = {
            "source_inventory_id": str(source_inventory.id),
            "target_inventory_id": str(target_inventory.id),
        }

        source_inventory_snapshot = source_inventory
        receiving_inventory_snapshot = target_inventory

        delivery_event = DeliveryEvent.objects.create(
            request=locked_req,
            confirmed_by=actor,
            quantity_received=effective_quantity_received,
            notes=notes,
        )

        locked_shipment.receive_token_used_at = timezone.now()
        locked_shipment.status = Shipment.Status.DELIVERED
        locked_shipment.actual_delivery_at = timezone.now()
        shipment_update_fields = [
            "dispatch_token_used_at",
            "receive_token_used_at",
            "status",
            "actual_delivery_at",
            "updated_at",
        ]
        shipment_update_fields.extend(_stop_shipment_sla_timer(locked_shipment))
        locked_shipment.save(
            update_fields=shipment_update_fields
        )

        ShipmentTracking.objects.create(
            shipment=locked_shipment,
            status=Shipment.Status.DELIVERED,
            notes="delivery_verified",
            recorded_by=actor,
        )

        locked_req.status = ResourceRequest.Status.FULFILLED
        locked_req.workflow_state = ResourceRequest.WorkflowState.COMPLETED
        locked_req.quantity_reserved = 0
        locked_req.quantity_transferred = effective_quantity_received
        req_update_fields = ["status", "workflow_state", "quantity_reserved", "quantity_transferred", "updated_at"]
        req_update_fields.extend(_stop_request_sla_timer(locked_req))
        locked_req.save(update_fields=req_update_fields)

        locked_delivery_token.used_at = timezone.now()
        locked_delivery_token.workflow_status = locked_req.workflow_state
        locked_delivery_token.save(update_fields=["used_at", "workflow_status"])

        _write_transition(locked_req, from_state, locked_req.workflow_state, actor, reason="transfer_confirmed")
        _write_workflow_audit(
            locked_req,
            "transfer_confirmed",
            "success",
            actor,
            details={
                "quantity_received": effective_quantity_received,
                "quantity_released_after_transfer": residual_reserved,
                "source_inventory_id": transfer_result["source_inventory_id"],
                "target_inventory_id": transfer_result["target_inventory_id"],
                "delivery_token_id": str(locked_delivery_token.id),
                "auto_sender_confirmed": auto_sender_confirmed,
            },
        )

        _notify_inventory_update_after_commit(
            req=locked_req,
            hospital=locked_req.supplying_hospital,
            catalog_item=locked_req.catalog_item,
            inventory=source_inventory_snapshot,
            operation="transfer_confirmed_inventory",
            actor=actor,
            quantity_available_delta=-effective_quantity_received,
            quantity_reserved_delta=-(effective_quantity_received + residual_reserved),
            quantity_transferred=effective_quantity_received,
            metadata={"direction": "outbound"},
        )
        _notify_inventory_update_after_commit(
            req=locked_req,
            hospital=locked_req.requesting_hospital,
            catalog_item=locked_req.catalog_item,
            inventory=receiving_inventory_snapshot,
            operation="transfer_received_inventory",
            actor=actor,
            quantity_available_delta=effective_quantity_received,
            quantity_reserved_delta=0,
            quantity_transferred=effective_quantity_received,
            metadata={"direction": "inbound"},
        )

    return _build_completion_payload(
        locked_req,
        locked_shipment,
        completion_stage="RECEIVER_CONFIRMED",
        delivery_event=delivery_event,
    )


def confirm_workflow_completion(
    *,
    qr_payload: str,
    quantity_received: int | None,
    notes: str,
    actor,
    req: ResourceRequest | None = None,
) -> dict:
    _ensure_completion_permission(actor)

    dispatch_event, delivery_token_record, qr_details = _resolve_dispatch_context(qr_payload, req=req)
    resolved_req = dispatch_event.request
    shipment = dispatch_event.shipment

    if shipment is None:
        raise ValidationError({"detail": "Shipment missing for this request."})

    expected_receiver_id = str(qr_details.get("receiver_user_id", ""))
    if str(actor.id) != expected_receiver_id:
        raise PermissionDenied("This package is not assigned to you")

    actor_hospital_id = _actor_hospital_id(actor)
    is_requesting_actor = bool(actor_hospital_id and str(actor_hospital_id) == str(resolved_req.requesting_hospital_id))
    if not is_requesting_actor:
        raise PermissionDenied("This delivery does not belong to your healthcare facility")

    completion_payload = _confirm_receiver_completion_step(
        resolved_req,
        shipment,
        qr_details=qr_details,
        quantity_received=quantity_received,
        notes=notes,
        actor=actor,
        delivery_token_record=delivery_token_record,
    )

    if completion_payload.get("completion_stage") == "RECEIVER_CONFIRMED":
        _publish_badge_event_after_commit(
            DispatchCompletedEvent(
                event_id=f"dispatch-completed:{resolved_req.id}",
                request_id=str(resolved_req.id),
                requesting_hospital_id=str(resolved_req.requesting_hospital_id),
                supplying_hospital_id=str(resolved_req.supplying_hospital_id),
                completed_on=timezone.localdate(),
            )
        )
    return completion_payload


def _require_mutable_request_workflow(func):
    @wraps(func)
    def _wrapped(req: ResourceRequest, *args, **kwargs):
        ensure_request_workflow_is_mutable(req)
        return func(req, *args, **kwargs)

    return _wrapped


def _scoped_idempotency_key(req: ResourceRequest, idempotency_key: str) -> str:
    raw_key = str(idempotency_key or "").strip()
    if not raw_key:
        return ""

    scoped_key = f"{req.id}:{raw_key}"
    if len(scoped_key) <= 128:
        return scoped_key

    digest = hashlib.sha256(scoped_key.encode("utf-8")).hexdigest()
    return f"{str(req.id).replace('-', '')[:24]}:{digest}"


def _get_inventory_snapshot(facility_id, item_ref) -> dict:
    snapshot = _inventory_gateway().get_item_snapshot(facility_id, item_ref)
    if snapshot is None:
        raise NotFound("Inventory item not found for this facility.")
    return snapshot


def _write_transition(req: ResourceRequest, from_state: str, to_state: str, actor, reason: str = "", metadata=None):
    ResourceRequestStateTransition.objects.create(
        request=req,
        from_state=from_state,
        to_state=to_state,
        transition_reason=reason,
        performed_by=actor,
        metadata=metadata or {},
    )


def _write_workflow_audit(
    req: ResourceRequest,
    action_type: str,
    action_status: str,
    actor,
    details=None,
    correlation_id: str = "",
):
    RequestWorkflowAuditLog.objects.create(
        request=req,
        action_type=action_type,
        action_status=action_status,
        actor_type="user" if actor else "system",
        actor_id=getattr(actor, "id", None),
        correlation_id=correlation_id,
        details=details or {},
    )


def _get_existing_idempotent_response(req: ResourceRequest, operation_type: str, idempotency_key: str, payload: dict):
    raw_key = str(idempotency_key or "").strip()
    if not raw_key:
        return None

    scoped_key = _scoped_idempotency_key(req, raw_key)
    key_candidates = [scoped_key]
    if raw_key != scoped_key:
        key_candidates.append(raw_key)

    request_hash = _payload_hash(payload)
    existing = RequestOperationIdempotency.objects.filter(
        request=req,
        operation_type=operation_type,
        idempotency_key__in=key_candidates,
    ).order_by("-created_at").first()
    if not existing:
        return None
    if existing.request_hash != request_hash:
        raise ValidationError({"detail": "Idempotency key conflict for a different payload."})
    return existing.response_snapshot


def _save_idempotent_response(req: ResourceRequest, operation_type: str, idempotency_key: str, payload: dict, response_snapshot: dict):
    scoped_key = _scoped_idempotency_key(req, idempotency_key)
    if not scoped_key:
        return

    RequestOperationIdempotency.objects.update_or_create(
        request=req,
        operation_type=operation_type,
        idempotency_key=scoped_key,
        defaults={
            "request_hash": _payload_hash(payload),
            "response_snapshot": response_snapshot,
            "expires_at": timezone.now() + timedelta(days=2),
        },
    )


def _build_inventory_update_payload(
    *,
    req: ResourceRequest,
    hospital,
    catalog_item,
    inventory,
    operation: str,
    actor,
    quantity_available_delta: int = 0,
    quantity_reserved_delta: int = 0,
    quantity_transferred: int = 0,
    metadata: dict | None = None,
) -> dict:
    # Transitional compatibility: support both ORM inventory objects and gateway snapshot dicts.
    inventory_id = str(getattr(inventory, "id", "")) if not isinstance(inventory, dict) else str(inventory.get("inventory_id", ""))
    catalog_item_id = str(catalog_item.id)
    if isinstance(inventory, dict) and inventory.get("catalog_item_id"):
        catalog_item_id = str(inventory.get("catalog_item_id"))
    quantity_available = (
        int(getattr(inventory, "quantity_available", 0))
        if not isinstance(inventory, dict)
        else int(inventory.get("quantity_available", 0) or 0)
    )
    quantity_reserved = (
        int(getattr(inventory, "reserved_quantity", 0))
        if not isinstance(inventory, dict)
        else int(inventory.get("quantity_reserved", inventory.get("reserved_quantity", 0)) or 0)
    )
    quantity_free = (
        int(getattr(inventory, "quantity_free", 0))
        if not isinstance(inventory, dict)
        else int(inventory.get("quantity_free", max(0, quantity_available - quantity_reserved)) or 0)
    )

    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "inventory_updated",
        "operation": operation,
        "occurred_at": timezone.now().isoformat(),
        "hospital_id": str(hospital.id),
        "hospital_registration_number": hospital.registration_number,
        "request": {
            "id": str(req.id),
            "status": req.status,
            "workflow_state": req.workflow_state,
            "requesting_hospital_id": str(req.requesting_hospital_id),
            "supplying_hospital_id": str(req.supplying_hospital_id),
        },
        "actor_id": str(getattr(actor, "id", "")) if actor else "",
        "inventory": {
            "inventory_id": inventory_id,
            "catalog_item_id": catalog_item_id,
            "resource_name": catalog_item.name,
            "resource_type": catalog_item.resource_type.name,
            "unit_of_measure": catalog_item.unit_of_measure,
            "quantity_available": quantity_available,
            "quantity_reserved": quantity_reserved,
            "quantity_free": quantity_free,
            "quantity_available_delta": quantity_available_delta,
            "quantity_reserved_delta": quantity_reserved_delta,
            "quantity_transferred": quantity_transferred,
        },
        "metadata": metadata or {},
    }


def _notify_inventory_update_after_commit(
    *,
    req: ResourceRequest,
    hospital,
    catalog_item,
    inventory,
    operation: str,
    actor,
    quantity_available_delta: int = 0,
    quantity_reserved_delta: int = 0,
    quantity_transferred: int = 0,
    metadata: dict | None = None,
) -> None:
    payload = _build_inventory_update_payload(
        req=req,
        hospital=hospital,
        catalog_item=catalog_item,
        inventory=inventory,
        operation=operation,
        actor=actor,
        quantity_available_delta=quantity_available_delta,
        quantity_reserved_delta=quantity_reserved_delta,
        quantity_transferred=quantity_transferred,
        metadata=metadata,
    )

    def _send_update() -> None:
        from apps.hospitals.services import notify_hospital_inventory_update

        notify_hospital_inventory_update(
            hospital=hospital,
            operation=operation,
            payload=payload,
            request_obj=req,
        )

    transaction.on_commit(_send_update)


def _ensure_default_batch(inventory):
    from apps.resources.models import ResourceInventoryBatch

    if inventory.batches.exists():
        return
    ResourceInventoryBatch.objects.create(
        inventory=inventory,
        batch_number=f"AUTO-{inventory.id}",
        quantity_acquired=inventory.quantity_available,
        quantity_available_in_batch=inventory.quantity_available,
        quantity_reserved_in_batch=inventory.reserved_quantity,
        unit_price_at_acquisition=inventory.price_per_unit,
        currency=inventory.currency,
        acquired_at=timezone.now(),
        source_reference="system_bootstrap",
    )


def _batch_free_stock(batch) -> int:
    return max(0, int(batch.quantity_available_in_batch or 0) - int(batch.quantity_reserved_in_batch or 0))


def _sync_inventory_aggregate_from_batches(inventory, batches) -> None:
    total_available = sum(int(batch.quantity_available_in_batch or 0) for batch in batches)
    total_reserved = sum(int(batch.quantity_reserved_in_batch or 0) for batch in batches)
    inventory.quantity_available = max(0, total_available)
    inventory.reserved_quantity = max(0, total_reserved)
    inventory.quantity_reserved = inventory.reserved_quantity
    inventory.save(update_fields=["quantity_available", "reserved_quantity", "quantity_reserved", "updated_at"])


def _assert_inventory_batch_free_invariant(inventory, batches) -> None:
    aggregate_free_stock = max(0, int(inventory.quantity_available or 0) - int(inventory.reserved_quantity or 0))
    batch_free_stock = sum(_batch_free_stock(batch) for batch in batches)
    if aggregate_free_stock != batch_free_stock:
        raise ValidationError(
            {
                "detail": "Inventory/batch free-stock invariant violation.",
                "aggregate_free_stock": aggregate_free_stock,
                "batch_free_stock": batch_free_stock,
            }
        )


def _backfill_batch_reserved_from_inventory(inventory, batches) -> None:
    target_reserved = max(int(inventory.reserved_quantity or 0), int(inventory.quantity_reserved or 0))
    current_reserved = sum(int(batch.quantity_reserved_in_batch or 0) for batch in batches)
    deficit = target_reserved - current_reserved
    if deficit <= 0:
        return

    for batch in batches:
        free_capacity = _batch_free_stock(batch)
        if free_capacity <= 0:
            continue
        take = min(free_capacity, deficit)
        if take <= 0:
            continue
        batch.quantity_reserved_in_batch = int(batch.quantity_reserved_in_batch or 0) + take
        batch.save(update_fields=["quantity_reserved_in_batch", "updated_at"])
        deficit -= take
        if deficit <= 0:
            break

    if deficit > 0:
        raise ValidationError(
            {
                "detail": "Unable to align batch reserved stock with aggregate reserved stock.",
                "missing_reserved_quantity": deficit,
            }
        )


def _decrement_batch_reserved(batches, quantity: int, *, consume_available: bool) -> None:
    remaining = int(quantity or 0)
    if remaining <= 0:
        return

    for batch in batches:
        reserved_in_batch = int(batch.quantity_reserved_in_batch or 0)
        if reserved_in_batch <= 0:
            continue

        take = min(reserved_in_batch, remaining)
        if take <= 0:
            continue

        batch.quantity_reserved_in_batch = reserved_in_batch - take
        update_fields = ["quantity_reserved_in_batch", "updated_at"]
        if consume_available:
            available_in_batch = int(batch.quantity_available_in_batch or 0)
            if available_in_batch < take:
                raise ValidationError(
                    {
                        "detail": "Batch available stock is below transfer quantity.",
                        "batch_id": str(batch.id),
                    }
                )
            batch.quantity_available_in_batch = available_in_batch - take
            update_fields.insert(0, "quantity_available_in_batch")

        batch.save(update_fields=update_fields)
        remaining -= take
        if remaining <= 0:
            break

    if remaining > 0:
        raise ValidationError(
            {
                "detail": "Insufficient reserved batch stock for requested operation.",
                "missing_reserved_quantity": remaining,
            }
        )


def _share_offer_capacity_snapshot(
    *,
    supplying_hospital_id,
    catalog_item_id,
    exclude_request_id=None,
    lock: bool = False,
) -> dict | None:
    snapshot = share_capacity_snapshot_for_catalog_item(
        supplying_hospital_id=supplying_hospital_id,
        catalog_item_id=catalog_item_id,
        exclude_request_id=exclude_request_id,
        lock=lock,
    )
    if snapshot is None:
        return None

    return {
        "offered_quantity": int(snapshot.get("offered_quantity", 0)),
        "reserved_quantity": int(snapshot.get("reserved_quantity", 0)),
        "transferred_quantity": int(snapshot.get("transferred_quantity", 0)),
        "committed_quantity": int(snapshot.get("committed_quantity", 0)),
        "remaining_quantity": int(snapshot.get("available_share_quantity", 0)),
    }


def _ensure_share_offer_capacity(
    *,
    supplying_hospital_id,
    catalog_item_id,
    requested_quantity: int,
    exclude_request_id=None,
    lock: bool = False,
) -> dict | None:
    requested = int(requested_quantity or 0)
    if requested <= 0:
        return None

    snapshot = _share_offer_capacity_snapshot(
        supplying_hospital_id=supplying_hospital_id,
        catalog_item_id=catalog_item_id,
        exclude_request_id=exclude_request_id,
        lock=lock,
    )
    if snapshot is None:
        # Backward-compatible fallback: when no active share exists, use inventory-only controls.
        return None

    if requested > snapshot["remaining_quantity"]:
        raise ValidationError(
            {
                "detail": "Insufficient active shared quantity for this resource.",
                "requested_quantity": requested,
                "shared_quantity_offered": snapshot["offered_quantity"],
                "shared_quantity_committed": snapshot["committed_quantity"],
                "shared_quantity_remaining": snapshot["remaining_quantity"],
            }
        )

    return snapshot


def create_resource_request(requesting_hospital, data: dict, actor) -> ResourceRequest:
    from apps.hospitals.models import Hospital
    from apps.resources.models import ResourceCatalog

    if requesting_hospital is None:
        raise ValidationError({"detail": "Authenticated user does not have a hospital context."})

    try:
        supplying_hospital = Hospital.objects.get(id=data["supplying_hospital"])
    except Hospital.DoesNotExist:
        raise NotFound("Supplying hospital not found.")

    try:
        catalog_item = ResourceCatalog.objects.get(id=data["catalog_item"])
    except ResourceCatalog.DoesNotExist:
        raise NotFound("Catalog item not found.")

    if str(catalog_item.hospital_id) != str(supplying_hospital.id):
        raise ValidationError({"detail": "Catalog item does not belong to the selected supplying hospital."})

    if not catalog_item.is_shareable:
        raise ValidationError({"detail": "This resource is not marked as shareable."})

    if requesting_hospital == supplying_hospital:
        raise ValidationError({"detail": "Cannot request from your own hospital."})

    dedup_key = data.get("deduplication_key")
    if dedup_key:
        existing = ResourceRequest.objects.filter(
            requesting_hospital=requesting_hospital,
            deduplication_key=dedup_key,
        ).exclude(status__in=TERMINAL_REQUEST_STATUSES).first()
        if existing:
            if (
                existing.supplying_hospital_id == supplying_hospital.id
                and existing.catalog_item_id == catalog_item.id
                and existing.quantity_requested == data["quantity_requested"]
            ):
                return existing
            raise ValidationError({"detail": "duplicate_active_request"})

    _ensure_share_offer_capacity(
        supplying_hospital_id=supplying_hospital.id,
        catalog_item_id=catalog_item.id,
        requested_quantity=int(data["quantity_requested"]),
        lock=False,
    )

    expires_at = data.get("needed_by")
    expires_in_minutes = data.get("expires_in_minutes")
    if expires_in_minutes:
        expires_at = timezone.now() + timedelta(minutes=expires_in_minutes)

    payment_required = _is_payment_required(requesting_hospital, supplying_hospital)

    allow_partial_fulfillment = bool(data.get("allow_partial_fulfillment", False))

    req = ResourceRequest.objects.create(
        requesting_hospital=requesting_hospital,
        supplying_hospital=supplying_hospital,
        catalog_item=catalog_item,
        quantity_requested=data["quantity_requested"],
        workflow_state=ResourceRequest.WorkflowState.PENDING,
        allow_partial_fulfillment=allow_partial_fulfillment,
        payment_required=payment_required,
        deduplication_key=dedup_key,
        expires_at=expires_at,
        priority=data.get("priority", ResourceRequest.Priority.NORMAL),
        notes=data.get("notes", ""),
        needed_by=data.get("needed_by"),
        requested_by=actor,
    )
    _write_transition(req, "", ResourceRequest.WorkflowState.PENDING, actor, reason="request_created")
    _write_workflow_audit(req, "request_created", "success", actor, details={"payment_required": payment_required})
    logger.info("ResourceRequest created: %s", req.id)

    _publish_badge_event_after_commit(
        RequestCreatedEvent(
            event_id=f"request-created:{req.id}",
            request_id=str(req.id),
            requesting_hospital_id=str(req.requesting_hospital_id),
            supplying_hospital_id=str(req.supplying_hospital_id),
        )
    )
    return req


@_require_mutable_request_workflow
def approve_request(
    req: ResourceRequest,
    decision: str,
    quantity_approved: int,
    reason: str,
    actor,
    waive_payment: bool = False,
) -> ResourceRequest:
    _ensure_supplier_hospital_actor(req, actor, "approve")

    if req.status != ResourceRequest.Status.PENDING:
        raise ValidationError({"detail": f"Request is already {req.status}."})

    with transaction.atomic():
        from_state = req.workflow_state
        share_capacity_snapshot = None
        if decision == "approved":
            approved_qty = quantity_approved or req.quantity_requested
            share_capacity_snapshot = _ensure_share_offer_capacity(
                supplying_hospital_id=req.supplying_hospital_id,
                catalog_item_id=req.catalog_item_id,
                requested_quantity=approved_qty,
                exclude_request_id=req.id,
                lock=True,
            )
            _inventory_gateway().reserve_stock(
                request_id=req.id,
                facility_id=req.supplying_hospital_id,
                item_ref=req.catalog_item_id,
                quantity=approved_qty,
                idempotency_key=f"approve:{req.id}:{approved_qty}",
            )

            inventory_snapshot = _get_inventory_snapshot(req.supplying_hospital_id, req.catalog_item_id)
            unit_price = Decimal(str(inventory_snapshot.get("price_per_unit", "0")))

            # Snapshot pricing at approval time to prevent later manipulation.
            req.price_snapshot = unit_price
            req.total_price = Decimal(approved_qty) * unit_price
            req.status = ResourceRequest.Status.APPROVED
            req.quantity_approved = approved_qty
            req.quantity_reserved = approved_qty
            req.workflow_state = ResourceRequest.WorkflowState.RESERVED
            req.payment_required = _is_payment_required(req.requesting_hospital, req.supplying_hospital)
            if waive_payment:
                req.payment_required = False
                req.payment_note = "payment_waived_by_supplier"
            else:
                req.payment_note = ""
        else:
            req.status = ResourceRequest.Status.REJECTED
            req.quantity_approved = None
            req.workflow_state = ResourceRequest.WorkflowState.FAILED
            req.failed_reason = reason or "request_rejected"

        req_update_fields = [
            "status",
            "workflow_state",
            "quantity_approved",
            "quantity_reserved",
            "price_snapshot",
            "total_price",
            "payment_required",
            "payment_note",
            "failed_reason",
            "updated_at",
        ]
        req_update_fields.extend(_stop_request_sla_timer(req))
        req.save(update_fields=req_update_fields)

        if decision == "approved":
            _notify_inventory_update_after_commit(
                req=req,
                hospital=req.supplying_hospital,
                catalog_item=req.catalog_item,
                inventory=inventory_snapshot,
                operation="request_approved_inventory",
                actor=actor,
                quantity_available_delta=0,
                quantity_reserved_delta=approved_qty,
                metadata={"decision": decision},
            )

        ResourceRequestApproval.objects.create(
            request=req,
            reviewed_by=actor,
            decision=decision,
            quantity_approved=req.quantity_approved,
            reason=reason,
        )
        _write_transition(req, from_state, req.workflow_state, actor, reason=reason or decision)
        _write_workflow_audit(
            req,
            "request_approved" if decision == "approved" else "request_rejected",
            "success",
            actor,
            details={
                "decision": decision,
                "quantity_approved": req.quantity_approved,
                "shared_quantity_offered": (
                    share_capacity_snapshot["offered_quantity"] if share_capacity_snapshot else None
                ),
                "shared_quantity_committed": (
                    share_capacity_snapshot["committed_quantity"] if share_capacity_snapshot else None
                ),
                "shared_quantity_remaining_before_approval": (
                    share_capacity_snapshot["remaining_quantity"] if share_capacity_snapshot else None
                ),
            },
        )

        if decision == "approved":
            _publish_badge_event_after_commit(
                RequestApprovedEvent(
                    event_id=f"request-approved:{req.id}",
                    request_id=str(req.id),
                    requesting_hospital_id=str(req.requesting_hospital_id),
                    supplying_hospital_id=str(req.supplying_hospital_id),
                )
            )
        else:
            _publish_badge_event_after_commit(
                RequestRejectedEvent(
                    event_id=f"request-rejected:{req.id}",
                    request_id=str(req.id),
                    requesting_hospital_id=str(req.requesting_hospital_id),
                    supplying_hospital_id=str(req.supplying_hospital_id),
                )
            )

    logger.info("Request %s %s by %s", req.id, decision, actor.id)
    return req


@_require_mutable_request_workflow
def dispatch_request(req: ResourceRequest, actor, shipment=None, notes: str = "") -> DispatchEvent:
    _ensure_supplier_hospital_actor(req, actor, "dispatch")

    if req.status != ResourceRequest.Status.APPROVED:
        raise ValidationError({"detail": "Only approved requests can be dispatched."})

    if req.payment_required and req.workflow_state != ResourceRequest.WorkflowState.PAYMENT_COMPLETED:
        payable_amount = Decimal(str(req.total_price or "0"))
        if payable_amount <= Decimal("0"):
            req.payment_required = False
            req.payment_status = ResourceRequest.PaymentStatus.PAID
            req.payment_note = req.payment_note or "payment_auto_waived_zero_total"
            req.save(update_fields=["payment_required", "payment_status", "payment_note", "updated_at"])
            _write_workflow_audit(
                req,
                "payment_auto_waived_zero_total",
                "success",
                actor,
                details={"total_price": str(payable_amount)},
            )
        else:
            raise ValidationError({"detail": "Payment must be completed before dispatch."})

    if req.requested_by_id is None:
        raise ValidationError({"detail": "Request must have an intended receiver before dispatch."})

    with transaction.atomic():
        from apps.shipments.models import Shipment, ShipmentTracking
        from apps.shipments.services import create_shipment

        if shipment is None:
            shipment = create_shipment(
                origin_hospital=req.supplying_hospital,
                destination_hospital=req.requesting_hospital,
                data={"reference": f"REQ-{req.id}"},
                actor=actor,
            )

        from_state = req.workflow_state
        req.status = ResourceRequest.Status.DISPATCHED
        req.workflow_state = ResourceRequest.WorkflowState.IN_TRANSIT
        req.save(update_fields=["status", "workflow_state", "updated_at"])

        token_expiry = timezone.now() + timedelta(hours=24)
        internal_transition_token = generate_hex_token(32)
        internal_transition_hash = _hash_delivery_token(internal_transition_token)
        intended_receiver_user_id = req.requested_by_id
        shipment.dispatch_token = ""
        # Receiver confirmation is dispatch-QR-only; keep compatibility field but stop generating new values.
        shipment.receive_token = ""
        shipment.token_expires_at = token_expiry
        shipment.status = Shipment.Status.DISPATCHED
        shipment.save(update_fields=["dispatch_token", "receive_token", "token_expires_at", "status", "updated_at"])

        ShipmentTracking.objects.create(
            shipment=shipment,
            status=Shipment.Status.DISPATCHED,
            notes="Resource dispatched",
            recorded_by=actor,
        )

        dispatch_event = DispatchEvent.objects.create(
            request=req,
            dispatched_by=actor,
            shipment=shipment,
            notes=notes,
        )

        # Persist only internal token hash and assignment metadata; raw values are never exposed.
        DeliveryToken.objects.update_or_create(
            request=req,
            defaults={
                "shipment": shipment,
                "sender_user": actor,
                "intended_receiver_user_id": intended_receiver_user_id,
                "token": internal_transition_hash,
                "expires_at": token_expiry,
                "used_at": None,
                "workflow_status": req.workflow_state,
            },
        )
        _write_transition(req, from_state, req.workflow_state, actor, reason="dispatch")
        _write_workflow_audit(req, "request_dispatched", "success", actor, details={"shipment_id": str(shipment.id)})

        _publish_badge_event_after_commit(
            RequestDispatchedEvent(
                event_id=f"request-dispatched:{req.id}",
                request_id=str(req.id),
                supplying_hospital_id=str(req.supplying_hospital_id),
            )
        )

        dispatch_event.delivery_qr_payload = build_delivery_qr_payload(
            req=req,
            shipment_id=shipment.id,
            receiver_user_id=intended_receiver_user_id,
            expires_at=token_expiry,
        )

    logger.info("Request %s dispatched", req.id)
    return dispatch_event


def confirm_delivery(
    qr_payload: str,
    quantity_received: int | None,
    notes: str,
    actor,
) -> DeliveryEvent:
    completion_payload = confirm_workflow_completion(
        qr_payload=qr_payload,
        quantity_received=quantity_received,
        notes=notes,
        actor=actor,
    )
    delivery_event_id = completion_payload.get("delivery_event_id")
    if not delivery_event_id:
        raise ValidationError({"detail": "Receiver confirmation pending. Provide receiver context to finalize delivery."})
    return DeliveryEvent.objects.get(id=delivery_event_id)


@_require_mutable_request_workflow
def cancel_request(req: ResourceRequest, actor, reason: str = "") -> ResourceRequest:
    from apps.shipments.models import Shipment, ShipmentTracking

    _ensure_cancel_actor(req, actor, "cancel")

    if req.status in (ResourceRequest.Status.FULFILLED, ResourceRequest.Status.DELIVERED, ResourceRequest.Status.REJECTED):
        raise ValidationError({"detail": f"Cannot cancel a request with status {req.status}."})

    status_before_cancel = req.status

    with transaction.atomic():
        from_state = req.workflow_state
        quantity_reserved_update = None
        if req.status == ResourceRequest.Status.APPROVED:
            released_qty = req.quantity_reserved or req.quantity_approved or req.quantity_requested
            _inventory_gateway().release_stock(
                request_id=req.id,
                facility_id=req.supplying_hospital_id,
                item_ref=req.catalog_item_id,
                quantity=released_qty,
                reason=reason or "cancelled",
            )
            source_inventory_snapshot = _get_inventory_snapshot(req.supplying_hospital_id, req.catalog_item_id)
            ResourceRequestReservation.objects.filter(
                request=req,
                reservation_status=ResourceRequestReservation.ReservationStatus.ACTIVE,
            ).update(
                reservation_status=ResourceRequestReservation.ReservationStatus.RELEASED,
                release_reason=reason or "cancelled",
                released_at=timezone.now(),
                updated_at=timezone.now(),
            )
            quantity_reserved_update = 0

            try:
                dispatch_event = DispatchEvent.objects.select_related("shipment").get(request=req)
            except DispatchEvent.DoesNotExist:
                dispatch_event = None

            if dispatch_event and dispatch_event.shipment:
                dispatch_event.shipment.status = Shipment.Status.CANCELLED
                dispatch_event.shipment.cancel_reason = reason
                shipment_update_fields = ["status", "cancel_reason", "updated_at"]
                shipment_update_fields.extend(_stop_shipment_sla_timer(dispatch_event.shipment))
                dispatch_event.shipment.save(update_fields=shipment_update_fields)
                ShipmentTracking.objects.create(
                    shipment=dispatch_event.shipment,
                    status=Shipment.Status.CANCELLED,
                    notes=reason or "Cancelled before dispatch",
                    recorded_by=actor,
                )

            _notify_inventory_update_after_commit(
                req=req,
                hospital=req.supplying_hospital,
                catalog_item=req.catalog_item,
                inventory=source_inventory_snapshot,
                operation="request_cancelled_inventory",
                actor=actor,
                quantity_available_delta=0,
                quantity_reserved_delta=-released_qty,
                metadata={"reason": reason or "cancelled_before_dispatch"},
            )

        elif req.status == ResourceRequest.Status.DISPATCHED:
            if not reason:
                raise ValidationError({"detail": "Cannot cancel a dispatched request without a return reason."})

            dispatch_event = DispatchEvent.objects.select_related("shipment").filter(request=req).first()
            if not dispatch_event or not dispatch_event.shipment:
                raise ValidationError({"detail": "Dispatched request has no shipment to return."})

            shipment = dispatch_event.shipment
            if not shipment.return_token:
                shipment.return_token = generate_hex_token(32)
            shipment.status = Shipment.Status.RETURNING
            shipment.cancel_reason = reason
            shipment_update_fields = ["return_token", "status", "cancel_reason", "updated_at"]
            shipment_update_fields.extend(_stop_shipment_sla_timer(shipment))
            shipment.save(update_fields=shipment_update_fields)
            ShipmentTracking.objects.create(
                shipment=shipment,
                status=Shipment.Status.RETURNING,
                notes=reason or "Returning to origin",
                recorded_by=actor,
            )

        req.status = ResourceRequest.Status.CANCELLED
        req.workflow_state = ResourceRequest.WorkflowState.CANCELLED
        req.cancellation_reason = reason or "cancelled"
        update_fields = ["status", "workflow_state", "cancellation_reason", "updated_at"]
        update_fields.extend(_stop_request_sla_timer(req))
        if quantity_reserved_update is not None:
            req.quantity_reserved = quantity_reserved_update
            update_fields.append("quantity_reserved")
        req.save(update_fields=update_fields)
        _write_transition(req, from_state, req.workflow_state, actor, reason=req.cancellation_reason)
        _write_workflow_audit(req, "request_cancelled", "success", actor, details={"reason": req.cancellation_reason})

        # If a successful payment exists, a refund must be initiated separately
        # (NOT inline — see design §3.5). Log the intent so an async task can pick it up.
        payment_was_successful = PaymentTransaction.objects.filter(
            request=req,
            payment_status=PaymentTransaction.PaymentStatus.SUCCESS,
        ).exists()
        if payment_was_successful:
            _write_workflow_audit(
                req,
                "cancel_triggered_refund_enqueue",
                "success",
                actor,
                details={
                    "note": "Refund must be initiated via POST /refunds/initiate/ with cause=REQUESTER_CANCELLATION.",
                    "reason": req.cancellation_reason,
                },
            )

        _publish_badge_event_after_commit(
            RequestCancelledEvent(
                event_id=f"request-cancelled:{req.id}",
                request_id=str(req.id),
                requesting_hospital_id=str(req.requesting_hospital_id),
                supplying_hospital_id=str(req.supplying_hospital_id),
                was_pending_incoming=status_before_cancel == ResourceRequest.Status.PENDING,
                was_pending_dispatch=status_before_cancel == ResourceRequest.Status.APPROVED,
            )
        )

    logger.info("Request %s cancelled by %s", req.id, actor.id)
    return req


@_require_mutable_request_workflow
def initiate_return(req: ResourceRequest, actor, reason: str = "") -> dict:
    if req.status != ResourceRequest.Status.DISPATCHED:
        raise ValidationError({"detail": "Return can only be initiated for dispatched requests."})

    cancel_reason = reason or "return_requested"
    updated_req = cancel_request(req=req, actor=actor, reason=cancel_reason)

    dispatch_event = DispatchEvent.objects.select_related("shipment").filter(request=updated_req).first()
    shipment = dispatch_event.shipment if dispatch_event else None
    if shipment is None:
        raise ValidationError({"detail": "Return flow requires an attached shipment."})

    return {
        "request_id": str(updated_req.id),
        "status": updated_req.status,
        "workflow_state": updated_req.workflow_state,
        "expires_at": updated_req.expires_at.isoformat() if updated_req.expires_at else None,
        "sla_active": _is_request_sla_active(updated_req),
        "sla_end_time": updated_req.expired_at.isoformat() if updated_req.expired_at else None,
        "cancellation_reason": updated_req.cancellation_reason,
        "shipment_id": str(shipment.id),
        "shipment_status": shipment.status,
        "return_token": shipment.return_token,
    }


def verify_return(
    req: ResourceRequest,
    return_token: str,
    actor,
    quantity_returned: int | None = None,
    quantity_damaged: int = 0,
    quantity_lost: int = 0,
    verification_notes: str = "",
) -> ResourceRequest:
    from apps.shipments.models import Shipment, ShipmentTracking

    _ensure_supplier_hospital_actor(req, actor, "verify return")

    if req.status != ResourceRequest.Status.CANCELLED:
        raise ValidationError({"detail": "Only cancelled requests can be returned."})

    dispatch_event = DispatchEvent.objects.select_related("shipment").filter(request=req).first()
    if not dispatch_event or not dispatch_event.shipment:
        raise ValidationError({"detail": "No shipment found for this request."})

    shipment = dispatch_event.shipment
    if shipment.return_token != return_token:
        raise ValidationError({"detail": "Invalid return token."})
    if shipment.return_token_used_at:
        raise ValidationError({"detail": "Return token already used."})

    # Default returned quantity to the full transferred quantity if not supplied.
    effective_returned = quantity_returned if quantity_returned is not None else (req.quantity_transferred or 0)
    max_qty = req.quantity_transferred or 0
    if (effective_returned + quantity_damaged + quantity_lost) > max_qty and max_qty > 0:
        raise ValidationError(
            {"detail": "Sum of returned + damaged + lost quantities exceeds quantity_transferred."}
        )

    with transaction.atomic():
        released_qty = req.quantity_reserved or req.quantity_approved or req.quantity_requested
        _inventory_gateway().release_stock(
            request_id=req.id,
            facility_id=req.supplying_hospital_id,
            item_ref=req.catalog_item_id,
            quantity=released_qty,
            reason="return_verified",
        )
        source_inventory_snapshot = _get_inventory_snapshot(req.supplying_hospital_id, req.catalog_item_id)
        ResourceRequestReservation.objects.filter(
            request=req,
            reservation_status=ResourceRequestReservation.ReservationStatus.ACTIVE,
        ).update(
            reservation_status=ResourceRequestReservation.ReservationStatus.RELEASED,
            release_reason="return_verified",
            released_at=timezone.now(),
            updated_at=timezone.now(),
        )
        shipment.return_token_used_at = timezone.now()
        shipment.status = Shipment.Status.RETURNED
        shipment_update_fields = ["return_token_used_at", "status", "updated_at"]
        shipment_update_fields.extend(_stop_shipment_sla_timer(shipment))
        shipment.save(update_fields=shipment_update_fields)
        ShipmentTracking.objects.create(
            shipment=shipment,
            status=Shipment.Status.RETURNED,
            notes="Return verified at origin.",
            recorded_by=actor,
        )

        req.quantity_reserved = 0
        req_update_fields = ["quantity_reserved", "updated_at"]
        req_update_fields.extend(_stop_request_sla_timer(req))
        req.save(update_fields=req_update_fields)

        # Create or update PartialReturnRecord for this verification event.
        return_record, _ = PartialReturnRecord.objects.update_or_create(
            request=req,
            defaults={
                "shipment": shipment,
                "quantity_returned": effective_returned,
                "quantity_damaged": quantity_damaged,
                "quantity_lost": quantity_lost,
                "return_verified": True,
                "verified_by": actor,
                "verified_at": timezone.now(),
                "verification_notes": verification_notes,
                "return_token_used": return_token,
            },
        )

        # If a RefundRequest is blocked on return verification, advance it to PENDING.
        blocked_refund = (
            RefundRequest.objects.select_for_update()
            .filter(request=req, refund_status=RefundStatus.PENDING_RETURN_VERIFICATION)
            .first()
        )
        if blocked_refund:
            # Recalculate refund amount based on actual returned quantities.
            outcome = evaluate_refund_policy(
                req,
                blocked_refund.cause,
                returned_quantity=effective_returned,
                damaged_quantity=quantity_damaged,
            )
            blocked_refund.refund_amount = outcome.refund_amount
            blocked_refund.refund_percentage_applied = outcome.refund_percentage_applied
            blocked_refund.refund_status = RefundStatus.PENDING
            blocked_refund.sla_deadline_at = timezone.now() + timedelta(hours=_REFUND_PENDING_SLA_HOURS)
            blocked_refund.save(update_fields=[
                "refund_amount", "refund_percentage_applied",
                "refund_status", "sla_deadline_at", "updated_at",
            ])
            return_record.refund_request = blocked_refund
            return_record.save(update_fields=["refund_request", "updated_at"])
            _write_workflow_audit(
                req,
                "return_verified",
                "success",
                actor,
                details={
                    "refund_request_id": str(blocked_refund.id),
                    "refund_amount": str(outcome.refund_amount),
                    "refund_status": RefundStatus.PENDING,
                    "quantity_returned": effective_returned,
                },
            )
        else:
            _write_workflow_audit(
                req,
                "return_verified",
                "success",
                actor,
                details={"quantity_returned": effective_returned, "quantity_damaged": quantity_damaged},
            )

        _notify_inventory_update_after_commit(
            req=req,
            hospital=req.supplying_hospital,
            catalog_item=req.catalog_item,
            inventory=source_inventory_snapshot,
            operation="request_return_inventory",
            actor=actor,
            quantity_available_delta=0,
            quantity_reserved_delta=-released_qty,
            metadata={"return_verified": True},
        )

    logger.info("Return verified for request %s", req.id)
    return req


@_require_mutable_request_workflow
def confirm_payment(req: ResourceRequest, payment_status: str, payment_note: str, actor) -> ResourceRequest:
    _ensure_requesting_hospital_actor(req, actor, "confirm payment")

    if req.status not in (ResourceRequest.Status.FULFILLED, ResourceRequest.Status.DELIVERED, ResourceRequest.Status.APPROVED):
        raise ValidationError({"detail": "Payment can only be recorded for approved/fulfilled requests."})

    if payment_status not in ResourceRequest.PaymentStatus.values:
        raise ValidationError({"detail": "Invalid payment status."})

    from_state = req.workflow_state
    req.payment_status = payment_status
    req.payment_note = payment_note or ""
    if payment_status in (ResourceRequest.PaymentStatus.PAID, ResourceRequest.PaymentStatus.SUCCESS):
        req.workflow_state = ResourceRequest.WorkflowState.PAYMENT_COMPLETED
    elif payment_status in (ResourceRequest.PaymentStatus.REFUND_PENDING,):
        req.workflow_state = ResourceRequest.WorkflowState.FAILED
    req_update_fields = ["payment_status", "payment_note", "workflow_state", "updated_at"]
    req_update_fields.extend(_stop_request_sla_timer(req))
    req.save(update_fields=req_update_fields)
    if from_state != req.workflow_state:
        _write_transition(req, from_state, req.workflow_state, actor, reason="payment_status_update")
    _write_workflow_audit(req, "payment_confirmed", "success", actor, details={"payment_status": payment_status})
    logger.info("Payment status updated for request %s by %s", req.id, actor.id)
    return req


@_require_mutable_request_workflow
def reserve_request(
    req: ResourceRequest,
    actor,
    requested_quantity: int | None = None,
    strategy: str = "fefo",
    reservation_timeout_minutes: int = 120,
    idempotency_key: str = "",
    payload: dict | None = None,
) -> dict:
    _ensure_supplier_hospital_actor(req, actor, "reserve")
    required_codes = ("share.request.approve", "inventory.batch.view", "inventory.cost.view")
    missing_codes = [
        code for code in required_codes if not has_any_permission(actor, (code,), allow_role_fallback=False)
    ]
    if missing_codes:
        raise PermissionDenied(
            f"Missing required permission(s): {', '.join(missing_codes)}."
        )

    payload = payload or {
        "requested_quantity": requested_quantity,
        "strategy": strategy,
        "reservation_timeout_minutes": reservation_timeout_minutes,
    }
    existing = _get_existing_idempotent_response(
        req,
        RequestOperationIdempotency.OperationType.RESERVATION_CREATE,
        idempotency_key,
        payload,
    )
    if existing:
        return existing

    if req.status != ResourceRequest.Status.APPROVED:
        raise ValidationError({"detail": "Reservation can only be created for approved requests."})

    reserve_qty = requested_quantity or req.quantity_approved or req.quantity_requested
    if reserve_qty <= 0:
        raise ValidationError({"detail": "requested_quantity must be greater than zero."})

    from apps.resources.models import ResourceInventory, ResourceInventoryBatch

    # Transitional note: batch-level allocation is still performed here until fully moved behind
    # inventory module reservation interfaces.
    with transaction.atomic():
        req = ResourceRequest.objects.select_for_update().get(id=req.id)
        from_state = req.workflow_state
        _ensure_share_offer_capacity(
            supplying_hospital_id=req.supplying_hospital_id,
            catalog_item_id=req.catalog_item_id,
            requested_quantity=reserve_qty,
            exclude_request_id=req.id,
            lock=True,
        )
        inventory = ResourceInventory.objects.select_for_update().get(catalog_item_id=req.catalog_item_id)
        _ensure_default_batch(inventory)

        batches = list(
            ResourceInventoryBatch.objects.select_for_update()
            .filter(inventory=inventory)
            .order_by("expires_at", "acquired_at")
        )
        _backfill_batch_reserved_from_inventory(inventory, batches)
        batch_by_id = {batch.id: batch for batch in batches}

        active_existing_reservations = list(
            ResourceRequestReservation.objects.select_for_update()
            .select_related("inventory_batch")
            .filter(
                request=req,
                reservation_status=ResourceRequestReservation.ReservationStatus.ACTIVE,
            )
        )

        previous_reserved_quantity = 0
        for existing_reservation in active_existing_reservations:
            existing_batch = batch_by_id.get(existing_reservation.inventory_batch_id)
            if existing_batch is None:
                existing_batch = ResourceInventoryBatch.objects.select_for_update().get(
                    id=existing_reservation.inventory_batch_id
                )
                batches.append(existing_batch)
                batch_by_id[existing_batch.id] = existing_batch
            existing_batch.quantity_reserved_in_batch = max(
                0,
                existing_batch.quantity_reserved_in_batch - existing_reservation.reserved_quantity,
            )
            existing_batch.save(update_fields=["quantity_reserved_in_batch", "updated_at"])

            existing_reservation.reservation_status = ResourceRequestReservation.ReservationStatus.RELEASED
            existing_reservation.release_reason = "reservation_replaced"
            existing_reservation.released_at = timezone.now()
            existing_reservation.save(
                update_fields=["reservation_status", "release_reason", "released_at", "updated_at"]
            )
            previous_reserved_quantity += existing_reservation.reserved_quantity

        # Approvals may pre-reserve aggregate stock without reservation lines; release
        # that request-level reservation before re-allocating line-level reservations.
        legacy_reserved_to_replace = max(0, int(req.quantity_reserved or 0) - previous_reserved_quantity)
        if legacy_reserved_to_replace > 0:
            _decrement_batch_reserved(batches, legacy_reserved_to_replace, consume_available=False)
            previous_reserved_quantity += legacy_reserved_to_replace

        remaining = reserve_qty
        reservation_lines = []
        total_payable = Decimal("0.00")
        expires_at = timezone.now() + timedelta(minutes=reservation_timeout_minutes)

        for batch in batches:
            available = _batch_free_stock(batch)
            if available <= 0:
                continue
            take = min(available, remaining)
            if take <= 0:
                continue

            batch.quantity_reserved_in_batch += take
            batch.save(update_fields=["quantity_reserved_in_batch", "updated_at"])

            ResourceRequestReservation.objects.create(
                request=req,
                inventory_batch=batch,
                reserved_quantity=take,
                unit_price_at_reservation=batch.unit_price_at_acquisition,
                reservation_status=ResourceRequestReservation.ReservationStatus.ACTIVE,
                expires_at=expires_at,
            )
            line_total = Decimal(take) * batch.unit_price_at_acquisition
            total_payable += line_total
            reservation_lines.append(
                {
                    "inventory_batch_id": str(batch.id),
                    "reserved_quantity": take,
                    "unit_price_at_reservation": str(batch.unit_price_at_acquisition),
                }
            )
            remaining -= take
            if remaining <= 0:
                break

        reserved_quantity = reserve_qty - remaining
        shortage_quantity = remaining

        if shortage_quantity > 0 and not req.allow_partial_fulfillment:
            raise ValidationError({"detail": "Reservation shortfall and partial fulfillment is disabled."})

        _sync_inventory_aggregate_from_batches(inventory, batches)
        _assert_inventory_batch_free_invariant(inventory, batches)

        net_reserved_delta = reserved_quantity - previous_reserved_quantity

        req.quantity_reserved = reserved_quantity
        req.total_price = total_payable
        req.quantity_approved = req.quantity_approved or reserve_qty
        req.workflow_state = ResourceRequest.WorkflowState.RESERVED
        req.save(
            update_fields=[
                "quantity_reserved",
                "total_price",
                "quantity_approved",
                "workflow_state",
                "updated_at",
            ]
        )

        _write_transition(req, from_state, req.workflow_state, actor, reason="inventory_reserved")
        _write_workflow_audit(
            req,
            "inventory_reserved",
            "success",
            actor,
            details={"reserved_quantity": reserved_quantity, "shortage_quantity": shortage_quantity},
        )

        if net_reserved_delta != 0:
            _notify_inventory_update_after_commit(
                req=req,
                hospital=req.supplying_hospital,
                catalog_item=req.catalog_item,
                inventory=inventory,
                operation="request_reserved_inventory",
                actor=actor,
                quantity_available_delta=0,
                quantity_reserved_delta=net_reserved_delta,
                metadata={
                    "strategy": strategy,
                    "shortage_quantity": shortage_quantity,
                    "allow_partial_fulfillment": req.allow_partial_fulfillment,
                    "replaced_previous_reserved_quantity": previous_reserved_quantity,
                },
            )

        response_payload = {
            "request_id": str(req.id),
            "workflow_state": req.workflow_state,
            "reserved_quantity": reserved_quantity,
            "shortage_quantity": shortage_quantity,
            "reservation_expires_at": expires_at.isoformat(),
            "payment_due_at": expires_at.isoformat(),
            "total_payable_amount": str(total_payable),
            "currency": inventory.currency,
            "payment_required": req.payment_required,
            "reservation_lines": reservation_lines,
        }

    _save_idempotent_response(
        req,
        RequestOperationIdempotency.OperationType.RESERVATION_CREATE,
        idempotency_key,
        payload,
        response_payload,
    )
    return response_payload


@_require_mutable_request_workflow
def initiate_payment(
    req: ResourceRequest,
    actor,
    idempotency_key: str,
    gateway: str = "sslcommerz",
    reservation_timeout_minutes: int = 30,
    return_url: str = "",
    cancel_url: str = "",
    callback_base_url: str = "",
    payload: dict | None = None,
) -> dict:
    _ensure_requesting_hospital_actor(req, actor, "initiate payment")

    if not str(idempotency_key or "").strip():
        raise ValidationError({"detail": "Idempotency-Key header is required for payment initiation."})

    payload = payload or {
        "gateway": gateway,
        "reservation_timeout_minutes": reservation_timeout_minutes,
        "return_url": return_url,
        "cancel_url": cancel_url,
    }
    existing = _get_existing_idempotent_response(
        req,
        RequestOperationIdempotency.OperationType.PAYMENT_INITIATE,
        idempotency_key,
        payload,
    )
    if existing:
        return existing

    if not req.payment_required:
        raise ValidationError({"detail": "Payment is not required for this request."})
    if req.workflow_state not in (
        ResourceRequest.WorkflowState.RESERVED,
        ResourceRequest.WorkflowState.PAYMENT_PENDING,
    ):
        raise ValidationError({"detail": "Payment can only be initiated for reserved requests."})

    with transaction.atomic():
        from_state = req.workflow_state
        supply_inventory_snapshot = _get_inventory_snapshot(req.supplying_hospital_id, req.catalog_item_id)
        amount = req.total_price
        payment = PaymentTransaction.objects.create(
            request=req,
            provider=PaymentTransaction.Provider.SSLCOMMERZ if gateway.lower() == "sslcommerz" else PaymentTransaction.Provider.OTHER,
            amount=amount,
            currency=supply_inventory_snapshot.get("currency", "BDT"),
            payment_status=PaymentTransaction.PaymentStatus.PENDING,
            payer_hospital=req.requesting_hospital,
            receiver_hospital=req.supplying_hospital,
            idempotency_key=_scoped_idempotency_key(req, idempotency_key),
        )

        if Decimal(str(amount or "0")) <= Decimal("0"):
            payment.payment_status = PaymentTransaction.PaymentStatus.SUCCESS
            payment.completed_at = timezone.now()
            payment.provider_transaction_id = f"AUTOZERO-{payment.id.hex[:18]}"
            payment.gateway_session_id = ""
            payment.raw_gateway_payload = {
                "gateway": gateway,
                "reservation_timeout_minutes": reservation_timeout_minutes,
                "return_url": return_url,
                "cancel_url": cancel_url,
                "auto_settled": True,
                "reason": "zero_total",
            }
            payment.save(
                update_fields=[
                    "payment_status",
                    "completed_at",
                    "provider_transaction_id",
                    "gateway_session_id",
                    "raw_gateway_payload",
                    "updated_at",
                ]
            )

            req.payment_required = False
            req.payment_status = ResourceRequest.PaymentStatus.PAID
            req.payment_note = "payment_auto_settled_zero_total"
            req.workflow_state = ResourceRequest.WorkflowState.PAYMENT_COMPLETED
            req.save(
                update_fields=[
                    "payment_required",
                    "payment_status",
                    "payment_note",
                    "workflow_state",
                    "updated_at",
                ]
            )
            _write_transition(req, from_state, req.workflow_state, actor, reason="payment_auto_settled_zero_total")
            _write_workflow_audit(
                req,
                "payment_auto_settled_zero_total",
                "success",
                actor,
                details={"payment_id": str(payment.id), "amount": str(amount)},
            )
            redirect_url = ""
        else:
            if payment.provider == PaymentTransaction.Provider.SSLCOMMERZ:
                gateway_session = _sslcommerz_create_session(
                    payment=payment,
                    req=req,
                    return_url=return_url,
                    cancel_url=cancel_url,
                    callback_base_url=callback_base_url,
                )
                payment.gateway_session_id = gateway_session.get("session_key") or generate_hex_token(16)
                payment.provider_transaction_id = gateway_session.get("tran_id")
                payment.raw_gateway_payload = {
                    "gateway": gateway,
                    "reservation_timeout_minutes": reservation_timeout_minutes,
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                    "request": gateway_session.get("request_payload", {}),
                    "response": gateway_session.get("response_payload", {}),
                }
                redirect_url = gateway_session.get("redirect_url", "")
            else:
                payment.gateway_session_id = generate_hex_token(16)
                payment.raw_gateway_payload = {
                    "gateway": gateway,
                    "reservation_timeout_minutes": reservation_timeout_minutes,
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                }
                redirect_url = f"https://sandbox.sslcommerz.com/session/{payment.gateway_session_id}"

            payment.save(update_fields=["gateway_session_id", "provider_transaction_id", "raw_gateway_payload", "updated_at"])

            req.payment_status = ResourceRequest.PaymentStatus.PENDING
            req.workflow_state = ResourceRequest.WorkflowState.PAYMENT_PENDING
            req.save(update_fields=["payment_status", "workflow_state", "updated_at"])
            _write_transition(req, from_state, req.workflow_state, actor, reason="payment_initiated")
            _write_workflow_audit(req, "payment_initiated", "success", actor, details={"payment_id": str(payment.id)})

    response_payload = {
        "payment_id": str(payment.id),
        "workflow_state": req.workflow_state,
        "payment_status": payment.payment_status,
        "gateway_redirect_url": redirect_url,
        "amount": str(payment.amount),
        "currency": payment.currency,
    }
    _save_idempotent_response(
        req,
        RequestOperationIdempotency.OperationType.PAYMENT_INITIATE,
        idempotency_key,
        payload,
        response_payload,
    )
    return response_payload


@_require_mutable_request_workflow
def confirm_payment_transaction(
    req: ResourceRequest,
    actor,
    payment_status: str,
    payment_id=None,
    provider_transaction_id: str = "",
    raw_payload=None,
    enforce_actor: bool = True,
) -> dict:
    if enforce_actor:
        _ensure_requesting_hospital_actor(req, actor, "confirm payment")

    raw_payload = raw_payload or {}
    payment_qs = PaymentTransaction.objects.filter(request=req).order_by("-created_at")
    if payment_id:
        payment_qs = payment_qs.filter(id=payment_id)
    payment = payment_qs.first()
    if not payment:
        raise ValidationError({"detail": "Payment record not found for this request."})

    if payment_status not in PaymentTransaction.PaymentStatus.values:
        raise ValidationError({"detail": "Invalid payment status."})

    with transaction.atomic():
        current_payment_status = payment.payment_status

        # Idempotent no-op for repeated callbacks/confirmations with same final state.
        if payment_status == current_payment_status:
            return {
                "request_id": str(req.id),
                "workflow_state": req.workflow_state,
                "payment_status": payment.payment_status,
                "payment_id": str(payment.id),
            }

        # Do not allow callback replays to downgrade an already successful payment.
        if (
            current_payment_status == PaymentTransaction.PaymentStatus.SUCCESS
            and payment_status != PaymentTransaction.PaymentStatus.SUCCESS
        ):
            return {
                "request_id": str(req.id),
                "workflow_state": req.workflow_state,
                "payment_status": payment.payment_status,
                "payment_id": str(payment.id),
            }

        from_state = req.workflow_state
        payment.provider_transaction_id = provider_transaction_id or payment.provider_transaction_id
        payment.raw_gateway_payload = raw_payload

        if payment_status == PaymentTransaction.PaymentStatus.SUCCESS:
            payment.payment_status = PaymentTransaction.PaymentStatus.SUCCESS
            payment.completed_at = payment.completed_at or timezone.now()
            payment.failed_at = None
            req.payment_status = ResourceRequest.PaymentStatus.PAID
            req.workflow_state = ResourceRequest.WorkflowState.PAYMENT_COMPLETED
            req.failed_reason = ""

            if not PaymentLedgerEntry.objects.filter(
                payment_transaction=payment,
                hospital=req.requesting_hospital,
                entry_type=PaymentLedgerEntry.EntryType.SENT,
            ).exists():
                PaymentLedgerEntry.objects.create(
                    payment_transaction=payment,
                    hospital=req.requesting_hospital,
                    entry_type=PaymentLedgerEntry.EntryType.SENT,
                    amount=payment.amount,
                    currency=payment.currency,
                )

            if not PaymentLedgerEntry.objects.filter(
                payment_transaction=payment,
                hospital=req.supplying_hospital,
                entry_type=PaymentLedgerEntry.EntryType.RECEIVED,
            ).exists():
                PaymentLedgerEntry.objects.create(
                    payment_transaction=payment,
                    hospital=req.supplying_hospital,
                    entry_type=PaymentLedgerEntry.EntryType.RECEIVED,
                    amount=payment.amount,
                    currency=payment.currency,
                )
        elif payment_status == PaymentTransaction.PaymentStatus.FAILED:
            payment.payment_status = PaymentTransaction.PaymentStatus.FAILED
            payment.failed_at = payment.failed_at or timezone.now()
            req.payment_status = ResourceRequest.PaymentStatus.FAILED
            req.workflow_state = ResourceRequest.WorkflowState.FAILED
            req.failed_reason = "payment_failed"
        else:
            payment.payment_status = payment_status

        payment.save(update_fields=[
            "payment_status",
            "provider_transaction_id",
            "raw_gateway_payload",
            "completed_at",
            "failed_at",
            "updated_at",
        ])
        req_update_fields = ["payment_status", "workflow_state", "failed_reason", "updated_at"]
        req_update_fields.extend(_stop_request_sla_timer(req))
        req.save(update_fields=req_update_fields)
        if from_state != req.workflow_state:
            _write_transition(req, from_state, req.workflow_state, actor, reason="payment_confirmed")
        _write_workflow_audit(
            req,
            "payment_confirmed",
            "success",
            actor,
            details={
                "payment_status": payment_status,
                "payment_id": str(payment.id),
                "previous_payment_status": current_payment_status,
            },
        )

    return {
        "request_id": str(req.id),
        "workflow_state": req.workflow_state,
        "payment_status": payment.payment_status,
        "payment_id": str(payment.id),
    }


# ---------------------------------------------------------------------------
# Refund policy engine
# ---------------------------------------------------------------------------

_REFUND_INITIATABLE_STATES = frozenset({
    ResourceRequest.WorkflowState.PAYMENT_COMPLETED,
    ResourceRequest.WorkflowState.IN_TRANSIT,
    ResourceRequest.WorkflowState.COMPLETED,
    ResourceRequest.WorkflowState.CANCELLED,
    ResourceRequest.WorkflowState.FAILED,
})

# Causes that always result in 0% refund; blocked before engine runs.
_FRAUD_BLOCK_CAUSES = frozenset({
    RefundCause.FRAUD_SUSPECTED,
    RefundCause.POLICY_VIOLATION,
})

# Default SLA for a refund sitting in PENDING state (hours).
_REFUND_PENDING_SLA_HOURS = 24


@dataclass
class RefundOutcome:
    refund_amount: Decimal
    refund_percentage_applied: Decimal
    cause_used: str
    policy_rule: object  # RefundPolicyRule | None
    requires_return_verification: bool
    gateway_supported: bool
    rationale: str


def evaluate_refund_policy(
    req: ResourceRequest,
    cause: str,
    returned_quantity: int | None = None,
    damaged_quantity: int | None = None,
) -> RefundOutcome:
    """
    Stateless engine: looks up RefundPolicyRule and computes the refund outcome.
    Does NOT write to the database. Must be called inside the caller's transaction.
    """
    # Exact match first, then stage-default (NULL cause), then system fallback.
    rule = (
        RefundPolicyRule.objects.filter(
            workflow_state=req.workflow_state,
            cause=cause,
            is_active=True,
        ).first()
        or RefundPolicyRule.objects.filter(
            workflow_state=req.workflow_state,
            cause__isnull=True,
            is_active=True,
        ).first()
    )

    if rule is None:
        return RefundOutcome(
            refund_amount=Decimal("0.00"),
            refund_percentage_applied=Decimal("0.00"),
            cause_used=cause,
            policy_rule=None,
            requires_return_verification=False,
            gateway_supported=False,
            rationale="No matching policy rule; system fallback: 0% refund, manual review required.",
        )

    base_amount = req.total_price * (rule.refund_percentage / Decimal("100"))

    # Partial-return adjustment.
    quantity_transferred = req.quantity_transferred or 0
    if returned_quantity is not None and quantity_transferred > 0:
        usable = returned_quantity
        if cause == RefundCause.SUPPLIER_QUALITY_FAILURE and damaged_quantity:
            usable += damaged_quantity
        ratio = Decimal(str(usable)) / Decimal(str(quantity_transferred))
        final_amount = (base_amount * ratio).quantize(Decimal("0.01"))
        rationale = (
            f"Rule ({req.workflow_state}, {cause}): {rule.refund_percentage}% base; "
            f"partial ratio {usable}/{quantity_transferred} applied."
        )
    else:
        final_amount = base_amount.quantize(Decimal("0.01"))
        rationale = f"Rule ({req.workflow_state}, {cause}): {rule.refund_percentage}% applied."

    return RefundOutcome(
        refund_amount=final_amount,
        refund_percentage_applied=rule.refund_percentage,
        cause_used=cause,
        policy_rule=rule,
        requires_return_verification=rule.requires_return_verification,
        gateway_supported=rule.gateway_supported,
        rationale=rationale,
    )


def _ensure_refund_initiatable(req: ResourceRequest, cause: str) -> None:
    """Guard for initiate_refund(): replaces @_require_mutable_request_workflow."""
    if cause in _FRAUD_BLOCK_CAUSES:
        raise ValidationError(
            {"detail": f"Refund prohibited: cause '{cause}' is a fraud/policy block. No refund is payable."}
        )
    if req.workflow_state not in _REFUND_INITIATABLE_STATES:
        raise ValidationError(
            {"detail": f"Refund cannot be initiated from workflow state '{req.workflow_state}'."}
        )


def _ensure_refund_confirmable(req: ResourceRequest) -> None:
    """Guard for confirm_refund(): checks RefundRequest state, not workflow state."""
    active = RefundRequest.objects.filter(
        request=req,
        refund_status__in=[RefundStatus.PENDING, RefundStatus.PROCESSING],
    ).exists()
    if not active:
        raise ValidationError(
            {"detail": "No refund in PENDING or PROCESSING state found for this request."}
        )


# ---------------------------------------------------------------------------
# Refund initiation
# ---------------------------------------------------------------------------

def initiate_refund(
    req: ResourceRequest,
    actor,
    cause: str,
    cause_note: str = "",
    idempotency_key: str = "",
    returned_quantity: int | None = None,
    damaged_quantity: int | None = None,
) -> dict:
    _ensure_requesting_hospital_actor(req, actor, "initiate refund")
    _ensure_refund_initiatable(req, cause)

    if not idempotency_key:
        idempotency_key = str(uuid.uuid4())

    with transaction.atomic():
        # Lock payment row to prevent concurrent duplicate refunds.
        payment = (
            PaymentTransaction.objects.select_for_update()
            .filter(request=req, payment_status=PaymentTransaction.PaymentStatus.SUCCESS)
            .order_by("-created_at")
            .first()
        )
        if not payment:
            raise ValidationError({"detail": "No successful payment found to refund."})

        # Idempotency: return cached response if same key was already processed.
        existing_idem = RequestOperationIdempotency.objects.filter(
            operation_type=RequestOperationIdempotency.OperationType.REFUND_INITIATE,
            idempotency_key=idempotency_key,
        ).first()
        if existing_idem:
            return existing_idem.response_snapshot

        # Check for an already-active RefundRequest for this payment.
        existing_refund = (
            RefundRequest.objects.select_for_update()
            .filter(payment_transaction=payment)
            .exclude(refund_status__in=[RefundStatus.REFUND_FAILED, RefundStatus.CANCELLED])
            .first()
        )
        if existing_refund:
            raise ValidationError(
                {"detail": "A refund is already in progress for this payment. Use a new request only after the current one reaches REFUND_FAILED or CANCELLED."}
            )

        # Evaluate policy (stateless; safe inside transaction).
        outcome = evaluate_refund_policy(req, cause, returned_quantity, damaged_quantity)

        initial_status = (
            RefundStatus.PENDING_RETURN_VERIFICATION
            if outcome.requires_return_verification
            else RefundStatus.PENDING
        )

        sla_deadline = timezone.now() + timedelta(hours=_REFUND_PENDING_SLA_HOURS)

        refund_req = RefundRequest.objects.create(
            payment_transaction=payment,
            request=req,
            cause=cause,
            cause_note=cause_note,
            policy_rule=outcome.policy_rule,
            refund_percentage_applied=outcome.refund_percentage_applied,
            refund_amount=outcome.refund_amount,
            currency=payment.currency,
            refund_status=initial_status,
            idempotency_key=idempotency_key,
            initiated_by=actor,
            sla_deadline_at=sla_deadline if initial_status == RefundStatus.PENDING else None,
            initiated_at=timezone.now(),
        )

        payment.payment_status = PaymentTransaction.PaymentStatus.REFUND_PENDING
        payment.save(update_fields=["payment_status", "updated_at"])

        req.payment_status = ResourceRequest.PaymentStatus.REFUND_PENDING
        req.save(update_fields=["payment_status", "updated_at"])

        audit_details = {
            "refund_request_id": str(refund_req.id),
            "payment_id": str(payment.id),
            "cause": cause,
            "refund_amount": str(outcome.refund_amount),
            "refund_percentage": str(outcome.refund_percentage_applied),
            "requires_return_verification": outcome.requires_return_verification,
            "rationale": outcome.rationale,
        }
        _write_workflow_audit(req, "refund_initiated", "success", actor, details=audit_details)
        _write_workflow_audit(req, "refund_cause_recorded", "success", actor, details={"cause": cause, "cause_note": cause_note})
        _write_workflow_audit(req, "refund_policy_evaluated", "success", actor, details={"rationale": outcome.rationale})

        response = {
            "request_id": str(req.id),
            "refund_request_id": str(refund_req.id),
            "refund_status": refund_req.refund_status,
            "refund_amount": str(refund_req.refund_amount),
            "refund_percentage_applied": str(refund_req.refund_percentage_applied),
            "requires_return_verification": outcome.requires_return_verification,
            "payment_status": payment.payment_status,
        }

        RequestOperationIdempotency.objects.create(
            request=req,
            operation_type=RequestOperationIdempotency.OperationType.REFUND_INITIATE,
            idempotency_key=idempotency_key,
            request_hash="",
            response_snapshot=response,
            expires_at=timezone.now() + timedelta(days=2),
        )

    return response


# ---------------------------------------------------------------------------
# Refund confirmation (manual / admin-triggered)
# ---------------------------------------------------------------------------

def confirm_refund(
    req: ResourceRequest,
    actor,
    payment_status: str,
    provider_transaction_id: str = "",
) -> dict:
    _ensure_requesting_hospital_actor(req, actor, "confirm refund")
    _ensure_refund_confirmable(req)

    if payment_status not in (
        PaymentTransaction.PaymentStatus.REFUNDED,
        PaymentTransaction.PaymentStatus.REFUND_FAILED,
    ):
        raise ValidationError({"detail": "Invalid refund confirmation status."})

    with transaction.atomic():
        payment = (
            PaymentTransaction.objects.select_for_update()
            .filter(request=req, payment_status=PaymentTransaction.PaymentStatus.REFUND_PENDING)
            .order_by("-created_at")
            .first()
        )
        if not payment:
            raise ValidationError({"detail": "No payment in REFUND_PENDING state found for this request."})

        refund_req = (
            RefundRequest.objects.select_for_update()
            .filter(
                payment_transaction=payment,
                refund_status__in=[RefundStatus.PENDING, RefundStatus.PROCESSING],
            )
            .first()
        )
        if not refund_req:
            raise ValidationError({"detail": "No active RefundRequest found for this payment."})

        new_refund_status = (
            RefundStatus.REFUNDED
            if payment_status == PaymentTransaction.PaymentStatus.REFUNDED
            else RefundStatus.REFUND_FAILED
        )

        if payment_status == PaymentTransaction.PaymentStatus.REFUNDED:
            # Use engine-computed amount, not the full original payment amount.
            refund_amount = refund_req.refund_amount
            PaymentLedgerEntry.objects.create(
                payment_transaction=payment,
                hospital=req.requesting_hospital,
                entry_type=PaymentLedgerEntry.EntryType.REFUND_RECEIVED,
                amount=refund_amount,
                currency=refund_req.currency,
            )
            PaymentLedgerEntry.objects.create(
                payment_transaction=payment,
                hospital=req.supplying_hospital,
                entry_type=PaymentLedgerEntry.EntryType.REFUND_SENT,
                amount=refund_amount,
                currency=refund_req.currency,
            )
            req.payment_status = ResourceRequest.PaymentStatus.REFUNDED
            refund_req.completed_at = timezone.now()
        else:
            req.payment_status = ResourceRequest.PaymentStatus.REFUND_FAILED
            refund_req.failed_at = timezone.now()
            refund_req.retry_count += 1

        payment.payment_status = payment_status
        if provider_transaction_id:
            payment.provider_transaction_id = provider_transaction_id
        payment.save(update_fields=["payment_status", "provider_transaction_id", "updated_at"])

        refund_req.refund_status = new_refund_status
        refund_req.save(update_fields=["refund_status", "completed_at", "failed_at", "retry_count", "updated_at"])

        req.save(update_fields=["payment_status", "updated_at"])
        _write_workflow_audit(
            req,
            "refund_completed" if new_refund_status == RefundStatus.REFUNDED else "refund_gateway_failed",
            "success",
            actor,
            details={"refund_request_id": str(refund_req.id), "payment_status": payment_status},
        )

    return {
        "request_id": str(req.id),
        "refund_request_id": str(refund_req.id),
        "refund_status": refund_req.refund_status,
        "payment_status": payment.payment_status,
        "refund_amount": str(refund_req.refund_amount),
    }


def process_sslcommerz_webhook(payload: dict, headers: dict | None = None) -> dict:
    callback_payload = dict(payload or {})
    callback_status = str(callback_payload.get("status", "")).strip().upper()
    callback_tran_id = str(callback_payload.get("tran_id", "")).strip()
    callback_bank_tran_id = str(callback_payload.get("bank_tran_id", "")).strip()
    callback_val_id = str(callback_payload.get("val_id", "")).strip()

    payment = None
    callback_payment_id = str(callback_payload.get("value_a", "")).strip()
    if callback_payment_id:
        try:
            payment = PaymentTransaction.objects.select_related("request").get(id=callback_payment_id)
        except (PaymentTransaction.DoesNotExist, ValueError):
            payment = None
    if payment is None and callback_tran_id:
        payment = (
            PaymentTransaction.objects.select_related("request")
            .filter(provider_transaction_id=callback_tran_id)
            .order_by("-created_at")
            .first()
        )

    webhook_event = PaymentGatewayWebhookEvent.objects.create(
        provider=PaymentTransaction.Provider.SSLCOMMERZ,
        event_type=callback_status or "UNKNOWN",
        provider_transaction_id=callback_bank_tran_id or callback_tran_id,
        signature_valid=False,
        payload=callback_payload,
    )

    if payment is None:
        webhook_event.processing_status = "FAILED"
        webhook_event.error_message = "Payment transaction not found for callback payload."
        webhook_event.processed_at = timezone.now()
        webhook_event.save(update_fields=["processing_status", "error_message", "processed_at"])
        return {
            "accepted": False,
            "reason": "payment_not_found",
        }

    validation_payload = {}
    payment_status = _map_sslcommerz_callback_to_payment_status(callback_status)
    merged_payload = dict(callback_payload)
    signature_valid = False

    try:
        _validate_sslcommerz_callback_mapping(payment, callback_payload)

        if (
            payment.payment_status == PaymentTransaction.PaymentStatus.SUCCESS
            and payment_status != PaymentTransaction.PaymentStatus.SUCCESS
        ):
            raise ValidationError({"detail": "Replay callback cannot downgrade successful payment."})

        if payment_status == PaymentTransaction.PaymentStatus.SUCCESS:
            validation_payload = _sslcommerz_validate_session(callback_val_id)
            signature_valid = True
            merged_payload["validation"] = validation_payload

            _validate_sslcommerz_callback_mapping(payment, callback_payload, validation_payload)

            expected_amount = str(payment.amount)
            validated_amount = str(validation_payload.get("amount", "")).strip()
            validated_currency = str(validation_payload.get("currency", "")).strip().upper()
            if validated_amount and validated_amount != expected_amount:
                try:
                    expected_decimal = Decimal(expected_amount).quantize(Decimal("0.01"))
                    validated_decimal = Decimal(validated_amount).quantize(Decimal("0.01"))
                except Exception as exc:  # noqa: BLE001
                    raise ValidationError(
                        {
                            "detail": "SSLCommerz returned a non-decimal amount.",
                            "validated_amount": validated_amount,
                        }
                    ) from exc
                if validated_decimal != expected_decimal:
                    raise ValidationError(
                        {
                            "detail": "SSLCommerz amount mismatch.",
                            "expected_amount": expected_amount,
                            "validated_amount": validated_amount,
                        }
                    )
            if validated_currency and validated_currency != str(payment.currency).upper():
                raise ValidationError(
                    {
                        "detail": "SSLCommerz currency mismatch.",
                        "expected_currency": str(payment.currency).upper(),
                        "validated_currency": validated_currency,
                    }
                )
        else:
            signature_valid = True

        provider_transaction_id = callback_tran_id or payment.provider_transaction_id
        if payment_status in {
            PaymentTransaction.PaymentStatus.SUCCESS,
            PaymentTransaction.PaymentStatus.FAILED,
        }:
            confirm_result = confirm_payment_transaction(
                req=payment.request,
                actor=None,
                payment_status=payment_status,
                payment_id=payment.id,
                provider_transaction_id=provider_transaction_id,
                raw_payload=merged_payload,
                enforce_actor=False,
            )
        else:
            payment.raw_gateway_payload = merged_payload
            if provider_transaction_id:
                payment.provider_transaction_id = provider_transaction_id
            payment.save(update_fields=["raw_gateway_payload", "provider_transaction_id", "updated_at"])
            confirm_result = {
                "request_id": str(payment.request_id),
                "workflow_state": payment.request.workflow_state,
                "payment_status": payment.payment_status,
                "payment_id": str(payment.id),
            }

        redirect_url = _resolve_sslcommerz_redirect_url(
            payment=payment,
            payment_status=confirm_result.get("payment_status", payment.payment_status),
            callback_payload=callback_payload,
        )

        webhook_event.signature_valid = signature_valid
        webhook_event.payload = merged_payload
        webhook_event.processing_status = "PROCESSED"
        webhook_event.processed_at = timezone.now()
        webhook_event.error_message = ""
        webhook_event.save(
            update_fields=[
                "signature_valid",
                "payload",
                "processing_status",
                "processed_at",
                "error_message",
            ]
        )
        return {
            "accepted": True,
            "payment_id": str(payment.id),
            "request_id": str(payment.request_id),
            "payment_status": confirm_result.get("payment_status", payment.payment_status),
            "workflow_state": confirm_result.get("workflow_state", payment.request.workflow_state),
            "redirect_url": redirect_url,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to process SSLCommerz webhook for payment_id=%s", payment.id)
        webhook_event.signature_valid = False
        webhook_event.payload = merged_payload
        webhook_event.processing_status = "FAILED"
        webhook_event.processed_at = timezone.now()
        webhook_event.error_message = str(getattr(exc, "detail", exc))
        webhook_event.save(
            update_fields=[
                "signature_valid",
                "payload",
                "processing_status",
                "processed_at",
                "error_message",
            ]
        )
        return {
            "accepted": False,
            "reason": "processing_failed",
        }


def transfer_confirm(
    req: ResourceRequest,
    actor,
    qr_payload: str,
    quantity_received: int | None = None,
    notes: str = "",
    idempotency_key: str = "",
    payload: dict | None = None,
) -> dict:
    qr_payload_value = _normalize_qr_payload(qr_payload)
    if not qr_payload_value:
        raise ValidationError({"qrPayload": "qrPayload is required."})

    notes_value = str(notes or "")

    base_payload = payload or {}
    payload = {
        **base_payload,
        "qr_payload_hash": _hash_delivery_token(qr_payload_value),
        "quantity_received": quantity_received,
        "notes": notes_value,
    }

    existing = _get_existing_idempotent_response(
        req,
        RequestOperationIdempotency.OperationType.TRANSFER_CONFIRM,
        idempotency_key,
        payload,
    )
    if existing:
        return existing

    response_payload = confirm_workflow_completion(
        req=req,
        qr_payload=qr_payload_value,
        quantity_received=quantity_received,
        notes=notes_value,
        actor=actor,
    )

    _save_idempotent_response(
        req,
        RequestOperationIdempotency.OperationType.TRANSFER_CONFIRM,
        idempotency_key,
        payload,
        response_payload,
    )
    return response_payload


def expire_requests(limit: int = 500, actor=None) -> dict:
    now = timezone.now()
    expired_request_ids = list(
        ResourceRequest.objects.filter(
            workflow_state__in=REQUEST_EXPIRY_ACTIVE_WORKFLOW_STATES,
            expires_at__isnull=False,
            expires_at__lte=now,
        )
        .order_by("expires_at")
        .values_list("id", flat=True)[:limit]
    )

    expired_count = 0
    release_count = 0
    for request_id in expired_request_ids:
        with transaction.atomic():
            req = (
                ResourceRequest.objects.select_for_update()
                .select_related("supplying_hospital", "requesting_hospital", "catalog_item")
                .filter(id=request_id)
                .first()
            )
            if req is None:
                continue

            if (
                req.workflow_state not in REQUEST_EXPIRY_ACTIVE_WORKFLOW_STATES
                or req.expires_at is None
                or req.expires_at > now
            ):
                continue

            from_state = req.workflow_state
            transition_count = ResourceRequest.objects.filter(
                id=req.id,
                workflow_state__in=REQUEST_EXPIRY_ACTIVE_WORKFLOW_STATES,
                expires_at__isnull=False,
                expires_at__lte=now,
            ).update(
                workflow_state=ResourceRequest.WorkflowState.EXPIRED,
                status=ResourceRequest.Status.CANCELLED,
                expires_at=None,
                expired_at=now,
                cancellation_reason="expired",
                quantity_reserved=0,
                updated_at=now,
            )
            if transition_count != 1:
                continue

            active_res = list(
                ResourceRequestReservation.objects.select_for_update().filter(
                    request=req,
                    reservation_status=ResourceRequestReservation.ReservationStatus.ACTIVE,
                )
            )
            for reservation in active_res:
                batch = reservation.inventory_batch
                batch.quantity_reserved_in_batch = max(
                    0,
                    batch.quantity_reserved_in_batch - reservation.reserved_quantity,
                )
                batch.save(update_fields=["quantity_reserved_in_batch", "updated_at"])
                reservation.reservation_status = ResourceRequestReservation.ReservationStatus.EXPIRED
                reservation.released_at = now
                reservation.release_reason = "expired"
                reservation.save(update_fields=["reservation_status", "released_at", "release_reason", "updated_at"])
                release_count += 1

            reserved_qty_to_release = req.quantity_reserved or sum(r.reserved_quantity for r in active_res)
            source_inventory_snapshot = None
            if reserved_qty_to_release > 0:
                _inventory_gateway().release_stock(
                    request_id=req.id,
                    facility_id=req.supplying_hospital_id,
                    item_ref=req.catalog_item_id,
                    quantity=reserved_qty_to_release,
                    reason="expired",
                )
                source_inventory_snapshot = _get_inventory_snapshot(req.supplying_hospital_id, req.catalog_item_id)

            req.workflow_state = ResourceRequest.WorkflowState.EXPIRED
            req.status = ResourceRequest.Status.CANCELLED
            req.expires_at = None
            req.expired_at = now
            req.cancellation_reason = "expired"
            req.quantity_reserved = 0
            _write_transition(req, from_state, req.workflow_state, actor, reason="expired")
            _write_workflow_audit(req, "request_expired", "success", actor, details={})

            if source_inventory_snapshot is not None and reserved_qty_to_release > 0:
                _notify_inventory_update_after_commit(
                    req=req,
                    hospital=req.supplying_hospital,
                    catalog_item=req.catalog_item,
                    inventory=source_inventory_snapshot,
                    operation="request_cancelled_inventory",
                    actor=actor,
                    quantity_available_delta=0,
                    quantity_reserved_delta=-reserved_qty_to_release,
                    metadata={"reason": "expired"},
                )

            _publish_badge_event_after_commit(
                RequestExpiredEvent(
                    event_id=f"request-expired:{req.id}",
                    request_id=str(req.id),
                    requesting_hospital_id=str(req.requesting_hospital_id),
                    supplying_hospital_id=str(req.supplying_hospital_id),
                    was_pending_incoming=from_state == ResourceRequest.WorkflowState.PENDING,
                    was_pending_dispatch=from_state
                    in {
                        ResourceRequest.WorkflowState.APPROVED,
                        ResourceRequest.WorkflowState.RESERVED,
                        ResourceRequest.WorkflowState.PAYMENT_PENDING,
                        ResourceRequest.WorkflowState.PAYMENT_COMPLETED,
                    },
                )
            )
            expired_count += 1

    return {
        "expired_count": expired_count,
        "reservation_release_count": release_count,
    }


def get_payment_report_summary(hospital_id=None, date_from=None, date_to=None, actor=None) -> dict:
    scoped_hospital_id = hospital_id
    if actor and not _actor_is_super_admin(actor):
        actor_hospital_id = _actor_hospital_id(actor)
        if not actor_hospital_id:
            raise PermissionDenied("Authenticated user does not have a hospital context.")
        if scoped_hospital_id and str(scoped_hospital_id) != str(actor_hospital_id):
            raise PermissionDenied("Hospital admins can only view their own payment reports.")
        scoped_hospital_id = actor_hospital_id

    qs = PaymentLedgerEntry.objects.all()
    if scoped_hospital_id:
        qs = qs.filter(hospital_id=scoped_hospital_id)
    if date_from:
        qs = qs.filter(created_at__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__lte=date_to)

    sent_total = Decimal("0.00")
    received_total = Decimal("0.00")
    for row in qs:
        if row.entry_type in (
            PaymentLedgerEntry.EntryType.SENT,
            PaymentLedgerEntry.EntryType.REFUND_SENT,
        ):
            sent_total += row.amount
        if row.entry_type in (
            PaymentLedgerEntry.EntryType.RECEIVED,
            PaymentLedgerEntry.EntryType.REFUND_RECEIVED,
        ):
            received_total += row.amount

    net_balance = float(received_total - sent_total)

    return {
        "report_id": f"rep_{uuid.uuid4().hex[:12]}",
        "hospital_id": str(scoped_hospital_id) if scoped_hospital_id else None,
        "currency": "BDT",
        "summary": {
            "total_sent": float(sent_total),
            "total_received": float(received_total),
            "net_balance": net_balance,
        },
        "transactions": [],
    }


def trigger_payment_reconciliation(actor=None) -> PaymentReconciliationRun:
    actor_hospital_id = None
    if actor and not _actor_is_super_admin(actor):
        actor_hospital_id = _actor_hospital_id(actor)
        if not actor_hospital_id:
            raise PermissionDenied("Authenticated user does not have a hospital context.")

    run = PaymentReconciliationRun.objects.create(
        run_status=PaymentReconciliationRun.RunStatus.RUNNING,
        provider=PaymentTransaction.Provider.SSLCOMMERZ,
    )

    pending = PaymentTransaction.objects.filter(payment_status=PaymentTransaction.PaymentStatus.PENDING)
    if actor_hospital_id:
        pending = pending.filter(
            Q(payer_hospital_id=actor_hospital_id) | Q(receiver_hospital_id=actor_hospital_id)
        )

    pending_transactions = list(pending.select_related("request"))
    checked_count = len(pending_transactions)
    corrected = 0
    for tx in pending_transactions:
        # Deterministic fallback policy for stale pending records in this implementation.
        tx.payment_status = PaymentTransaction.PaymentStatus.FAILED
        tx.failed_at = timezone.now()
        tx.failure_code = "reconciliation_timeout"
        tx.save(update_fields=["payment_status", "failed_at", "failure_code", "updated_at"])

        req = tx.request
        if req and req.workflow_state == ResourceRequest.WorkflowState.PAYMENT_PENDING:
            from_state = req.workflow_state
            req.payment_status = ResourceRequest.PaymentStatus.FAILED
            req.workflow_state = ResourceRequest.WorkflowState.FAILED
            req.failed_reason = req.failed_reason or "payment_reconciliation_timeout"
            req_update_fields = ["payment_status", "workflow_state", "failed_reason", "updated_at"]
            req_update_fields.extend(_stop_request_sla_timer(req))
            req.save(update_fields=req_update_fields)
            _write_transition(req, from_state, req.workflow_state, actor, reason="payment_reconciliation_timeout")
            _write_workflow_audit(
                req,
                "payment_reconciled_timeout",
                "success",
                actor,
                details={"payment_id": str(tx.id)},
            )
        corrected += 1

    run.checked_count = checked_count
    run.corrected_count = corrected
    run.failed_count = 0
    run.run_status = PaymentReconciliationRun.RunStatus.COMPLETED
    run.completed_at = timezone.now()
    run.save(
        update_fields=[
            "checked_count",
            "corrected_count",
            "failed_count",
            "run_status",
            "completed_at",
        ]
    )
    return run
