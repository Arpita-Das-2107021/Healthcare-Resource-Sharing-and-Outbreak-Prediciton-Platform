import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import AppLayout from '@/components/layout/AppLayout';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { requestsApi, shipmentsApi, staffApi } from '@/services/api';
import { RequestStatusStepper } from '@/components/request/RequestStatusStepper';
import { SLATimer } from '@/components/request/SLATimer';
import { ClinicalMetadataBadges } from '@/components/resource/ClinicalMetadataBadges';
import { ChevronDown, ChevronUp, Loader2, AlertTriangle, Truck } from 'lucide-react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { useToast } from '@/hooks/use-toast';
import { useAuth } from '@/contexts/AuthContext';
import {
  validateTransferConfirmInput,
} from '@/utils/deliveryConfirmation';
import { buildPublicUrlFromLocation } from '@/utils/paymentReturnUrl';
import { RESOURCE_SHARES_UPDATED_EVENT } from '@/constants/events';
import { hasAnyPermission } from '@/lib/rbac';

type WorkflowState =
  | 'PENDING'
  | 'APPROVED'
  | 'RESERVED'
  | 'PAYMENT_PENDING'
  | 'PAYMENT_COMPLETED'
  | 'IN_TRANSIT'
  | 'RETURNING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'
  | 'EXPIRED';

type CompletionStage = 'NOT_STARTED' | 'SENDER_CONFIRMED' | 'RECEIVER_CONFIRMED';

type SortBy = 'newest' | 'status' | 'hospital' | 'urgency';
type WorkflowTab = 'incoming' | 'outgoing';
type RequestWorkflowView = 'combined' | WorkflowTab;

interface RequestWorkflowProps {
  view?: RequestWorkflowView;
}

interface StaffRow {
  id: string;
  fullName: string;
  phone?: string;
}

interface ShipmentInfo {
  id: string;
  requestId?: string;
  status: string;
  dispatchToken?: string;
  dispatchQrPayload?: string;
  dispatchQrImageUrl?: string;
  returnToken?: string;
  deliveryPersonnelName?: string;
  deliveryPersonnelPhone?: string;
  vehicleInfo?: string;
  tokenExpiresAt?: string;
}

interface MappedRequest {
  id: string;
  resourceName: string;
  catalogItemId?: string;
  resourceType: 'drugs' | 'blood' | 'organs' | 'equipment';
  requestingHospitalId?: string;
  supplyingHospitalId?: string;
  requestingHospital: string;
  providingHospital: string;
  quantity: number;
  urgency: 'routine' | 'urgent' | 'critical';
  status: string;
  requestedAt: string;
  justification?: string;
  bloodType?: string;
  coldChainRequired?: boolean;
  coldChainTemp?: string;
  lotNumber?: string;
  expiryDate?: string;
  reservationExpiry?: string;
  estimatedDelivery?: string;
  paymentRequired: boolean | null;
  paymentNote?: string;
  paymentStatus?: string;
  failedReason?: string;
  latestPaymentId?: string;
  latestPaymentTransactionStatus?: string;
  shipmentStatus?: string;
  returnToken?: string;
  shipmentId?: string;
  dispatchToken?: string;
  dispatchQrPayload?: string;
  dispatchQrImageUrl?: string;
  completionStage: CompletionStage;
  senderConfirmedAt?: string;
  receiverConfirmedAt?: string;
  dispatchTokenExpiresAt?: string;
}

interface DispatchSnapshot {
  requestId?: string;
  shipmentId?: string;
  dispatchToken?: string;
  dispatchQrPayload?: string;
  dispatchQrImageUrl?: string;
  dispatchTokenExpiresAt?: string;
}

interface DispatchCacheEntry extends DispatchSnapshot {
  workflowState?: string;
  completionStage?: CompletionStage;
}

type DispatchCacheByRequestId = Record<string, DispatchCacheEntry>;

const DISPATCH_QR_CACHE_STORAGE_KEY = 'request-workflow:dispatch-qr-cache:v1';
const DISPATCH_SCAN_REQUEST_CONTEXT_KEY = 'dispatch-scan:request-id';

const hasDispatchSnapshotData = (snapshot?: DispatchSnapshot | null): boolean =>
  Boolean(
    snapshot?.dispatchToken ||
      snapshot?.dispatchQrPayload ||
      snapshot?.dispatchQrImageUrl ||
      snapshot?.shipmentId ||
      snapshot?.dispatchTokenExpiresAt
  );

const shouldRetainDispatchCache = (workflowState: string, completionStage: CompletionStage): boolean => {
  const mappedState = mapStatus(workflowState);
  if (TERMINAL_WORKFLOW_STATES.has(mappedState)) {
    return false;
  }
  if (completionStage === 'RECEIVER_CONFIRMED') {
    return false;
  }
  return true;
};

const readDispatchQrCache = (): DispatchCacheByRequestId => {
  if (typeof window === 'undefined') {
    return {};
  }

  try {
    const raw = window.localStorage.getItem(DISPATCH_QR_CACHE_STORAGE_KEY);
    if (!raw) {
      return {};
    }

    const parsed = JSON.parse(raw) as unknown;
    const root = asRecord(parsed);
    const cache: DispatchCacheByRequestId = {};

    Object.entries(root).forEach(([requestId, value]) => {
      const record = asRecord(value);
      const entry: DispatchCacheEntry = {
        requestId,
        shipmentId: readIdFromUnknown(record.shipmentId ?? record.shipment_id) || undefined,
        dispatchToken: readString(record, ['dispatchToken', 'dispatch_token']) || undefined,
        dispatchQrPayload:
          takeOpaqueQrPayload(
            readString(record, ['dispatchQrPayload', 'dispatch_qr_payload']),
            readString(record, ['dispatchToken', 'dispatch_token'])
          ) || undefined,
        dispatchQrImageUrl: readString(record, ['dispatchQrImageUrl', 'dispatch_qr_image_url']) || undefined,
        dispatchTokenExpiresAt: readDateTime(record, ['dispatchTokenExpiresAt', 'dispatch_token_expires_at']) || undefined,
        workflowState: readString(record, ['workflowState', 'workflow_state']) || undefined,
        completionStage:
          readString(record, ['completionStage', 'completion_stage']) === 'RECEIVER_CONFIRMED'
            ? 'RECEIVER_CONFIRMED'
            : readString(record, ['completionStage', 'completion_stage']) === 'SENDER_CONFIRMED'
              ? 'SENDER_CONFIRMED'
              : 'NOT_STARTED',
      };

      if (hasDispatchSnapshotData(entry)) {
        cache[requestId] = entry;
      }
    });

    return cache;
  } catch {
    return {};
  }
};

const writeDispatchQrCache = (cache: DispatchCacheByRequestId) => {
  if (typeof window === 'undefined') {
    return;
  }

  try {
    window.localStorage.setItem(DISPATCH_QR_CACHE_STORAGE_KEY, JSON.stringify(cache));
  } catch {
    // Ignore localStorage write failures.
  }
};

const WORKFLOW_STATES: WorkflowState[] = [
  'PENDING',
  'APPROVED',
  'RESERVED',
  'PAYMENT_PENDING',
  'PAYMENT_COMPLETED',
  'IN_TRANSIT',
  'RETURNING',
  'COMPLETED',
  'FAILED',
  'CANCELLED',
  'EXPIRED',
];
const STAGE_LABEL: Record<WorkflowState, string> = {
  PENDING: 'Pending',
  APPROVED: 'Approved',
  RESERVED: 'Reserved',
  PAYMENT_PENDING: 'Payment Pending',
  PAYMENT_COMPLETED: 'Payment Completed',
  IN_TRANSIT: 'In Transit',
  RETURNING: 'Returning',
  COMPLETED: 'Completed',
  FAILED: 'Failed',
  CANCELLED: 'Cancelled',
  EXPIRED: 'Expired',
};

const TERMINAL_WORKFLOW_STATES = new Set<WorkflowState>(['COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED']);

const REQUEST_APPROVE_PERMISSION_CODES = ['hospital:request.approve', 'hospital:request.supervise'];
const REQUEST_DISPATCH_PERMISSION_CODES = ['hospital:request.dispatch', 'hospital:request.supervise'];
const REQUEST_TRANSFER_CONFIRM_PERMISSION_CODES = ['hospital:request.transfer.confirm'];
const REQUEST_RETURN_VERIFY_PERMISSION_CODES = ['hospital:request.return.verify'];

const normalizeStatus = (status: string): string => (status || '').toLowerCase().replace(/[-\s]/g, '_');

const mapStatus = (status: string): WorkflowState => {
  const normalized = normalizeStatus(status);
  if (normalized === 'pending' || normalized === 'requested' || normalized === 'new') return 'PENDING';
  if (normalized === 'approved') return 'APPROVED';
  if (normalized === 'reserved') return 'RESERVED';
  if (normalized === 'payment_pending' || normalized === 'awaiting_payment') return 'PAYMENT_PENDING';
  if (normalized === 'payment_completed' || normalized === 'paid') return 'PAYMENT_COMPLETED';
  if (normalized === 'dispatched' || normalized === 'in_transit' || normalized === 'intransit' || normalized === 'shipped') return 'IN_TRANSIT';
  if (normalized === 'returning' || normalized === 'return_pending' || normalized === 'return_initiated') return 'RETURNING';
  if (normalized === 'delivered' || normalized === 'fulfilled' || normalized === 'received' || normalized === 'completed' || normalized === 'closed') {
    return 'COMPLETED';
  }
  if (normalized === 'failed') return 'FAILED';
  if (normalized === 'cancelled' || normalized === 'canceled' || normalized === 'rejected') return 'CANCELLED';
  if (normalized === 'expired') return 'EXPIRED';
  return 'PENDING';
};

const mapCompletionStage = (
  rawCompletionStage: string,
  workflowState: WorkflowState,
  senderConfirmedAt?: string,
  receiverConfirmedAt?: string,
  senderConfirmed = false,
  receiverConfirmed = false,
): CompletionStage => {
  const normalized = rawCompletionStage.trim().toUpperCase().replace(/[-\s]+/g, '_');

  if (normalized === 'SENDER_CONFIRMED') return 'SENDER_CONFIRMED';
  if (normalized === 'RECEIVER_CONFIRMED') return 'RECEIVER_CONFIRMED';

  if (receiverConfirmed || receiverConfirmedAt || workflowState === 'COMPLETED') {
    return 'RECEIVER_CONFIRMED';
  }
  if (senderConfirmed || senderConfirmedAt) {
    return 'SENDER_CONFIRMED';
  }

  return 'NOT_STARTED';
};

const takeToken = (...values: unknown[]): string => {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return '';
};

const takeOpaqueQrPayload = (...values: unknown[]): string => {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
  }
  return '';
};

const isOpaqueQrPayloadPresent = (value: string): boolean => {
  return typeof value === 'string' && value.trim().length > 0;
};

const asRecord = (value: unknown): Record<string, unknown> => {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
};

const readString = (record: Record<string, unknown>, keys: string[], fallback = ''): string => {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
  }
  return fallback;
};

const readBoolean = (record: Record<string, unknown>, keys: string[]): boolean | null => {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'boolean') {
      return value;
    }
  }
  return null;
};

const readDateTime = (record: Record<string, unknown>, keys: string[], fallback = ''): string => {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return fallback;
};

const DEFAULT_REQUESTED_AT = '1970-01-01T00:00:00.000Z';

const toEpochMillis = (value?: string): number => {
  const parsed = Date.parse(value || '');
  return Number.isFinite(parsed) ? parsed : 0;
};

const isExpiredTimestamp = (value?: string): boolean => {
  if (!value) return false;
  const expiresAt = Date.parse(value);
  if (!Number.isFinite(expiresAt)) return false;
  return expiresAt <= Date.now();
};

const readIdFromUnknown = (value: unknown): string => {
  if (typeof value === 'string' || typeof value === 'number') {
    const parsed = String(value).trim();
    return parsed;
  }

  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    return readString(record, ['id', 'uuid', 'pk']);
  }

  return '';
};

const extractCollection = (payload: unknown): unknown[] => {
  const root = asRecord(payload);
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(root.data)) return root.data as unknown[];
  if (Array.isArray(root.results)) return root.results as unknown[];

  const data = asRecord(root.data);
  if (Array.isArray(data.results)) return data.results as unknown[];

  return [];
};

const parseGatewayRedirectUrl = (payload: unknown): string => {
  const root = asRecord(payload);
  const data = asRecord(root.data);
  const payment = asRecord(data.payment);

  return takeToken(
    data.gateway_redirect_url,
    data.redirect_url,
    data.checkout_url,
    data.payment_url,
    payment.gateway_redirect_url,
    payment.redirect_url,
    payment.checkout_url,
    payment.payment_url,
    root.gateway_redirect_url,
    root.redirect_url,
    root.checkout_url,
    root.payment_url
  );
};

const buildPaymentIdempotencyKey = (requestId: string): string => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `req-payment-${requestId}-${crypto.randomUUID()}`;
  }
  return `req-payment-${requestId}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
};

const GATEWAY_RETURN_QUERY_KEYS = [
  'payment_request_id',
  'request_id',
  'payment_status',
  'status',
  'result',
  'gateway_status',
  'transaction_status',
  'payment_id',
  'paymentId',
  'provider_transaction_id',
  'transaction_id',
  'tran_id',
  'val_id',
  'cancelled',
];

const unwrapDataRecord = (payload: unknown): Record<string, unknown> => {
  const root = asRecord(payload);
  const data = asRecord(root.data);
  return Object.keys(data).length > 0 ? data : root;
};

const normalizeResourceType = (value: string): MappedRequest['resourceType'] => {
  const normalized = normalizeStatus(value);
  if (normalized === 'blood') return 'blood';
  if (normalized === 'organs' || normalized === 'organ') return 'organs';
  if (normalized === 'equipment') return 'equipment';
  return 'drugs';
};

const buildTransferIdempotencyKey = (requestId: string, stage: 'sender' | 'receiver'): string => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `request-transfer-${requestId}-${stage}-${crypto.randomUUID()}`;
  }
  return `request-transfer-${requestId}-${stage}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
};

const mapTransferConfirmationError = (
  error: unknown,
): { title: string; description: string; clearEnteredToken?: boolean } => {
  const message = getErrorMessage(error);
  const normalized = message.toLowerCase();
  const errorRecord = error as { status?: number };

  if (
    errorRecord.status === 403 ||
    normalized.includes('does not belong to your healthcare facility') ||
    normalized.includes('not authorized') ||
    normalized.includes('permission denied') ||
    normalized.includes('forbidden')
  ) {
    return {
      title: 'Delivery confirmation denied',
      description: message,
      clearEnteredToken: true,
    };
  }

  if (normalized.includes('qr already used') || normalized.includes('delivery token is already used') || normalized.includes('already used') || normalized.includes('already confirmed')) {
    return {
      title: 'Shipment already confirmed',
      description: message,
      clearEnteredToken: true,
    };
  }

  if (normalized.includes('expired') && (normalized.includes('qr') || normalized.includes('token'))) {
    return {
      title: 'Expired QR code',
      description: message,
      clearEnteredToken: true,
    };
  }

  if (normalized.includes('invalid') && (normalized.includes('qr') || normalized.includes('token'))) {
    return {
      title: 'Invalid delivery QR',
      description: message,
      clearEnteredToken: true,
    };
  }

  if (
    normalized.includes('qrpayload') ||
    normalized.includes('qr payload')
  ) {
    return {
      title: 'QR payload required',
      description: message,
      clearEnteredToken: true,
    };
  }

  if (
    normalized.includes('invalid stage actor') ||
    (normalized.includes('actor') && normalized.includes('invalid')) ||
    (normalized.includes('actor') && normalized.includes('not permitted')) ||
    normalized.includes('supplier-side actor') ||
    normalized.includes('requester-side actor')
  ) {
    return {
      title: 'Action not allowed for this actor',
      description: message,
      clearEnteredToken: true,
    };
  }

  if (
    normalized.includes('workflow already completed') ||
    normalized.includes('terminal workflow') ||
    normalized.includes('already completed') ||
    normalized.includes('already closed') ||
    normalized.includes('already cancelled')
  ) {
    return {
      title: 'Workflow is already closed',
      description: message,
      clearEnteredToken: true,
    };
  }

  if (
    normalized.includes('quantity') &&
    (normalized.includes('exceed') || normalized.includes('approved') || normalized.includes('reserved') || normalized.includes('validation'))
  ) {
    return {
      title: 'Quantity validation failed',
      description: message,
    };
  }

  return {
    title: 'Receiver confirmation failed',
    description: message,
  };
};

const mapApiRequest = (req: unknown): MappedRequest => {
  const request = asRecord(req);
  const resource = asRecord(request.resource);
  const metadata = asRecord(request.metadata);
  const completion = asRecord(request.completion);
  const transfer = asRecord(request.transfer);
  const shipment = asRecord(request.shipment);
  const latestShipment = asRecord(request.latest_shipment);
  const payment = asRecord(request.payment);

  const priority = readString(request, ['priority'], 'normal').toLowerCase();
  const urgency = readString(request, ['urgency'], priority === 'emergency' ? 'critical' : priority === 'urgent' ? 'urgent' : 'routine');
  const workflowState = String(request.workflow_state || request.status || request.request_status || 'PENDING');
  const mappedWorkflowState = mapStatus(workflowState);

  const senderConfirmedFlag = [
    readBoolean(request, ['sender_confirmed', 'is_sender_confirmed', 'senderConfirmed']),
    readBoolean(completion, ['sender_confirmed', 'is_sender_confirmed', 'senderConfirmed']),
    readBoolean(transfer, ['sender_confirmed', 'is_sender_confirmed', 'senderConfirmed']),
    readBoolean(shipment, ['sender_confirmed', 'is_sender_confirmed', 'senderConfirmed']),
    readBoolean(latestShipment, ['sender_confirmed', 'is_sender_confirmed', 'senderConfirmed']),
  ].some((value) => value === true);

  const receiverConfirmedFlag = [
    readBoolean(request, ['receiver_confirmed', 'is_receiver_confirmed', 'receiverConfirmed']),
    readBoolean(completion, ['receiver_confirmed', 'is_receiver_confirmed', 'receiverConfirmed']),
    readBoolean(transfer, ['receiver_confirmed', 'is_receiver_confirmed', 'receiverConfirmed']),
    readBoolean(shipment, ['receiver_confirmed', 'is_receiver_confirmed', 'receiverConfirmed']),
    readBoolean(latestShipment, ['receiver_confirmed', 'is_receiver_confirmed', 'receiverConfirmed']),
  ].some((value) => value === true);

  const senderConfirmedAt = takeToken(
    readDateTime(request, ['sender_confirmed_at', 'sender_confirmation_at', 'sender_confirm_at', 'dispatch_confirmed_at']),
    readDateTime(completion, ['sender_confirmed_at', 'sender_confirmation_at', 'sender_confirm_at', 'dispatch_confirmed_at']),
    readDateTime(transfer, ['sender_confirmed_at', 'sender_confirmation_at', 'sender_confirm_at', 'dispatch_confirmed_at']),
    readDateTime(shipment, ['sender_confirmed_at', 'sender_confirmation_at']),
    readDateTime(latestShipment, ['sender_confirmed_at', 'sender_confirmation_at'])
  );

  const receiverConfirmedAt = takeToken(
    readDateTime(request, ['receiver_confirmed_at', 'receiver_confirmation_at', 'receiver_confirm_at', 'delivery_confirmed_at']),
    readDateTime(completion, ['receiver_confirmed_at', 'receiver_confirmation_at', 'receiver_confirm_at', 'delivery_confirmed_at']),
    readDateTime(transfer, ['receiver_confirmed_at', 'receiver_confirmation_at', 'receiver_confirm_at', 'delivery_confirmed_at']),
    readDateTime(shipment, ['receiver_confirmed_at', 'receiver_confirmation_at']),
    readDateTime(latestShipment, ['receiver_confirmed_at', 'receiver_confirmation_at'])
  );

  const rawCompletionStage = takeToken(
    readString(request, ['completion_stage', 'completionStage', 'transfer_completion_stage']),
    readString(completion, ['completion_stage', 'completionStage', 'stage']),
    readString(transfer, ['completion_stage', 'completionStage', 'stage']),
    readString(shipment, ['completion_stage', 'completionStage']),
    readString(latestShipment, ['completion_stage', 'completionStage'])
  );

  const completionStage = mapCompletionStage(
    rawCompletionStage,
    mappedWorkflowState,
    senderConfirmedAt,
    receiverConfirmedAt,
    senderConfirmedFlag,
    receiverConfirmedFlag,
  );

  const requestDispatchSnapshot = getDispatchSnapshotFromRecord(request);
  const completionDispatchSnapshot = getDispatchSnapshotFromRecord(completion);
  const transferDispatchSnapshot = getDispatchSnapshotFromRecord(transfer);
  const shipmentDispatchSnapshot = getDispatchSnapshotFromRecord(shipment);
  const latestShipmentDispatchSnapshot = getDispatchSnapshotFromRecord(latestShipment);

  const dispatchQrPayload =
    takeOpaqueQrPayload(
      requestDispatchSnapshot.dispatchQrPayload,
      completionDispatchSnapshot.dispatchQrPayload,
      transferDispatchSnapshot.dispatchQrPayload,
      shipmentDispatchSnapshot.dispatchQrPayload,
      latestShipmentDispatchSnapshot.dispatchQrPayload,
    ) || undefined;

  const dispatchToken =
    takeToken(
      requestDispatchSnapshot.dispatchToken,
      completionDispatchSnapshot.dispatchToken,
      transferDispatchSnapshot.dispatchToken,
      shipmentDispatchSnapshot.dispatchToken,
      latestShipmentDispatchSnapshot.dispatchToken,
    ) || undefined;

  const dispatchQrImageUrl =
    takeToken(
      requestDispatchSnapshot.dispatchQrImageUrl,
      completionDispatchSnapshot.dispatchQrImageUrl,
      transferDispatchSnapshot.dispatchQrImageUrl,
      shipmentDispatchSnapshot.dispatchQrImageUrl,
      latestShipmentDispatchSnapshot.dispatchQrImageUrl,
    ) || undefined;

  const dispatchTokenExpiresAt =
    takeToken(
      requestDispatchSnapshot.dispatchTokenExpiresAt,
      completionDispatchSnapshot.dispatchTokenExpiresAt,
      transferDispatchSnapshot.dispatchTokenExpiresAt,
      shipmentDispatchSnapshot.dispatchTokenExpiresAt,
      latestShipmentDispatchSnapshot.dispatchTokenExpiresAt,
    ) || undefined;

  return {
    id: String(request.id || ''),
    resourceName: readString(request, ['catalog_item_name', 'resource_name'], readString(resource, ['name'], 'Unknown Resource')),
    catalogItemId: readString(request, ['catalog_item', 'catalog_item_id']) || undefined,
    resourceType: normalizeResourceType(readString(request, ['resource_type'], readString(resource, ['type'], 'drugs'))),
    requestingHospitalId: String(request.requesting_hospital || request.requesting_hospital_id || request.hospital_id || ''),
    supplyingHospitalId: String(request.supplying_hospital || request.supplying_hospital_id || ''),
    requestingHospital: readString(request, ['requesting_hospital_name'], readString(asRecord(request.hospital), ['name'], readString(request, ['hospital_name']))),
    providingHospital: readString(request, ['supplying_hospital_name', 'providing_hospital_name', 'source_hospital_name']),
    quantity: Number(request.quantity_requested ?? request.quantity ?? 0) || 0,
    urgency: (urgency as MappedRequest['urgency']) || 'routine',
    status: workflowState,
    requestedAt: readDateTime(
      request,
      ['created_at', 'requested_at', 'requestedAt', 'submitted_at', 'request_date', 'updated_at'],
      DEFAULT_REQUESTED_AT,
    ),
    justification: readString(request, ['justification', 'notes']) || undefined,
    bloodType: readString(request, ['blood_type'], readString(metadata, ['blood_type'])) || undefined,
    coldChainRequired:
      (typeof request.cold_chain_required === 'boolean' ? request.cold_chain_required : undefined) ||
      (typeof metadata.cold_chain_required === 'boolean' ? metadata.cold_chain_required : undefined),
    coldChainTemp: readString(request, ['cold_chain_temp'], readString(metadata, ['cold_chain_temp'])) || undefined,
    lotNumber: readString(request, ['lot_number'], readString(metadata, ['lot_number'])) || undefined,
    expiryDate: readString(request, ['expiry_date'], readString(metadata, ['expiry_date'])) || undefined,
    reservationExpiry: readString(request, ['reservation_expiry', 'reserved_until']) || undefined,
    estimatedDelivery: readString(request, ['estimated_delivery', 'estimated_delivery_at']) || undefined,
    paymentRequired: readBoolean(request, ['payment_required', 'is_payment_required']),
    paymentNote: readString(request, ['payment_note']) || undefined,
    paymentStatus: readString(request, ['payment_status'], readString(payment, ['status'])) || undefined,
    failedReason: readString(request, ['failed_reason']) || undefined,
    latestPaymentId: readString(request, ['latest_payment_id'], readString(payment, ['id', 'payment_id'])) || undefined,
    latestPaymentTransactionStatus: readString(request, ['latest_payment_transaction_status']) || undefined,
    shipmentStatus: readString(request, ['shipment_status'], readString(shipment, ['status'], readString(latestShipment, ['status']))) || undefined,
    returnToken: takeToken(request.return_token),
    shipmentId:
      readIdFromUnknown(request.shipment_id) ||
      readIdFromUnknown(request.latest_shipment_id) ||
      readIdFromUnknown(request.shipment) ||
      readIdFromUnknown(request.latest_shipment) ||
      readIdFromUnknown(shipment.id) ||
      readIdFromUnknown(latestShipment.id) ||
      undefined,
    dispatchToken,
    dispatchQrPayload: dispatchQrPayload || dispatchToken,
    dispatchQrImageUrl,
    completionStage,
    senderConfirmedAt: senderConfirmedAt || undefined,
    receiverConfirmedAt: receiverConfirmedAt || undefined,
    dispatchTokenExpiresAt,
  };
};

const mapShipment = (shipmentPayload: unknown): ShipmentInfo => {
  const shipment = asRecord(shipmentPayload);
  const requestRef =
    shipment.request ??
    shipment.request_id ??
    shipment.resource_request ??
    shipment.resource_request_id;

  const dispatchSnapshot = getDispatchSnapshotFromRecord(shipment);

  return {
    id: readIdFromUnknown(shipment.id),
    requestId: readIdFromUnknown(requestRef),
    status: readString(shipment, ['status', 'workflow_state']),
    dispatchToken: dispatchSnapshot.dispatchToken,
    dispatchQrPayload: dispatchSnapshot.dispatchQrPayload,
    dispatchQrImageUrl: dispatchSnapshot.dispatchQrImageUrl,
    returnToken: undefined,
    deliveryPersonnelName: readString(shipment, ['rider_name', 'driver_name', 'delivery_personnel_name']),
    deliveryPersonnelPhone: readString(shipment, ['rider_phone', 'driver_phone', 'delivery_personnel_phone']),
    vehicleInfo: readString(shipment, ['vehicle_info']),
    tokenExpiresAt: dispatchSnapshot.dispatchTokenExpiresAt,
  };
};

const getDispatchSnapshotFromRecord = (record: Record<string, unknown>): DispatchSnapshot => {
  const deliveryQr = asRecord(record.delivery_qr);
  const dispatchContext = asRecord(record.dispatch_context);
  const tokens = asRecord(record.tokens);

  const dispatchToken =
    takeToken(
      readString(deliveryQr, ['dispatch_token', 'dispatchToken', 'token']),
      readString(dispatchContext, ['dispatch_token', 'dispatchToken', 'token']),
      readString(tokens, ['dispatch_token', 'dispatchToken']),
      readString(record, ['dispatch_token', 'dispatchToken'])
    ) || undefined;

  const dispatchQrPayload =
    takeOpaqueQrPayload(
      readString(deliveryQr, ['qrPayload', 'qr_payload', 'qr_signature', 'qrSignature', 'signature']),
      readString(dispatchContext, ['qrPayload', 'qr_payload', 'qr_signature', 'qrSignature', 'signature']),
      readString(tokens, ['dispatch_qr_payload', 'dispatch_qr_signature', 'dispatch_signature', 'qr_signature', 'signature']),
      readString(record, ['dispatch_qr_payload', 'dispatchQrPayload', 'qr_payload', 'qrPayload', 'qr_signature', 'qrSignature', 'signature', 'signed_qr_payload']),
      dispatchToken
    ) || undefined;

  const dispatchQrImageUrl =
    takeToken(
      readString(deliveryQr, ['qrImageUrl', 'qr_image_url', 'image_url', 'qr_code_url', 'url']),
      readString(dispatchContext, ['qr_image_url', 'qrImageUrl', 'dispatch_qr_code_url', 'dispatchQrCodeUrl']),
      readString(tokens, ['dispatch_qr_code_url', 'dispatch_qr_image_url']),
      readString(record, ['dispatch_qr_code_url', 'dispatchQrCodeUrl', 'dispatch_qr_image_url', 'dispatchQrImageUrl', 'qr_code_url']),
    ) || undefined;

  const dispatchTokenExpiresAt =
    takeToken(
      readDateTime(deliveryQr, ['expiresAt', 'expires_at', 'expiry_at', 'token_expires_at']),
      readDateTime(dispatchContext, ['expiresAt', 'expires_at', 'token_expires_at']),
      readDateTime(record, ['dispatch_token_expires_at', 'dispatchTokenExpiresAt', 'token_expires_at']),
    ) || undefined;

  return {
    requestId:
      readIdFromUnknown(record.request) ||
      readIdFromUnknown(record.request_id) ||
      readIdFromUnknown(deliveryQr.requestId ?? deliveryQr.request_id) ||
      undefined,
    shipmentId:
      readIdFromUnknown(record.shipment) ||
      readIdFromUnknown(record.shipment_id) ||
      readIdFromUnknown(record.latest_shipment) ||
      readIdFromUnknown(record.latest_shipment_id) ||
      undefined,
    dispatchToken,
    dispatchQrPayload,
    dispatchQrImageUrl,
    dispatchTokenExpiresAt,
  };
};

const extractDispatchSessionFromPayload = (payload: unknown, fallbackRequestId: string): DispatchSnapshot | null => {
  const root = asRecord(payload);
  const data = asRecord(root.data);
  const candidateRecords: Record<string, unknown>[] = [
    data,
    root,
  ];

  for (const candidate of candidateRecords) {
    if (Object.keys(candidate).length === 0) {
      continue;
    }

    const snapshot = getDispatchSnapshotFromRecord(candidate);
    if (snapshot.dispatchToken || snapshot.dispatchQrPayload || snapshot.dispatchQrImageUrl || snapshot.shipmentId) {
      return {
        ...snapshot,
        requestId: snapshot.requestId || fallbackRequestId,
      };
    }
  }

  return null;
};

const mapShipmentFromPayload = (payload: unknown, requestId: string): ShipmentInfo | null => {
  const root = asRecord(payload);
  const data = asRecord(root.data);
  const snapshot = extractDispatchSessionFromPayload(payload, requestId);

  if (!snapshot) {
    return null;
  }

  const shipmentRecord =
    asRecord(data.shipment).id ? asRecord(data.shipment) :
    asRecord(root.shipment).id ? asRecord(root.shipment) :
    asRecord(data.latest_shipment).id ? asRecord(data.latest_shipment) :
    asRecord(root.latest_shipment).id ? asRecord(root.latest_shipment) :
    {};

  return {
    id: snapshot.shipmentId || readIdFromUnknown(shipmentRecord.id),
    requestId: snapshot.requestId || requestId,
    status: readString(
      shipmentRecord,
      ['status', 'workflow_state'],
      readString(data, ['status', 'workflow_state'], readString(root, ['status', 'workflow_state']))
    ),
    dispatchToken: snapshot.dispatchToken,
    dispatchQrPayload: snapshot.dispatchQrPayload,
    dispatchQrImageUrl: snapshot.dispatchQrImageUrl,
    returnToken: undefined,
    deliveryPersonnelName: readString(shipmentRecord, ['rider_name', 'driver_name'], readString(data, ['rider_name', 'driver_name'], readString(root, ['rider_name', 'driver_name']))),
    deliveryPersonnelPhone: readString(shipmentRecord, ['rider_phone', 'driver_phone'], readString(data, ['rider_phone', 'driver_phone'], readString(root, ['rider_phone', 'driver_phone']))),
    vehicleInfo: readString(shipmentRecord, ['vehicle_info'], readString(data, ['vehicle_info'], readString(root, ['vehicle_info']))),
    tokenExpiresAt: snapshot.dispatchTokenExpiresAt,
  };
};

const mergeRequestWithCachedDispatchData = (
  incoming: MappedRequest,
  existing?: MappedRequest,
  cached?: DispatchSnapshot,
): MappedRequest => {
  if (!existing && !cached) {
    return incoming;
  }

  return {
    ...incoming,
    shipmentId: incoming.shipmentId || existing?.shipmentId || cached?.shipmentId,
    dispatchToken: incoming.dispatchToken || existing?.dispatchToken || cached?.dispatchToken,
    dispatchQrPayload:
      incoming.dispatchQrPayload ||
      existing?.dispatchQrPayload ||
      cached?.dispatchQrPayload ||
      cached?.dispatchToken,
    dispatchQrImageUrl: incoming.dispatchQrImageUrl || existing?.dispatchQrImageUrl || cached?.dispatchQrImageUrl,
    dispatchTokenExpiresAt:
      incoming.dispatchTokenExpiresAt || existing?.dispatchTokenExpiresAt || cached?.dispatchTokenExpiresAt,
  };
};

const mergeShipmentWithCachedDispatchData = (
  incoming: ShipmentInfo,
  existing?: ShipmentInfo,
  cached?: DispatchSnapshot,
): ShipmentInfo => {
  if (!existing) {
    return {
      ...incoming,
      id: incoming.id || cached?.shipmentId || '',
      requestId: incoming.requestId || cached?.requestId,
      status: incoming.status || existing?.status || 'IN_TRANSIT',
      dispatchToken: incoming.dispatchToken || undefined,
      dispatchQrPayload: incoming.dispatchQrPayload || cached?.dispatchQrPayload || cached?.dispatchToken || undefined,
      dispatchQrImageUrl: incoming.dispatchQrImageUrl || cached?.dispatchQrImageUrl || undefined,
      returnToken: incoming.returnToken || undefined,
      tokenExpiresAt: incoming.tokenExpiresAt || cached?.dispatchTokenExpiresAt || undefined,
    };
  }

  return {
    ...existing,
    ...incoming,
    dispatchToken: incoming.dispatchToken || existing.dispatchToken || cached?.dispatchToken,
    dispatchQrPayload:
      incoming.dispatchQrPayload ||
      existing.dispatchQrPayload ||
      cached?.dispatchQrPayload ||
      cached?.dispatchToken,
    dispatchQrImageUrl: incoming.dispatchQrImageUrl || existing.dispatchQrImageUrl || cached?.dispatchQrImageUrl,
    returnToken: incoming.returnToken || existing.returnToken,
    tokenExpiresAt: incoming.tokenExpiresAt || existing.tokenExpiresAt || cached?.dispatchTokenExpiresAt,
  };
};

const upsertShipment = (existing: ShipmentInfo[], incoming: ShipmentInfo): ShipmentInfo[] => {
  const byIdIndex = incoming.id
    ? existing.findIndex((shipment) => shipment.id && shipment.id === incoming.id)
    : -1;

  if (byIdIndex >= 0) {
    const next = [...existing];
    next[byIdIndex] = mergeShipmentWithCachedDispatchData(incoming, next[byIdIndex]);
    return next;
  }

  const byRequestIndex = incoming.requestId
    ? existing.findIndex((shipment) => shipment.requestId && shipment.requestId === incoming.requestId)
    : -1;

  if (byRequestIndex >= 0) {
    const next = [...existing];
    next[byRequestIndex] = mergeShipmentWithCachedDispatchData(incoming, next[byRequestIndex]);
    return next;
  }

  return [...existing, mergeShipmentWithCachedDispatchData(incoming)];
};

type QrDownloadFormat = 'svg' | 'png' | 'jpg';
type QrDisplaySize = '160' | '220' | '280' | '340' | '420';
const QR_QUIET_ZONE_RATIO = 0.14;
const QR_MIN_QUIET_ZONE_PX = 16;

const DEFAULT_QR_DISPLAY_SIZE: QrDisplaySize = '340';
const QR_DISPLAY_SIZE_OPTIONS: Array<{ value: QrDisplaySize; label: string }> = [
  { value: '160', label: 'Small (160px)' },
  { value: '220', label: 'Medium (220px)' },
  { value: '280', label: 'Large (280px)' },
  { value: '340', label: 'XL (340px)' },
  { value: '420', label: 'XXL (420px)' },
];

const getQrDownloadMime = (format: QrDownloadFormat): string => {
  if (format === 'jpg') return 'image/jpeg';
  if (format === 'png') return 'image/png';
  return 'image/svg+xml;charset=utf-8';
};

const triggerDownload = (url: string, fileName: string) => {
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  anchor.rel = 'noreferrer';
  anchor.click();
};

const loadImageElement = async (url: string): Promise<HTMLImageElement> =>
  new Promise((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('Failed to load QR image.'));
    image.src = url;
  });

const parseSvgSizeValue = (raw?: string | null): number => {
  if (!raw) return 0;
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
};

const resolveQrSourceSize = (element: SVGSVGElement): number => {
  const viewBoxSize = Math.max(element.viewBox.baseVal.width || 0, element.viewBox.baseVal.height || 0);
  if (viewBoxSize > 0) {
    return viewBoxSize;
  }

  const widthAttr = parseSvgSizeValue(element.getAttribute('width'));
  const heightAttr = parseSvgSizeValue(element.getAttribute('height'));
  const attrSize = Math.max(widthAttr, heightAttr);
  if (attrSize > 0) {
    return attrSize;
  }

  const rect = element.getBoundingClientRect();
  return Math.max(Math.ceil(rect.width || 0), Math.ceil(rect.height || 0), 1);
};

const resolveQrPixelSize = (element: SVGSVGElement, outputSize?: number): number => {
  const targetSize =
    typeof outputSize === 'number' && Number.isFinite(outputSize) && outputSize > 0
      ? Math.floor(outputSize)
      : null;

  if (targetSize) {
    return Math.max(targetSize, 1);
  }

  const rect = element.getBoundingClientRect();
  const viewBoxSize = Math.max(element.viewBox.baseVal.width || 0, element.viewBox.baseVal.height || 0);
  const measuredSize = Math.max(Math.ceil(rect.width || 0), Math.ceil(rect.height || 0), Math.ceil(viewBoxSize));
  return Math.max(measuredSize || 160, 1);
};

const getQrQuietZoneSize = (qrPixelSize: number): number => {
  const scaled = Math.round(qrPixelSize * QR_QUIET_ZONE_RATIO);
  return Math.max(QR_MIN_QUIET_ZONE_PX, scaled);
};

const downloadQrSvg = async (
  svgId: string,
  fileName: string,
  format: QrDownloadFormat,
  outputSize?: number,
): Promise<boolean> => {
  const element = document.getElementById(svgId);
  if (!element) return false;
  if (!(element instanceof SVGSVGElement)) return false;

  const serializer = new XMLSerializer();
  const source = serializer.serializeToString(element);
  const sourceSize = resolveQrSourceSize(element);
  const qrSize = resolveQrPixelSize(element, outputSize);
  const quietZone = getQrQuietZoneSize(qrSize);
  const totalSize = qrSize + quietZone * 2;
  const scale = qrSize / sourceSize;

  const innerMarkup = source
    .replace(/^<svg[^>]*>/i, '')
    .replace(/<\/svg>\s*$/i, '');

  const quietZoneSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="${totalSize}" height="${totalSize}" viewBox="0 0 ${totalSize} ${totalSize}"><rect x="0" y="0" width="${totalSize}" height="${totalSize}" fill="#FFFFFF"/><g transform="translate(${quietZone} ${quietZone}) scale(${scale})">${innerMarkup}</g></svg>`;

  if (format === 'svg') {
    const blob = new Blob([quietZoneSvg], { type: getQrDownloadMime(format) });
    const url = URL.createObjectURL(blob);
    triggerDownload(url, fileName);
    URL.revokeObjectURL(url);
    return true;
  }

  let svgBlobUrl = '';
  try {
    const blob = new Blob([quietZoneSvg], { type: 'image/svg+xml;charset=utf-8' });
    svgBlobUrl = URL.createObjectURL(blob);
    const image = await loadImageElement(svgBlobUrl);

    const canvas = document.createElement('canvas');
    canvas.width = totalSize;
    canvas.height = totalSize;

    const context = canvas.getContext('2d');
    if (!context) return false;

    context.fillStyle = '#FFFFFF';
    context.fillRect(0, 0, totalSize, totalSize);
    context.imageSmoothingEnabled = false;
    context.drawImage(image, 0, 0, totalSize, totalSize);
    const dataUrl = canvas.toDataURL(getQrDownloadMime(format));
    triggerDownload(dataUrl, fileName);
    return true;
  } catch {
    return false;
  } finally {
    if (svgBlobUrl) {
      URL.revokeObjectURL(svgBlobUrl);
    }
  }
};

const downloadQrImage = async (
  imageUrl: string,
  fileName: string,
  format: QrDownloadFormat,
  outputSize?: number,
): Promise<boolean> => {
  if (!imageUrl) return false;

  if (format === 'svg') {
    triggerDownload(imageUrl, fileName);
    return true;
  }

  try {
    const image = await loadImageElement(imageUrl);
    const qrSize =
      typeof outputSize === 'number' && Number.isFinite(outputSize) && outputSize > 0
        ? Math.floor(outputSize)
        : Math.max(Math.min(image.naturalWidth || image.width || 160, image.naturalHeight || image.height || 160), 1);
    const quietZone = getQrQuietZoneSize(qrSize);
    const totalSize = qrSize + quietZone * 2;

    const canvas = document.createElement('canvas');
    canvas.width = totalSize;
    canvas.height = totalSize;

    const context = canvas.getContext('2d');
    if (!context) return false;

    context.fillStyle = '#FFFFFF';
    context.fillRect(0, 0, totalSize, totalSize);
    context.imageSmoothingEnabled = false;
    context.drawImage(image, quietZone, quietZone, qrSize, qrSize);
    const dataUrl = canvas.toDataURL(getQrDownloadMime(format));
    triggerDownload(dataUrl, fileName);
    return true;
  } catch {
    // Fallback to direct download when browser blocks canvas export for remote image URLs.
    triggerDownload(imageUrl, fileName);
    return true;
  }
};

const printQrSection = (sectionId: string, title: string, outputSize?: number): boolean => {
  const section = document.getElementById(sectionId);
  if (!section) return false;

  const clonedSection = section.cloneNode(true);
  if (!(clonedSection instanceof HTMLElement)) {
    return false;
  }

  clonedSection.classList.add('qr-print-target');

  const printQrSize =
    typeof outputSize === 'number' && Number.isFinite(outputSize) && outputSize > 0
      ? Math.max(Math.floor(outputSize), 320)
      : 420;
  const printQuietZone = getQrQuietZoneSize(printQrSize);

  const printWindow = window.open('', '_blank', 'noopener,noreferrer,width=760,height=920');
  if (!printWindow) return false;

  const escapedTitle = title.replace(/</g, '&lt;').replace(/>/g, '&gt;');
  printWindow.document.write(`<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>${escapedTitle}</title>
  <style>
    @page { size: auto; margin: 12mm; }
    body { font-family: Arial, sans-serif; margin: 0; color: #111827; }
    h1 { font-size: 18px; margin: 0 0 12px; }
    p { font-size: 13px; margin: 0 0 12px; color: #374151; }
    .print-container { padding: 18px; }
    .qr-print-wrapper {
      border: 1px solid #d1d5db;
      border-radius: 10px;
      padding: 20px;
      max-width: 760px;
      background: #ffffff;
    }
    .qr-print-target {
      display: inline-flex !important;
      align-items: center;
      justify-content: center;
      background: #ffffff;
      border: 2px solid #111827;
      border-radius: 12px;
      box-sizing: content-box;
      padding: ${printQuietZone}px !important;
    }
    .qr-print-target img,
    .qr-print-target svg {
      width: ${printQrSize}px !important;
      height: ${printQrSize}px !important;
      max-width: none !important;
      max-height: none !important;
      display: block;
      image-rendering: crisp-edges;
    }
  </style>
</head>
<body>
  <div class="print-container">
    <h1>${escapedTitle}</h1>
    <p>Print and attach this QR to the shipment package.</p>
    <div class="qr-print-wrapper">${clonedSection.outerHTML}</div>
  </div>
  <script>
    (function () {
      window.addEventListener('afterprint', function () {
        window.close();
      });

      const runPrint = function () {
        window.focus();
        window.print();
      };

      if (document.readyState === 'complete') {
        setTimeout(runPrint, 180);
      } else {
        window.addEventListener('load', function () {
          setTimeout(runPrint, 180);
        }, { once: true });
      }
    })();
  </script>
</body>
</html>`);
  printWindow.document.close();
  return true;
};

const isPendingForProviderAction = (status: string): boolean => {
  const normalized = normalizeStatus(status);
  return normalized === 'pending' || normalized === 'requested' || normalized === 'new';
};

const DISPATCH_ELIGIBLE_STATES: WorkflowState[] = ['APPROVED', 'RESERVED', 'PAYMENT_PENDING', 'PAYMENT_COMPLETED'];

const getDispatchEligibility = (request: MappedRequest): { canDispatch: boolean; reason?: string } => {
  const stage = mapStatus(request.status);

  if (!DISPATCH_ELIGIBLE_STATES.includes(stage)) {
    return {
      canDispatch: false,
      reason: 'Dispatch is available only after approval/reservation stages.',
    };
  }

  if (request.paymentRequired === true && stage !== 'PAYMENT_COMPLETED') {
    return {
      canDispatch: false,
      reason: 'Dispatch blocked: payment is required and must be completed first.',
    };
  }

  return { canDispatch: true };
};

const getErrorMessage = (error: unknown): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  const record = asRecord(error);
  return readString(record, ['message', 'detail'], 'Please retry.');
};

const isPaymentNotRequiredError = (message: string): boolean =>
  message.toLowerCase().includes('payment is not required for this request');

const normalizePaymentTerminal = (status: string | undefined): 'success' | 'failed' | 'pending' | 'unknown' => {
  const normalized = normalizeStatus(status || '');
  if (['success', 'completed', 'paid', 'refunded'].includes(normalized)) {
    return 'success';
  }
  if (['failed', 'cancelled', 'canceled', 'refund_failed'].includes(normalized)) {
    return 'failed';
  }
  if (['pending', 'initiated', 'processing', 'awaiting_payment'].includes(normalized)) {
    return 'pending';
  }
  return 'unknown';
};

const formatDateTime = (value?: string): string => {
  if (!value) return 'N/A';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
};

const formatCompactDateTime = (value?: string): string => {
  if (!value) return 'N/A';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  const datePart = parsed.toLocaleDateString();
  const timePart = parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return `${datePart}, ${timePart}`;
};

const RequestWorkflow = ({ view = 'combined' }: RequestWorkflowProps) => {
  const { user } = useAuth();
  const { toast } = useToast();

  const [requests, setRequests] = useState<MappedRequest[]>([]);
  const [shipments, setShipments] = useState<ShipmentInfo[]>([]);
  const [staffRows, setStaffRows] = useState<StaffRow[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<WorkflowTab>(view === 'outgoing' ? 'outgoing' : 'incoming');
  const [decisionLoadingId, setDecisionLoadingId] = useState<string | null>(null);
  const [decisionProcessing, setDecisionProcessing] = useState<'approved' | 'rejected' | null>(null);

  const [sortBy, setSortBy] = useState<SortBy>('newest');
  const [hospitalFilter, setHospitalFilter] = useState('all');
  const [resourceTypeFilter, setResourceTypeFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [requestDateFilter, setRequestDateFilter] = useState<'all' | 'today' | 'last_7_days' | 'custom'>('all');
  const [customDateFrom, setCustomDateFrom] = useState('');
  const [customDateTo, setCustomDateTo] = useState('');
  const [waivePaymentByRequest, setWaivePaymentByRequest] = useState<Record<string, boolean>>({});
  const [latestPaymentIdByRequest, setLatestPaymentIdByRequest] = useState<Record<string, string>>({});
  const [paymentRedirectByRequest, setPaymentRedirectByRequest] = useState<Record<string, string>>({});
  const [paymentInitiationKeyByRequest, setPaymentInitiationKeyByRequest] = useState<Record<string, string>>({});
  const [paymentPollingTimedOutByRequest, setPaymentPollingTimedOutByRequest] = useState<Record<string, boolean>>({});
  const [paymentReturnRequestId, setPaymentReturnRequestId] = useState<string | null>(null);
  const [cancelReasonByRequest, setCancelReasonByRequest] = useState<Record<string, string>>({});
  const [cancelFormOpenByRequest, setCancelFormOpenByRequest] = useState<Record<string, boolean>>({});
  const [returnReasonByRequest, setReturnReasonByRequest] = useState<Record<string, string>>({});
  const [verifyReturnTokenByRequest, setVerifyReturnTokenByRequest] = useState<Record<string, string>>({});
  const [transferIdempotencyByAction, setTransferIdempotencyByAction] = useState<Record<string, string>>({});
  const [dispatchQrFormatByRequest, setDispatchQrFormatByRequest] = useState<Record<string, QrDownloadFormat>>({});
  const [dispatchQrSizeByRequest, setDispatchQrSizeByRequest] = useState<Record<string, QrDisplaySize>>({});


  const [personnelMode, setPersonnelMode] = useState<'existing' | 'external'>('existing');
  const [selectedStaffId, setSelectedStaffId] = useState('');
  const [externalPersonnelName, setExternalPersonnelName] = useState('');
  const [externalPersonnelPhone, setExternalPersonnelPhone] = useState('');
  const [vehicleInfo, setVehicleInfo] = useState('');
  const [dispatchNote, setDispatchNote] = useState('');
  const [receiverTokenInputByRequest, setReceiverTokenInputByRequest] = useState<Record<string, string>>({});

  const [actionLoading, setActionLoading] = useState(false);
  const [paymentLoadingId, setPaymentLoadingId] = useState<string | null>(null);

  const gatewayReturnQueryHandledRef = useRef('');
  const dispatchQrCacheRef = useRef<DispatchCacheByRequestId>(readDispatchQrCache());

  const userHospitalId = String(user?.hospital_id || '');
  const userContext = String(user?.context || '').trim().toUpperCase();
  const hasHealthcareContext = userContext ? userContext === 'HEALTHCARE' : Boolean(userHospitalId);
  const pageTitle =
    view === 'incoming'
      ? 'Incoming Request Workflow'
      : view === 'outgoing'
        ? 'Outgoing Request Workflow'
        : 'Request Workflow';

  useEffect(() => {
    if (view !== 'combined') {
      setActiveTab(view);
    }
  }, [view]);

  const notifyResourceSharesUpdated = useCallback(() => {
    window.dispatchEvent(new Event(RESOURCE_SHARES_UPDATED_EVENT));
  }, []);

  const clearReceiverTokenInput = useCallback((requestId: string) => {
    setReceiverTokenInputByRequest((prev) => {
      if (!prev[requestId]) {
        return prev;
      }
      const next = { ...prev };
      delete next[requestId];
      return next;
    });
  }, []);

  useEffect(() => {
    const nextCache: DispatchCacheByRequestId = { ...dispatchQrCacheRef.current };

    requests.forEach((request) => {
      const requestId = request.id;
      if (!requestId) {
        return;
      }

      if (!shouldRetainDispatchCache(request.status, request.completionStage)) {
        delete nextCache[requestId];
        return;
      }

      const existing = nextCache[requestId];
      const nextEntry: DispatchCacheEntry = {
        requestId,
        shipmentId: request.shipmentId || existing?.shipmentId,
        dispatchToken: request.dispatchToken || existing?.dispatchToken,
        dispatchQrPayload:
          request.dispatchQrPayload ||
          existing?.dispatchQrPayload ||
          request.dispatchToken ||
          existing?.dispatchToken,
        dispatchQrImageUrl: request.dispatchQrImageUrl || existing?.dispatchQrImageUrl,
        dispatchTokenExpiresAt: request.dispatchTokenExpiresAt || existing?.dispatchTokenExpiresAt,
        workflowState: request.status,
        completionStage: request.completionStage,
      };

      if (hasDispatchSnapshotData(nextEntry)) {
        nextCache[requestId] = nextEntry;
      }
    });

    shipments.forEach((shipment) => {
      const requestId = shipment.requestId;
      if (!requestId) {
        return;
      }

      const existing = nextCache[requestId];
      const nextEntry: DispatchCacheEntry = {
        requestId,
        shipmentId: shipment.id || existing?.shipmentId,
        dispatchToken: shipment.dispatchToken || existing?.dispatchToken,
        dispatchQrPayload:
          shipment.dispatchQrPayload ||
          existing?.dispatchQrPayload ||
          shipment.dispatchToken ||
          existing?.dispatchToken,
        dispatchQrImageUrl: shipment.dispatchQrImageUrl || existing?.dispatchQrImageUrl,
        dispatchTokenExpiresAt: shipment.tokenExpiresAt || existing?.dispatchTokenExpiresAt,
        workflowState: existing?.workflowState,
        completionStage: existing?.completionStage || 'NOT_STARTED',
      };

      if (hasDispatchSnapshotData(nextEntry)) {
        nextCache[requestId] = nextEntry;
      }
    });

    dispatchQrCacheRef.current = nextCache;
    writeDispatchQrCache(nextCache);
  }, [requests, shipments]);

  const loadRequests = useCallback(async (options?: { silent?: boolean }) => {
    if (!options?.silent) {
      setLoading(true);
    }

    try {
      const [requestRes, shipmentRes, staffRes] = await Promise.all([
        requestsApi.getAll(),
        shipmentsApi.getAll().catch(() => []),
        staffApi.getAll({ limit: '200' }).catch(() => []),
      ]);

      const requestRaw = extractCollection(requestRes);
      const shipmentRaw = extractCollection(shipmentRes);
      const staffRaw = extractCollection(staffRes);

      const mapped = requestRaw.map(mapApiRequest);
      const mappedShipments = shipmentRaw
        .map(mapShipment)
        .filter(
          (shipment) =>
            shipment.id ||
            shipment.requestId ||
            shipment.dispatchToken ||
            shipment.dispatchQrPayload ||
            shipment.dispatchQrImageUrl
        );
      const mappedStaff = staffRaw.map((item: unknown) => {
        const staff = asRecord(item);
        const userRecord = asRecord(staff.user);
        const profileRecord = asRecord(staff.profile);
        const firstName = readString(staff, ['first_name'], readString(userRecord, ['first_name']));
        const lastName = readString(staff, ['last_name'], readString(userRecord, ['last_name']));
        const combinedName = [firstName, lastName].filter(Boolean).join(' ').trim();

        return {
          id: String(staff.id || userRecord.id || profileRecord.id || ''),
          fullName: readString(
            staff,
            ['full_name', 'fullName', 'display_name', 'name', 'email'],
            readString(
              userRecord,
              ['full_name', 'fullName', 'display_name', 'name', 'email'],
              readString(profileRecord, ['full_name', 'fullName', 'display_name', 'name'], combinedName || 'Staff')
            )
          ),
          phone: readString(
            staff,
            ['phone', 'mobile', 'phone_number', 'mobile_number', 'contact_number', 'contact_no'],
            readString(
              userRecord,
              ['phone', 'mobile', 'phone_number', 'mobile_number', 'contact_number', 'contact_no'],
              readString(profileRecord, ['phone', 'mobile', 'phone_number', 'mobile_number'])
            )
          ),
        };
      });

      setRequests((prev) => {
        const existingById = new Map(prev.map((item) => [item.id, item]));
        return mapped.map((item) =>
          mergeRequestWithCachedDispatchData(
            item,
            existingById.get(item.id),
            dispatchQrCacheRef.current[item.id],
          )
        );
      });
      setShipments((prev) => {
        const existingById = new Map(prev.filter((item) => item.id).map((item) => [item.id, item]));
        const existingByRequest = new Map(
          prev.filter((item) => item.requestId).map((item) => [item.requestId as string, item])
        );

        const merged = mappedShipments.map((item) => {
          const cached = (item.id && existingById.get(item.id)) || (item.requestId && existingByRequest.get(item.requestId));
          return mergeShipmentWithCachedDispatchData(item, cached, item.requestId ? dispatchQrCacheRef.current[item.requestId] : undefined);
        });

        const mergedKeys = new Set(
          merged.map((item) => (item.id ? `id:${item.id}` : `request:${item.requestId || ''}`))
        );
        const stickyCached = prev.filter((item) => {
          if (!item.dispatchToken && !item.dispatchQrPayload && !item.dispatchQrImageUrl) {
            return false;
          }
          const key = item.id ? `id:${item.id}` : `request:${item.requestId || ''}`;
          return !mergedKeys.has(key);
        });

        return [...merged, ...stickyCached];
      });
      setStaffRows(mappedStaff);
      setLatestPaymentIdByRequest((prev) => {
        const next = { ...prev };
        mapped.forEach((row) => {
          if (row.latestPaymentId) {
            next[row.id] = row.latestPaymentId;
          }
        });
        return next;
      });
      setPaymentInitiationKeyByRequest((prev) => {
        const next = { ...prev };
        mapped.forEach((row) => {
          const stage = mapStatus(row.status);
          if (
            row.paymentRequired === false ||
            stage === 'PAYMENT_COMPLETED' ||
            stage === 'FAILED' ||
            stage === 'CANCELLED' ||
            stage === 'EXPIRED' ||
            stage === 'COMPLETED'
          ) {
            delete next[row.id];
          }
        });
        return next;
      });
      setPaymentPollingTimedOutByRequest((prev) => {
        const next = { ...prev };
        mapped.forEach((row) => {
          const stage = mapStatus(row.status);
          if (
            row.paymentRequired === false ||
            stage === 'PAYMENT_COMPLETED' ||
            stage === 'FAILED' ||
            stage === 'CANCELLED' ||
            stage === 'EXPIRED' ||
            stage === 'COMPLETED'
          ) {
            delete next[row.id];
          }
        });
        return next;
      });

      setExpandedId((prev) => (prev || mapped[0]?.id || null));

      setError(null);
    } catch (err: unknown) {
      setError(getErrorMessage(err) || 'Failed to load requests');
    } finally {
      if (!options?.silent) {
        setLoading(false);
      }
    }
  }, []);

  const refreshRequestDetail = useCallback(async (requestId: string): Promise<MappedRequest | null> => {
    const response = await requestsApi.getByIdFresh(requestId);
    const detailRecord = unwrapDataRecord(response);
    const mapped = mapApiRequest(detailRecord);
    if (!mapped.id) {
      return null;
    }

    setRequests((prev) =>
      prev.map((entry) =>
        entry.id === requestId
          ? mergeRequestWithCachedDispatchData(mapped, entry, dispatchQrCacheRef.current[requestId])
          : entry
      )
    );

    if (mapped.latestPaymentId) {
      setLatestPaymentIdByRequest((prev) => ({
        ...prev,
        [requestId]: mapped.latestPaymentId as string,
      }));
    }

    const mappedStage = mapStatus(mapped.status);
    if (
      mapped.paymentRequired === false ||
      mappedStage === 'PAYMENT_COMPLETED' ||
      mappedStage === 'FAILED' ||
      mappedStage === 'CANCELLED' ||
      mappedStage === 'EXPIRED' ||
      mappedStage === 'COMPLETED'
    ) {
      setPaymentInitiationKeyByRequest((prev) => {
        if (!prev[requestId]) {
          return prev;
        }
        const next = { ...prev };
        delete next[requestId];
        return next;
      });
      setPaymentPollingTimedOutByRequest((prev) => {
        if (!prev[requestId]) {
          return prev;
        }
        const next = { ...prev };
        delete next[requestId];
        return next;
      });
    }

    return mapped;
  }, []);

  const refreshShipmentForRequest = useCallback(async (requestId: string): Promise<ShipmentInfo | null> => {
    const queryAttempts: Array<Record<string, string> | undefined> = [
      { request: requestId },
      { resource_request: requestId },
      undefined,
    ];

    for (const params of queryAttempts) {
      try {
        const response = params ? await shipmentsApi.getAll(params) : await shipmentsApi.getAll();
        const collection = extractCollection(response);
        const mapped = collection
          .map(mapShipment)
          .filter(
            (shipment) =>
              shipment.id ||
              shipment.requestId ||
              shipment.dispatchToken ||
              shipment.dispatchQrPayload ||
              shipment.dispatchQrImageUrl
          );
        const matched = mapped.find((shipment) => shipment.requestId === requestId);
        if (matched) {
          const normalized = {
            ...matched,
            requestId: matched.requestId || requestId,
          };
          setShipments((prev) => upsertShipment(prev, normalized));
          return normalized;
        }
      } catch {
        // Try fallback query style.
      }
    }

    return null;
  }, []);

  useEffect(() => {
    void loadRequests();
  }, [loadRequests]);

  useEffect(() => {
    const pollId = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        void loadRequests({ silent: true });
      }
    }, 10000);

    const handleFocus = () => {
      void loadRequests({ silent: true });
    };

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        void loadRequests({ silent: true });
      }
    };

    window.addEventListener('focus', handleFocus);
    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      window.clearInterval(pollId);
      window.removeEventListener('focus', handleFocus);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [loadRequests]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestId = takeToken(params.get('payment_request_id'), params.get('request_id'));
    if (!requestId || gatewayReturnQueryHandledRef.current === requestId) {
      return;
    }

    gatewayReturnQueryHandledRef.current = requestId;
    setPaymentPollingTimedOutByRequest((prev) => {
      if (!prev[requestId]) {
        return prev;
      }
      const next = { ...prev };
      delete next[requestId];
      return next;
    });
    setPaymentReturnRequestId(requestId);

    toast({
      title: 'Checking payment status',
      description: 'Refreshing request state from backend callback processing.',
    });

    const cleanUrl = new URL(window.location.href);
    GATEWAY_RETURN_QUERY_KEYS.forEach((key) => cleanUrl.searchParams.delete(key));
    window.history.replaceState({}, document.title, `${cleanUrl.pathname}${cleanUrl.search}${cleanUrl.hash}`);
  }, [toast]);

  useEffect(() => {
    if (!paymentReturnRequestId) {
      return;
    }

    let cancelled = false;
    let attempts = 0;
    const maxAttempts = 12;

    const pollReturnedPaymentRequest = async () => {
      attempts += 1;
      const refreshed = await refreshRequestDetail(paymentReturnRequestId).catch(() => null);
      if (!refreshed || cancelled) {
        return;
      }

      const current = mapStatus(refreshed.status);
      const transactionTerminal = normalizePaymentTerminal(
        refreshed.latestPaymentTransactionStatus || refreshed.paymentStatus
      );

      if (current === 'PAYMENT_COMPLETED' || transactionTerminal === 'success') {
        const paymentIdContext = refreshed.latestPaymentId
          ? `Payment id: ${refreshed.latestPaymentId}.`
          : 'Latest payment id not available yet.';
        toast({
          title: 'Payment completed',
          description: `${paymentIdContext} Dispatch can continue.`,
        });
        setPaymentPollingTimedOutByRequest((prev) => {
          if (!prev[paymentReturnRequestId]) {
            return prev;
          }
          const next = { ...prev };
          delete next[paymentReturnRequestId];
          return next;
        });
        setPaymentReturnRequestId(null);
        return;
      }

      if (current === 'FAILED' || current === 'CANCELLED' || transactionTerminal === 'failed') {
        const failureContext =
          refreshed.failedReason ||
          refreshed.paymentNote ||
          refreshed.latestPaymentTransactionStatus ||
          refreshed.paymentStatus ||
          'Gateway callback marked this payment as failed.';

        toast({
          title: 'Payment failed',
          description: failureContext,
          variant: 'destructive',
        });
        setPaymentPollingTimedOutByRequest((prev) => {
          if (!prev[paymentReturnRequestId]) {
            return prev;
          }
          const next = { ...prev };
          delete next[paymentReturnRequestId];
          return next;
        });
        setPaymentReturnRequestId(null);
        return;
      }

      if (attempts >= maxAttempts) {
        setPaymentPollingTimedOutByRequest((prev) => ({
          ...prev,
          [paymentReturnRequestId]: true,
        }));
        toast({
          title: 'Payment still processing',
          description: 'Callback reconciliation is still pending. Use Refresh payment status to check manually.',
        });
        setPaymentReturnRequestId(null);
      }
    };

    void pollReturnedPaymentRequest();
    const intervalId = window.setInterval(() => {
      void pollReturnedPaymentRequest();
    }, 5000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [paymentReturnRequestId, refreshRequestDetail, toast]);

  const getSLATarget = (requestedAt: string, urgency: string) => {
    const date = new Date(requestedAt);
    switch (urgency) {
      case 'critical':
        date.setHours(date.getHours() + 4);
        break;
      case 'urgent':
        date.setHours(date.getHours() + 48);
        break;
      default:
        date.setDate(date.getDate() + 5);
    }
    return date.toISOString();
  };

  const incomingRequests = useMemo(() => {
    return requests.filter((request) => !!userHospitalId && request.supplyingHospitalId === userHospitalId);
  }, [requests, userHospitalId]);

  const outgoingRequests = useMemo(() => {
    return requests.filter((request) => !!userHospitalId && request.requestingHospitalId === userHospitalId);
  }, [requests, userHospitalId]);

  const hospitalOptions = useMemo(() => {
    const set = new Set<string>();
    [...incomingRequests, ...outgoingRequests].forEach((request) => {
      if (request.requestingHospital) set.add(request.requestingHospital);
      if (request.providingHospital) set.add(request.providingHospital);
    });
    return Array.from(set).sort();
  }, [incomingRequests, outgoingRequests]);

  const applyFiltersAndSort = useCallback((items: MappedRequest[]) => {
    const filtered = items.filter((request) => {
      const stage = mapStatus(request.status);
      const hospitalMatched =
        hospitalFilter === 'all' ||
        request.requestingHospital === hospitalFilter ||
        request.providingHospital === hospitalFilter;
      const resourceTypeMatched = resourceTypeFilter === 'all' || request.resourceType === resourceTypeFilter;
      const statusMatched = statusFilter === 'all' || stage === statusFilter;

      const requestTimestamp = toEpochMillis(request.requestedAt);
      const now = new Date();
      const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      const lastSevenDays = new Date(startOfToday);
      lastSevenDays.setDate(lastSevenDays.getDate() - 7);
      const customStart = customDateFrom ? new Date(`${customDateFrom}T00:00:00`) : null;
      const customEnd = customDateTo ? new Date(`${customDateTo}T23:59:59.999`) : null;
      const customStartTime = customStart ? customStart.getTime() : null;
      const customEndTime = customEnd ? customEnd.getTime() : null;
      const customRangeMatched =
        requestDateFilter === 'custom' &&
        (customStartTime === null || requestTimestamp >= customStartTime) &&
        (customEndTime === null || requestTimestamp <= customEndTime);
      const requestDateMatched =
        requestDateFilter === 'all' ||
        (requestDateFilter === 'today' && requestTimestamp >= startOfToday.getTime()) ||
        (requestDateFilter === 'last_7_days' && requestTimestamp >= lastSevenDays.getTime()) ||
        customRangeMatched;

      return hospitalMatched && resourceTypeMatched && statusMatched && requestDateMatched;
    });

    return filtered.sort((a, b) => {
      if (sortBy === 'newest') {
        const timestampDiff = toEpochMillis(b.requestedAt) - toEpochMillis(a.requestedAt);
        if (timestampDiff !== 0) {
          return timestampDiff;
        }
        return b.id.localeCompare(a.id);
      }
      if (sortBy === 'status') {
        return mapStatus(a.status).localeCompare(mapStatus(b.status));
      }
      if (sortBy === 'hospital') {
        return a.requestingHospital.localeCompare(b.requestingHospital);
      }
      const urgencyOrder: Record<MappedRequest['urgency'], number> = { critical: 0, urgent: 1, routine: 2 };
      return urgencyOrder[a.urgency] - urgencyOrder[b.urgency];
    });
  }, [sortBy, hospitalFilter, resourceTypeFilter, statusFilter, requestDateFilter, customDateFrom, customDateTo]);

  const displayIncoming = useMemo(() => applyFiltersAndSort(incomingRequests), [
    incomingRequests,
    applyFiltersAndSort,
  ]);

  const displayOutgoing = useMemo(() => applyFiltersAndSort(outgoingRequests), [
    outgoingRequests,
    applyFiltersAndSort,
  ]);

  const effectiveTab: WorkflowTab = view === 'combined' ? activeTab : view;
  const displayRequests = effectiveTab === 'incoming' ? displayIncoming : displayOutgoing;

  const getRequestShipment = (request: MappedRequest): ShipmentInfo | null => {
    const byShipmentId = request.shipmentId
      ? shipments.find((shipment) => shipment.id && shipment.id === request.shipmentId)
      : null;
    if (byShipmentId) return byShipmentId;

    const byRequestId = shipments.find((shipment) => shipment.requestId && shipment.requestId === request.id);
    if (byRequestId) return byRequestId;

    const byQrPayload = shipments.find(
      (shipment) =>
        isOpaqueQrPayloadPresent(shipment.dispatchQrPayload || '') &&
        shipment.dispatchQrPayload === request.dispatchQrPayload
    ) || null;

    if (byQrPayload) return byQrPayload;

    if (
      request.shipmentId ||
      request.dispatchToken ||
      request.dispatchQrPayload ||
      request.dispatchQrImageUrl
    ) {
      return {
        id: request.shipmentId || '',
        requestId: request.id,
        status: request.shipmentStatus || request.status,
        dispatchToken: request.dispatchToken,
        dispatchQrPayload: request.dispatchQrPayload,
        dispatchQrImageUrl: request.dispatchQrImageUrl,
        returnToken: request.returnToken,
      };
    }

    return null;
  };

  const getDispatchQrValue = (request: MappedRequest, shipment: ShipmentInfo | null) =>
    takeOpaqueQrPayload(
      request.dispatchQrPayload,
      shipment?.dispatchQrPayload,
      request.dispatchToken,
      shipment?.dispatchToken,
    );

  const getDispatchQrImageUrl = (request: MappedRequest, shipment: ShipmentInfo | null) =>
    takeToken(request.dispatchQrImageUrl, shipment?.dispatchQrImageUrl);

  const handleDecision = async (request: MappedRequest, decision: 'approved' | 'rejected') => {
    setDecisionLoadingId(request.id);
    setDecisionProcessing(decision);
    const waivePayment = decision === 'approved' ? Boolean(waivePaymentByRequest[request.id]) : false;
    try {
      await requestsApi.approve(request.id, {
        decision,
        quantity_approved: decision === 'approved' ? request.quantity : undefined,
        reason: decision === 'rejected' ? 'Rejected by provider hospital' : undefined,
        waive_payment: decision === 'approved' ? waivePayment : undefined,
      });

      await loadRequests();
      notifyResourceSharesUpdated();

      toast({
        title: decision === 'approved' ? 'Request approved' : 'Request rejected',
        description:
          decision === 'approved'
            ? waivePayment
              ? 'Request approved with payment waived for requester hospital.'
              : 'Request moved to APPROVED/RESERVED workflow states.'
            : 'Requester hospital notified.',
      });
    } catch (err: unknown) {
      toast({
        title: 'Failed to update request',
        description: getErrorMessage(err),
        variant: 'destructive',
      });
    } finally {
      setDecisionLoadingId(null);
      setDecisionProcessing(null);
    }
  };

  const handleDispatch = async (request: MappedRequest) => {
    const selectedStaff =
      staffRows.find((staff) => staff.id === selectedStaffId) ||
      (personnelMode === 'existing' && !selectedStaffId && staffRows.length === 1 ? staffRows[0] : undefined);
    const deliveryPersonnelName =
      personnelMode === 'existing' ? (selectedStaff?.fullName || '').trim() : externalPersonnelName.trim();
    const deliveryPersonnelPhone =
      personnelMode === 'existing' ? (selectedStaff?.phone || '').trim() : externalPersonnelPhone.trim();
    const initialDispatchCheck = getDispatchEligibility(request);

    if (personnelMode === 'existing' && selectedStaffId && !selectedStaff) {
      toast({
        title: 'Selected staff unavailable',
        description: 'Selected delivery personnel could not be resolved. Please re-select staff and retry.',
        variant: 'destructive',
      });
      return;
    }

    if (personnelMode === 'existing' && !selectedStaff && staffRows.length !== 1) {
      toast({
        title: 'Delivery personnel required',
        description: 'Please select delivery personnel before dispatch.',
        variant: 'destructive',
      });
      return;
    }

    if (!deliveryPersonnelName || !vehicleInfo.trim()) {
      const missingFields = [
        !deliveryPersonnelName ? 'delivery personnel name' : null,
        !vehicleInfo.trim() ? 'vehicle details' : null,
      ].filter(Boolean).join(', ');

      toast({
        title: 'Missing dispatch information',
        description: `Required: ${missingFields}.`,
        variant: 'destructive',
      });
      return;
    }

    if (!initialDispatchCheck.canDispatch) {
      toast({
        title: 'Dispatch blocked',
        description: initialDispatchCheck.reason || 'Dispatch is not allowed for this request yet.',
        variant: 'destructive',
      });
      return;
    }

    setActionLoading(true);
    try {
      let refreshedRequest = (await refreshRequestDetail(request.id)) || request;

      if (mapStatus(refreshedRequest.status) === 'APPROVED') {
        const reserveResponse = await requestsApi.reserve(request.id, {
          requested_quantity: request.quantity,
        });

        const reserveData = unwrapDataRecord(reserveResponse);
        const reservedPaymentRequired = readBoolean(reserveData, ['payment_required']);
        const reservedWorkflowState = readString(reserveData, ['workflow_state', 'status']);

        refreshedRequest = {
          ...refreshedRequest,
          ...(reservedPaymentRequired !== null ? { paymentRequired: reservedPaymentRequired } : {}),
          ...(reservedWorkflowState ? { status: reservedWorkflowState } : {}),
        };

        const postReserveRefresh = await refreshRequestDetail(request.id).catch(() => null);
        if (postReserveRefresh) {
          refreshedRequest = postReserveRefresh;
        }
      }

      const dispatchCheck = getDispatchEligibility(refreshedRequest);
      if (!dispatchCheck.canDispatch) {
        toast({
          title: 'Dispatch blocked',
          description: dispatchCheck.reason || 'Dispatch is not allowed for this request yet.',
          variant: 'destructive',
        });
        return;
      }

      const dispatchNotes = [
        dispatchNote.trim() || null,
        `Delivery personnel: ${deliveryPersonnelName}`,
        deliveryPersonnelPhone ? `Phone: ${deliveryPersonnelPhone}` : null,
        `Vehicle: ${vehicleInfo.trim()}`,
      ]
        .filter(Boolean)
        .join(' | ');

      const dispatchResponse = await requestsApi.dispatch(request.id, {
        notes: dispatchNotes,
      });

      const dispatchSnapshot = extractDispatchSessionFromPayload(dispatchResponse, request.id);
      if (dispatchSnapshot) {
        setRequests((prev) =>
          prev.map((entry) => {
            if (entry.id !== request.id) {
              return entry;
            }

            return mergeRequestWithCachedDispatchData(
              {
                ...entry,
                shipmentId: dispatchSnapshot.shipmentId || entry.shipmentId,
                dispatchToken: dispatchSnapshot.dispatchToken || entry.dispatchToken,
                dispatchQrPayload: dispatchSnapshot.dispatchQrPayload || entry.dispatchQrPayload,
                dispatchQrImageUrl: dispatchSnapshot.dispatchQrImageUrl || entry.dispatchQrImageUrl,
                dispatchTokenExpiresAt: dispatchSnapshot.dispatchTokenExpiresAt || entry.dispatchTokenExpiresAt,
              },
              entry,
              dispatchQrCacheRef.current[request.id],
            );
          })
        );
      }

      const dispatchedShipment = mapShipmentFromPayload(dispatchResponse, request.id);
      if (dispatchedShipment) {
        setShipments((prev) => upsertShipment(prev, dispatchedShipment));
      }

      const detailAfterDispatch = await refreshRequestDetail(request.id).catch(() => null);
      const shipmentAfterDispatch = (await refreshShipmentForRequest(request.id).catch(() => null)) || dispatchedShipment;

      await loadRequests({ silent: true });

      const latestDispatchQrPayload = takeOpaqueQrPayload(
        dispatchSnapshot?.dispatchQrPayload,
        dispatchSnapshot?.dispatchToken,
        shipmentAfterDispatch?.dispatchQrPayload,
        shipmentAfterDispatch?.dispatchToken,
        detailAfterDispatch?.dispatchQrPayload,
        detailAfterDispatch?.dispatchToken,
      );

      toast({
        title: 'Dispatch initiated',
        description: latestDispatchQrPayload
          ? 'Dispatch QR generated. It is now ready for receiver scan confirmation.'
          : 'Dispatch submitted. Shipment QR will appear when backend data sync completes.',
      });
    } catch (err: unknown) {
      toast({ title: 'Dispatch failed', description: getErrorMessage(err) || 'Unable to dispatch request.', variant: 'destructive' });
    } finally {
      setActionLoading(false);
    }
  };

  const handleRequesterPayment = async (request: MappedRequest) => {
    setPaymentLoadingId(request.id);
    setActionLoading(true);
    try {
      const currentRequest = (await refreshRequestDetail(request.id)) || request;

      if (currentRequest.paymentRequired === false) {
        toast({
          title: 'Payment waived',
          description: currentRequest.paymentNote || 'Payment is not required for this request.',
        });
        return;
      }

      if (mapStatus(currentRequest.status) === 'PAYMENT_COMPLETED') {
        toast({
          title: 'Payment already completed',
          description: 'No gateway action is needed for this request.',
        });
        return;
      }

      const callbackUrl = buildPublicUrlFromLocation(window.location.href);
      callbackUrl.searchParams.set('payment_request_id', request.id);

      const idempotencyKey =
        paymentInitiationKeyByRequest[request.id] || buildPaymentIdempotencyKey(request.id);
      setPaymentInitiationKeyByRequest((prev) => ({
        ...prev,
        [request.id]: prev[request.id] || idempotencyKey,
      }));
      setPaymentPollingTimedOutByRequest((prev) => {
        if (!prev[request.id]) {
          return prev;
        }
        const next = { ...prev };
        delete next[request.id];
        return next;
      });

      const initiateResponse = await requestsApi.initiatePayment(request.id, {
        gateway: 'sslcommerz',
        return_url: callbackUrl.toString(),
        cancel_url: callbackUrl.toString(),
      }, idempotencyKey);

      const initiateData = unwrapDataRecord(initiateResponse);
      const paymentId = readString(initiateData, ['payment_id', 'id']);
      if (paymentId) {
        setLatestPaymentIdByRequest((prev) => ({
          ...prev,
          [request.id]: paymentId,
        }));
      }

      const gatewayRedirectUrl = parseGatewayRedirectUrl(initiateResponse);
      if (gatewayRedirectUrl) {
        setPaymentRedirectByRequest((prev) => ({
          ...prev,
          [request.id]: gatewayRedirectUrl,
        }));
      }

      await loadRequests({ silent: true });
      const refreshedAfterInitiation = await refreshRequestDetail(request.id).catch(() => null);

      if (!gatewayRedirectUrl) {
        const postInitiationRequest = refreshedAfterInitiation || currentRequest;
        const stageAfterInitiation = mapStatus(postInitiationRequest.status);
        const transactionAfterInitiation = normalizePaymentTerminal(
          postInitiationRequest.latestPaymentTransactionStatus || postInitiationRequest.paymentStatus
        );

        if (stageAfterInitiation === 'PAYMENT_COMPLETED' || transactionAfterInitiation === 'success') {
          toast({
            title: 'Payment auto-settled',
            description: 'Zero-price payment was settled automatically. Dispatch actions are now available.',
          });
          return;
        }

        setPaymentReturnRequestId(request.id);
        toast({
          title: 'Payment processing',
          description: 'No gateway redirect was required. Waiting for backend reconciliation to complete.',
        });
        return;
      }

      toast({
        title: 'Gateway checkout opened',
        description: 'Complete SSLCommerz checkout. Backend webhook will update payment state asynchronously.',
      });

      window.open(gatewayRedirectUrl, '_self');
    } catch (err: unknown) {
      const errorMessage = getErrorMessage(err);
      if (isPaymentNotRequiredError(errorMessage)) {
        await refreshRequestDetail(request.id).catch(() => null);
        toast({
          title: 'Payment waived',
          description: 'Payment is not required for this request. Workflow can continue without payment.',
        });
        return;
      }

      toast({
        title: 'Payment initiation failed',
        description: errorMessage,
        variant: 'destructive',
      });
    } finally {
      setPaymentLoadingId(null);
      setActionLoading(false);
    }
  };

  const handleManualPaymentRefresh = async (request: MappedRequest) => {
    setActionLoading(true);
    try {
      const refreshed = await refreshRequestDetail(request.id);
      if (!refreshed) {
        toast({
          title: 'Refresh failed',
          description: 'Unable to read latest payment status for this request.',
          variant: 'destructive',
        });
        return;
      }

      const current = mapStatus(refreshed.status);
      const transactionTerminal = normalizePaymentTerminal(
        refreshed.latestPaymentTransactionStatus || refreshed.paymentStatus
      );

      if (current === 'PAYMENT_COMPLETED' || transactionTerminal === 'success') {
        toast({
          title: 'Payment completed',
          description: 'Latest backend state confirms payment completion.',
        });
        setPaymentPollingTimedOutByRequest((prev) => {
          if (!prev[request.id]) {
            return prev;
          }
          const next = { ...prev };
          delete next[request.id];
          return next;
        });
        return;
      }

      if (current === 'FAILED' || current === 'CANCELLED' || transactionTerminal === 'failed') {
        toast({
          title: 'Payment failed',
          description:
            refreshed.failedReason ||
            refreshed.paymentNote ||
            refreshed.latestPaymentTransactionStatus ||
            refreshed.paymentStatus ||
            'Backend marked this payment as failed.',
          variant: 'destructive',
        });
        return;
      }

      toast({
        title: 'Payment still processing',
        description: 'Payment callback has not reached terminal state yet. Try again shortly.',
      });
    } catch (err: unknown) {
      toast({
        title: 'Unable to refresh payment status',
        description: getErrorMessage(err),
        variant: 'destructive',
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleCancelRequest = async (request: MappedRequest) => {
    setActionLoading(true);
    try {
      const reason = (cancelReasonByRequest[request.id] || '').trim();
      await requestsApi.cancelRequest(request.id, reason ? { reason } : {});
      await loadRequests({ silent: true });
      notifyResourceSharesUpdated();
      setCancelReasonByRequest((prev) => ({
        ...prev,
        [request.id]: '',
      }));
      setCancelFormOpenByRequest((prev) => ({
        ...prev,
        [request.id]: false,
      }));
      toast({
        title: 'Request cancelled',
        description: 'Cancellation was recorded successfully.',
      });
    } catch (err: unknown) {
      toast({
        title: 'Cancellation failed',
        description: getErrorMessage(err),
        variant: 'destructive',
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleInitiateReturn = async (request: MappedRequest) => {
    const reason = (returnReasonByRequest[request.id] || '').trim();
    if (!reason) {
      toast({
        title: 'Reason required',
        description: 'Return reason is required for dispatched/in-transit cancellation.',
        variant: 'destructive',
      });
      return;
    }

    setActionLoading(true);
    try {
      const response = await requestsApi.initiateReturn(request.id, { reason });
      const data = unwrapDataRecord(response);
      const shipmentStatus = readString(data, ['shipment_status']);
      const returnToken = takeToken(data.return_token);
      const workflowState = readString(data, ['workflow_state', 'status'], 'RETURNING');

      setRequests((prev) =>
        prev.map((entry) =>
          entry.id === request.id
            ? {
                ...entry,
                status: workflowState,
                shipmentStatus: shipmentStatus || entry.shipmentStatus,
                returnToken: returnToken || entry.returnToken,
              }
            : entry
        )
      );
      if (returnToken) {
        setVerifyReturnTokenByRequest((prev) => ({
          ...prev,
          [request.id]: returnToken,
        }));
      }

      await loadRequests({ silent: true });
      notifyResourceSharesUpdated();

      toast({
        title: 'Return started',
        description: `${shipmentStatus ? `Shipment status: ${shipmentStatus}. ` : ''}${returnToken ? `Return token: ${returnToken}` : 'Return token generated.'}`,
      });
    } catch (err: unknown) {
      toast({
        title: 'Unable to initiate return',
        description: getErrorMessage(err),
        variant: 'destructive',
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleVerifyReturn = async (request: MappedRequest, shipment: ShipmentInfo | null) => {
    const isSenderActor = !!userHospitalId && request.supplyingHospitalId === userHospitalId;
    const canVerifyReturnByPermission = hasAnyPermission(user, REQUEST_RETURN_VERIFY_PERMISSION_CODES);
    if (!isSenderActor || !canVerifyReturnByPermission) {
      toast({
        title: 'Not authorized',
        description: 'Return verification requires supplying-hospital ownership and return verification permission.',
        variant: 'destructive',
      });
      return;
    }

    const token = (verifyReturnTokenByRequest[request.id] || request.returnToken || shipment?.returnToken || '').trim();
    if (!token) {
      toast({
        title: 'Return token required',
        description: 'Provide a return token before verification.',
        variant: 'destructive',
      });
      return;
    }

    setActionLoading(true);
    try {
      await requestsApi.verifyReturn(request.id, { return_token: token });
      await loadRequests({ silent: true });
      toast({
        title: 'Return verified',
        description: 'Return verification completed successfully.',
      });
    } catch (err: unknown) {
      toast({
        title: 'Return verification failed',
        description: getErrorMessage(err),
        variant: 'destructive',
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleConfirmReceipt = async (
    request: MappedRequest,
    receiverTokenInput: string,
    fallbackDispatchQrPayload: string,
  ): Promise<boolean> => {
    const currentStage = mapStatus(request.status);
    const isReceiverActor = !!userHospitalId && request.requestingHospitalId === userHospitalId;
    const canTransferConfirm = hasAnyPermission(user, REQUEST_TRANSFER_CONFIRM_PERMISSION_CODES);

    if (!hasHealthcareContext || !isReceiverActor || !canTransferConfirm) {
      toast({
        title: 'Not authorized',
        description: 'Receiver confirmation requires healthcare context, requesting-hospital ownership, and transfer confirmation permission.',
        variant: 'destructive',
      });
      return false;
    }

    if (TERMINAL_WORKFLOW_STATES.has(currentStage) || request.completionStage === 'RECEIVER_CONFIRMED') {
      toast({
        title: 'Workflow is already closed',
        description: 'Receiver confirmation is blocked for terminal workflows.',
        variant: 'destructive',
      });
      return false;
    }

    if (currentStage !== 'IN_TRANSIT') {
      toast({
        title: 'Receiver confirmation not available',
        description: 'Receiver confirmation is only available in IN_TRANSIT stage.',
        variant: 'destructive',
      });
      return false;
    }

    const quantityReceived = Math.max(1, request.quantity || 1);
    const payloadValidation = validateTransferConfirmInput(
      takeOpaqueQrPayload(receiverTokenInput, fallbackDispatchQrPayload),
      quantityReceived,
    );
    if (payloadValidation.error || !payloadValidation.payload) {
      toast({
        title: 'QR payload required',
        description: payloadValidation.error || 'Scan or paste the qrPayload before confirming receipt.',
        variant: 'destructive',
      });
      clearReceiverTokenInput(request.id);
      return false;
    }

    const actionKey = `${request.id}:receiver`;
    const idempotencyKey = transferIdempotencyByAction[actionKey] || buildTransferIdempotencyKey(request.id, 'receiver');
    setTransferIdempotencyByAction((prev) => ({
      ...prev,
      [actionKey]: prev[actionKey] || idempotencyKey,
    }));

    setActionLoading(true);
    try {
      await requestsApi.transferConfirm(request.id, payloadValidation.payload, idempotencyKey);

      const refreshed = await refreshRequestDetail(request.id).catch(() => null);
      await refreshShipmentForRequest(request.id).catch(() => null);
      await loadRequests({ silent: true });
      notifyResourceSharesUpdated();
      clearReceiverTokenInput(request.id);

      if (refreshed?.completionStage === 'RECEIVER_CONFIRMED' || mapStatus(refreshed?.status || '') === 'COMPLETED') {
        toast({ title: 'Delivery successfully confirmed', description: 'Workflow completed' });
      } else {
        toast({ title: 'Delivery confirmation submitted', description: 'Backend accepted confirmation and is syncing workflow state.' });
      }
      return true;
    } catch (err: unknown) {
      const mappedError = mapTransferConfirmationError(err);
      toast({ title: mappedError.title, description: mappedError.description, variant: 'destructive' });
      if (mappedError.clearEnteredToken) {
        clearReceiverTokenInput(request.id);
      }
      return false;
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <AppLayout title={pageTitle}
        // subtitle="Track and manage resource requests with SLA monitoring"
      >
        <div className="flex items-center justify-center h-64">
          <Loader2 className="h-8 w-8 animate-spin" />
          <span className="ml-2">Loading requests...</span>
        </div>
      </AppLayout>
    );
  }

  if (error) {
    return (
      <AppLayout title={pageTitle}
        // subtitle="Track and manage resource requests with SLA monitoring"
      >
        <div className="flex flex-col items-center justify-center h-64">
          <AlertTriangle className="h-12 w-12 text-destructive mb-4" />
          <h3 className="text-lg font-medium mb-2">Failed to load requests</h3>
          <p className="text-muted-foreground">{error}</p>
          <Button className="mt-4" variant="outline" onClick={() => void loadRequests()}>Retry</Button>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout title={pageTitle}
      // subtitle="Track and manage incoming/outgoing requests with integrated transport workflow"
    >
      <div className="space-y-5 pb-8">
        <Card className="overflow-hidden border border-border/70 bg-gradient-to-br from-background to-muted/40 shadow-sm">
          <CardContent className="p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
              <p className="text-base font-semibold">Sorting and Filters</p>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  setSortBy('newest');
                  setHospitalFilter('all');
                  setResourceTypeFilter('all');
                  setStatusFilter('all');
                  setRequestDateFilter('all');
                  setCustomDateFrom('');
                  setCustomDateTo('');
                }}
              >
                Reset
              </Button>
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
              <Select value={sortBy} onValueChange={(value) => setSortBy(value as SortBy)}>
                <SelectTrigger className="h-10 rounded-lg bg-background/90"><SelectValue placeholder="Sort by" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="newest">Newest</SelectItem>
                  <SelectItem value="status">Status</SelectItem>
                  <SelectItem value="hospital">Hospital</SelectItem>
                  <SelectItem value="urgency">Urgency</SelectItem>
                </SelectContent>
              </Select>

              <Select value={hospitalFilter} onValueChange={setHospitalFilter}>
                <SelectTrigger className="h-10 rounded-lg bg-background/90"><SelectValue placeholder="Hospital" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Hospitals</SelectItem>
                  {hospitalOptions.map((hospital) => (
                    <SelectItem key={hospital} value={hospital}>{hospital}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select value={resourceTypeFilter} onValueChange={setResourceTypeFilter}>
                <SelectTrigger className="h-10 rounded-lg bg-background/90"><SelectValue placeholder="Resource type" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Types</SelectItem>
                  <SelectItem value="drugs">Drugs</SelectItem>
                  <SelectItem value="blood">Blood</SelectItem>
                  <SelectItem value="organs">Organs</SelectItem>
                  <SelectItem value="equipment">Equipment</SelectItem>
                </SelectContent>
              </Select>

              <Select value={statusFilter} onValueChange={setStatusFilter}>
                <SelectTrigger className="h-10 rounded-lg bg-background/90"><SelectValue placeholder="Status" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Statuses</SelectItem>
                  {WORKFLOW_STATES.map((state) => (
                    <SelectItem key={state} value={state}>{STAGE_LABEL[state]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select
                value={requestDateFilter}
                onValueChange={(value) => setRequestDateFilter(value as 'all' | 'today' | 'last_7_days' | 'custom')}
              >
                <SelectTrigger className="h-10 rounded-lg bg-background/90"><SelectValue placeholder="Request date" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Dates</SelectItem>
                  <SelectItem value="today">Today</SelectItem>
                  <SelectItem value="last_7_days">Last 7 days</SelectItem>
                  <SelectItem value="custom">Custom range</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {requestDateFilter === 'custom' ? (
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <div className="space-y-1">
                  <Label htmlFor="custom-date-from">From date</Label>
                  <Input
                    id="custom-date-from"
                    type="date"
                    value={customDateFrom}
                    onChange={(event) => setCustomDateFrom(event.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="custom-date-to">To date</Label>
                  <Input
                    id="custom-date-to"
                    type="date"
                    value={customDateTo}
                    onChange={(event) => setCustomDateTo(event.target.value)}
                  />
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>

        {view === 'combined' ? (
          <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as WorkflowTab)}>
            <TabsList className="grid w-full max-w-[420px] grid-cols-2 rounded-lg bg-muted/60 p-1">
              <TabsTrigger className="rounded-md data-[state=active]:bg-background data-[state=active]:shadow-sm" value="incoming">
                Incoming Requests ({displayIncoming.length})
              </TabsTrigger>
              <TabsTrigger className="rounded-md data-[state=active]:bg-background data-[state=active]:shadow-sm" value="outgoing">
                Outgoing Requests ({displayOutgoing.length})
              </TabsTrigger>
            </TabsList>

            <TabsContent value="incoming" className="space-y-4">
              {displayIncoming.length === 0 && (
                <div className="text-center py-12 text-muted-foreground">No incoming resource requests found.</div>
              )}
            </TabsContent>

            <TabsContent value="outgoing" className="space-y-4">
              {displayOutgoing.length === 0 && (
                <div className="text-center py-12 text-muted-foreground">No outgoing resource requests found.</div>
              )}
            </TabsContent>
          </Tabs>
        ) : (
          <Card className="border border-border/70 bg-background/70 shadow-sm">
            <CardContent className="p-4">
              <p className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                {effectiveTab === 'incoming' ? 'Incoming Requests' : 'Outgoing Requests'} ({displayRequests.length})
              </p>
            </CardContent>
          </Card>
        )}

        {view !== 'combined' && displayRequests.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            {effectiveTab === 'incoming'
              ? 'No incoming resource requests found.'
              : 'No outgoing resource requests found.'}
          </div>
        ) : null}

        {displayRequests.map((request) => {
          const isExpanded = expandedId === request.id;
          const currentStage = mapStatus(request.status);
          const mappedStatus = mapStatus(request.status);

          const shipment = getRequestShipment(request);
          const dispatchQrValue = getDispatchQrValue(request, shipment);
          const dispatchQrImageUrl = getDispatchQrImageUrl(request, shipment);
          const effectiveDispatchQrPayload = takeOpaqueQrPayload(dispatchQrValue);

          const isSender = !!userHospitalId && request.supplyingHospitalId === userHospitalId;
          const isReceiver = !!userHospitalId && request.requestingHospitalId === userHospitalId;
          const canApproveRequest = hasAnyPermission(user, REQUEST_APPROVE_PERMISSION_CODES);
          const canDispatchRequest = hasAnyPermission(user, REQUEST_DISPATCH_PERMISSION_CODES);
          const canTransferConfirm = hasAnyPermission(user, REQUEST_TRANSFER_CONFIRM_PERMISSION_CODES);
          const canReturnVerifyByPermission = hasAnyPermission(user, REQUEST_RETURN_VERIFY_PERMISSION_CODES);

          const dispatchEligibility = (() => {
            const eligibility = getDispatchEligibility(request);
            if (!isSender || canDispatchRequest) {
              return eligibility;
            }

            return {
              canDispatch: false,
              reason: 'Dispatch blocked: you do not have request dispatch permission.',
            };
          })();

          const effectiveLatestPaymentId = request.latestPaymentId || latestPaymentIdByRequest[request.id] || '';
          const receiverConfirmed = request.completionStage === 'RECEIVER_CONFIRMED' || currentStage === 'COMPLETED';
          const isWorkflowTerminal = TERMINAL_WORKFLOW_STATES.has(currentStage) || receiverConfirmed;
          const dispatchQrExpiresAt = takeToken(request.dispatchTokenExpiresAt, shipment?.tokenExpiresAt);
          const dispatchQrExpired = isExpiredTimestamp(dispatchQrExpiresAt);
          const showDispatchQrPanel =
            isSender &&
            !isWorkflowTerminal &&
            (isOpaqueQrPayloadPresent(dispatchQrValue) || Boolean(dispatchQrImageUrl));
          const canReceiverConfirm =
            hasHealthcareContext &&
            isReceiver &&
            canTransferConfirm &&
            currentStage === 'IN_TRANSIT' &&
            !isWorkflowTerminal;
          const receiverTokenInput = receiverTokenInputByRequest[request.id] || '';
          const receiverTokenCandidate = takeOpaqueQrPayload(receiverTokenInput, effectiveDispatchQrPayload);
          const canReceiverSubmitToken = isOpaqueQrPayloadPresent(receiverTokenCandidate);
          const paymentIsWaived = request.paymentRequired === false;
          const paymentTransactionTerminal = normalizePaymentTerminal(
            request.latestPaymentTransactionStatus || request.paymentStatus
          );
          const paymentState = paymentIsWaived
            ? 'waived'
            : currentStage === 'PAYMENT_COMPLETED' || paymentTransactionTerminal === 'success'
                ? 'success'
                : currentStage === 'FAILED' || paymentTransactionTerminal === 'failed'
                  ? 'failed'
                  : currentStage === 'PAYMENT_PENDING'
                    ? 'processing'
                  : 'not_started';
          const paymentFailureContext =
            request.failedReason ||
            request.paymentNote ||
            request.latestPaymentTransactionStatus ||
            request.paymentStatus ||
            'Gateway callback marked this payment as failed.';
          const canCancelPreDispatch =
            isReceiver && ['PENDING', 'APPROVED', 'RESERVED'].includes(currentStage);
          const canInitiateReturn = isReceiver && currentStage === 'IN_TRANSIT';
          const hasReturnPendingContext =
            currentStage === 'RETURNING' ||
            (currentStage === 'CANCELLED' && (
              Boolean(request.returnToken || shipment?.returnToken) ||
              normalizeStatus(request.shipmentStatus || shipment?.status || '').includes('return')
            ));
          const canVerifyReturn = isSender && hasReturnPendingContext && canReturnVerifyByPermission;
          const effectiveReturnToken =
            verifyReturnTokenByRequest[request.id] || request.returnToken || shipment?.returnToken || '';
          const receiverConfirmDisabledReason = (() => {
            if (receiverConfirmed) return 'Receiver confirmation already recorded.';
            if (!hasHealthcareContext) return 'Healthcare context is required for receiver confirmation.';
            if (!isReceiver) return 'Only the requesting hospital can perform receiver confirmation.';
            if (!canTransferConfirm) {
              return 'Missing permission: hospital:request.transfer.confirm.';
            }
            if (currentStage !== 'IN_TRANSIT') return 'Receiver confirmation is only available during IN_TRANSIT stage.';
            if (isWorkflowTerminal) return 'Workflow is terminal; no further completion actions are allowed.';
            return '';
          })();

          return (
            <Collapsible key={request.id} open={isExpanded} onOpenChange={() => setExpandedId(isExpanded ? null : request.id)}>
              <Card
                className={
                  request.urgency === 'critical'
                    ? 'border border-destructive/50 shadow-sm'
                    : 'border border-border/70 shadow-sm transition-shadow hover:shadow-md'
                }
              >
                <CollapsibleTrigger asChild>
                  <CardContent className="cursor-pointer p-4 transition-colors hover:bg-muted/30 md:p-5">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex items-start gap-3">
                        <div className="mt-0.5 flex h-11 w-11 items-center justify-center rounded-xl bg-muted text-2xl shadow-inner">
                          {request.resourceType === 'blood' ? '🩸' : request.resourceType === 'drugs' ? '💊' : request.resourceType === 'organs' ? '🫀' : '🏥'}
                        </div>
                        <div className="space-y-1">
                          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{formatCompactDateTime(request.requestedAt)}</p>
                          <h3 className="text-base font-semibold leading-tight">{request.resourceName}</h3>
                          <p className="text-sm text-muted-foreground">{request.requestingHospital} {'->'} {request.providingHospital}</p>
                        </div>
                      </div>

                      <div className="flex shrink-0 items-center gap-2">
                        <Badge variant="outline" className="hidden sm:inline-flex">{STAGE_LABEL[mappedStatus]}</Badge>
                        <Badge variant={request.urgency === 'critical' ? 'destructive' : request.urgency === 'urgent' ? 'default' : 'secondary'}>
                          {request.urgency}
                        </Badge>
                        <Badge variant="outline">{request.quantity} units</Badge>
                        <div className="rounded-md border border-border/70 p-1 text-muted-foreground">
                          {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </CollapsibleTrigger>

                <CollapsibleContent>
                  <CardContent className="space-y-5 border-t bg-muted/20 px-5 pb-6 pt-4 md:px-6">
                    <RequestStatusStepper
                      status={mappedStatus}
                      urgency={request.urgency}
                      requestedAt={request.requestedAt}
                      reservationExpiry={request.reservationExpiry}
                      estimatedDelivery={request.estimatedDelivery}
                    />

                    <div className="grid gap-4 lg:grid-cols-5">
                      <div className="space-y-3 rounded-xl border bg-background/80 p-4 lg:col-span-2">
                        <p className="text-sm font-medium">SLA</p>
                        <SLATimer
                          targetTime={getSLATarget(request.requestedAt, request.urgency)}
                          urgency={request.urgency}
                          status={mappedStatus}
                        />
                      </div>
                      <div className="space-y-3 rounded-xl border bg-background/80 p-4 lg:col-span-3">
                        <p className="text-sm font-medium">Clinical metadata</p>
                        <ClinicalMetadataBadges
                          metadata={{
                            bloodType: request.bloodType,
                            coldChainRequired: request.coldChainRequired,
                            coldChainTemp: request.coldChainTemp,
                            lotNumber: request.lotNumber,
                            expiryDate: request.expiryDate,
                          }}
                          compact
                        />
                        {request.justification ? (
                          <p className="text-sm italic text-muted-foreground">"{request.justification}"</p>
                        ) : (
                          <p className="text-xs text-muted-foreground">No additional notes.</p>
                        )}
                      </div>
                    </div>

                    {showDispatchQrPanel ? (
                      <div className="rounded-xl border bg-background/80 p-4">
                        <p className="text-sm font-medium">Dispatch QR</p>
                        {dispatchQrExpired ? (
                          <p className="mt-1 text-xs text-warning">Dispatch QR token is expired. Regenerate dispatch QR before receiver scan.</p>
                        ) : null}
                        {(() => {
                          const downloadFormat = dispatchQrFormatByRequest[request.id] || 'png';
                          const displaySize = dispatchQrSizeByRequest[request.id] || DEFAULT_QR_DISPLAY_SIZE;
                          const qrPixelSize = Number(displaySize);
                          const quietZonePx = getQrQuietZoneSize(qrPixelSize);
                          const extension = downloadFormat === 'jpg' ? 'jpg' : downloadFormat;
                          const fileName = `dispatch-qr.${extension}`;
                          const printableSectionId = `dispatch-qr-preview-${request.id}`;

                          return (
                            <div className="mt-3 flex flex-wrap items-end gap-3">
                              <div
                                id={printableSectionId}
                                className="inline-flex items-center justify-center rounded-md border border-slate-200 bg-white"
                                style={{ padding: quietZonePx }}
                              >
                                {dispatchQrValue ? (
                                  <QRCodeSVG
                                    id={`dispatch-qr-${request.id}`}
                                    value={dispatchQrValue}
                                    size={qrPixelSize}
                                    bgColor="#FFFFFF"
                                    fgColor="#000000"
                                  />
                                ) : null}
                                {!dispatchQrValue && dispatchQrImageUrl ? (
                                  <img
                                    src={dispatchQrImageUrl}
                                    alt="Dispatch QR"
                                    className="object-contain"
                                    style={{ width: qrPixelSize, height: qrPixelSize }}
                                  />
                                ) : null}
                              </div>
                              <div className="min-w-[9rem] space-y-2">
                                <Label className="text-xs text-muted-foreground">Download format</Label>
                                <Select
                                  value={downloadFormat}
                                  onValueChange={(value) => {
                                    setDispatchQrFormatByRequest((prev) => ({
                                      ...prev,
                                      [request.id]: value as QrDownloadFormat,
                                    }));
                                  }}
                                >
                                  <SelectTrigger className="rounded-lg bg-background"><SelectValue /></SelectTrigger>
                                  <SelectContent>
                                    <SelectItem value="png">PNG</SelectItem>
                                    <SelectItem value="jpg">JPG</SelectItem>
                                    <SelectItem value="svg">SVG</SelectItem>
                                  </SelectContent>
                                </Select>
                              </div>
                              <div className="min-w-[10rem] space-y-2">
                                <Label className="text-xs text-muted-foreground">QR size</Label>
                                <Select
                                  value={displaySize}
                                  onValueChange={(value) => {
                                    setDispatchQrSizeByRequest((prev) => ({
                                      ...prev,
                                      [request.id]: value as QrDisplaySize,
                                    }));
                                  }}
                                >
                                  <SelectTrigger className="rounded-lg bg-background"><SelectValue /></SelectTrigger>
                                  <SelectContent>
                                    {QR_DISPLAY_SIZE_OPTIONS.map((option) => (
                                      <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                                    ))}
                                  </SelectContent>
                                </Select>
                              </div>
                              <Button
                                type="button"
                                variant="outline"
                                disabled={dispatchQrExpired}
                                onClick={async () => {
                                  const ok = dispatchQrValue
                                    ? await downloadQrSvg(`dispatch-qr-${request.id}`, fileName, downloadFormat, qrPixelSize)
                                    : await downloadQrImage(dispatchQrImageUrl, fileName, downloadFormat, qrPixelSize);

                                  if (!ok) {
                                    toast({ title: 'QR download failed', description: 'Generated QR not found.', variant: 'destructive' });
                                  }
                                }}
                              >
                                Download QR code
                              </Button>
                              <Button
                                type="button"
                                variant="outline"
                                disabled={dispatchQrExpired}
                                onClick={() => {
                                  const ok = printQrSection(printableSectionId, 'Dispatch QR', Math.max(qrPixelSize, 420));
                                  if (!ok) {
                                    toast({ title: 'Print unavailable', description: 'Unable to open printable QR preview.', variant: 'destructive' });
                                  }
                                }}
                              >
                                Print QR code
                              </Button>
                            </div>
                          );
                        })()}
                      </div>
                    ) : null}

                    {canCancelPreDispatch && (
                      <div className="space-y-3 rounded-xl border bg-background/80 p-4">
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-sm font-medium">Cancel Request</p>
                          <Button
                            size="sm"
                            variant={cancelFormOpenByRequest[request.id] ? 'secondary' : 'destructive'}
                            onClick={() => {
                              setCancelFormOpenByRequest((prev) => ({
                                ...prev,
                                [request.id]: !prev[request.id],
                              }));
                            }}
                          >
                            {cancelFormOpenByRequest[request.id] ? 'Hide form' : 'Cancel request'}
                          </Button>
                        </div>

                        {cancelFormOpenByRequest[request.id] ? (
                          <>
                            <Textarea
                              rows={2}
                              value={cancelReasonByRequest[request.id] || ''}
                              onChange={(event) => {
                                const value = event.target.value;
                                setCancelReasonByRequest((prev) => ({
                                  ...prev,
                                  [request.id]: value,
                                }));
                              }}
                              placeholder="Optional cancellation reason"
                            />
                            <div className="flex flex-wrap gap-2">
                              <Button
                                size="sm"
                                variant="destructive"
                                disabled={actionLoading}
                                onClick={() => handleCancelRequest(request)}
                              >
                                Confirm cancellation
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={actionLoading}
                                onClick={() => {
                                  setCancelFormOpenByRequest((prev) => ({
                                    ...prev,
                                    [request.id]: false,
                                  }));
                                }}
                              >
                                Dismiss
                              </Button>
                            </div>
                          </>
                        ) : null}
                      </div>
                    )}

                    {canInitiateReturn && (
                      <div className="space-y-3 rounded-xl border bg-background/80 p-4">
                        <p className="text-sm font-medium">Initiate Return</p>
                        <Textarea
                          rows={2}
                          value={returnReasonByRequest[request.id] || ''}
                          onChange={(event) => {
                            const value = event.target.value;
                            setReturnReasonByRequest((prev) => ({
                              ...prev,
                              [request.id]: value,
                            }));
                          }}
                          placeholder="Reason for initiating return"
                        />
                        <Button disabled={actionLoading} onClick={() => handleInitiateReturn(request)}>
                          Initiate return
                        </Button>
                      </div>
                    )}

                    {canVerifyReturn && (
                      <div className="space-y-3 rounded-xl border bg-background/80 p-4">
                        <p className="text-sm font-medium">Verify Return</p>
                        <Input
                          value={effectiveReturnToken}
                          onChange={(event) => {
                            const value = event.target.value;
                            setVerifyReturnTokenByRequest((prev) => ({
                              ...prev,
                              [request.id]: value,
                            }));
                          }}
                          placeholder="Return token"
                        />
                        <Button variant="secondary" disabled={actionLoading} onClick={() => handleVerifyReturn(request, shipment)}>
                          Verify return
                        </Button>
                      </div>
                    )}

                    {hasReturnPendingContext && (
                      <div className="space-y-2 rounded-xl border bg-background/80 p-4">
                        <p className="text-sm font-medium">Return in progress</p>
                        <p className="text-sm"><span className="font-medium">Shipment status:</span> {request.shipmentStatus || shipment?.status || 'RETURNING'}</p>
                        <p className="text-sm"><span className="font-medium">Return token:</span> <span className="font-mono text-xs break-all">{request.returnToken || shipment?.returnToken || 'Not available yet'}</span></p>
                      </div>
                    )}

                    {currentStage === 'PENDING' && (
                      <div className="space-y-3 rounded-xl border bg-background/80 p-4">
                        <p className="text-sm font-medium">Pending</p>
                        {isSender ? (
                          <div className="space-y-3">
                            {!canApproveRequest ? (
                              <p className="text-sm text-destructive">Approval actions are disabled: missing request approval permission.</p>
                            ) : null}
                            <div className="flex items-center space-x-2">
                              <Checkbox
                                id={`waive-payment-${request.id}`}
                                checked={Boolean(waivePaymentByRequest[request.id])}
                                onCheckedChange={(checked) => {
                                  setWaivePaymentByRequest((prev) => ({
                                    ...prev,
                                    [request.id]: checked === true,
                                  }));
                                }}
                              />
                              <Label htmlFor={`waive-payment-${request.id}`} className="text-sm font-normal">
                                Waive payment for requester on approval
                              </Label>
                            </div>

                            <div className="flex gap-2">
                              <Button
                                size="sm"
                                onClick={() => handleDecision(request, 'approved')}
                                disabled={
                                  decisionLoadingId === request.id ||
                                  !isPendingForProviderAction(request.status) ||
                                  !canApproveRequest
                                }
                              >
                                {decisionLoadingId === request.id && decisionProcessing === 'approved' ? (
                                  <>
                                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    Approving...
                                  </>
                                ) : (
                                  'Approve request'
                                )}
                              </Button>
                              <Button
                                size="sm"
                                variant="destructive"
                                onClick={() => handleDecision(request, 'rejected')}
                                disabled={
                                  decisionLoadingId === request.id ||
                                  !isPendingForProviderAction(request.status) ||
                                  !canApproveRequest
                                }
                              >
                                {decisionLoadingId === request.id && decisionProcessing === 'rejected' ? (
                                  <>
                                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    Rejecting...
                                  </>
                                ) : (
                                  'Reject request'
                                )}
                              </Button>
                            </div>
                          </div>
                        ) : (
                          <p className="text-sm text-muted-foreground">Awaiting supplier decision.</p>
                        )}
                      </div>
                    )}

                    {['APPROVED', 'RESERVED', 'PAYMENT_PENDING', 'PAYMENT_COMPLETED'].includes(currentStage) && (
                      <div className="space-y-3 rounded-xl border bg-background/80 p-4">
                        <p className="text-sm font-medium">Approved / Reserved / Payment</p>

                        {isSender ? (
                          <>
                            {paymentIsWaived ? (
                              <p className="text-sm text-muted-foreground">
                                Payment has been waived for this request.
                                {request.paymentNote ? ` Note: ${request.paymentNote}` : ''}
                              </p>
                            ) : null}

                            <div className="grid gap-3 md:grid-cols-2">
                              <div>
                                <Label>Delivery personnel source</Label>
                                <Select value={personnelMode} onValueChange={(value) => setPersonnelMode(value as 'existing' | 'external')}>
                                  <SelectTrigger><SelectValue /></SelectTrigger>
                                  <SelectContent>
                                    <SelectItem value="existing">Select from existing staff</SelectItem>
                                    <SelectItem value="external">Temporary or external personnel</SelectItem>
                                  </SelectContent>
                                </Select>
                              </div>
                              <div>
                                <Label>Vehicle details</Label>
                                <Input value={vehicleInfo} onChange={(event) => setVehicleInfo(event.target.value)} placeholder="Vehicle number/details" />
                              </div>
                            </div>

                            {personnelMode === 'existing' ? (
                              <div>
                                <Label>Select delivery personnel</Label>
                                <Select value={selectedStaffId} onValueChange={setSelectedStaffId}>
                                  <SelectTrigger><SelectValue placeholder="Choose staff" /></SelectTrigger>
                                  <SelectContent>
                                    {staffRows.length === 0 ? (
                                      <SelectItem value="none" disabled>No staff available</SelectItem>
                                    ) : (
                                      staffRows.map((staff) => (
                                        <SelectItem key={staff.id} value={staff.id}>{staff.fullName}</SelectItem>
                                      ))
                                    )}
                                  </SelectContent>
                                </Select>
                              </div>
                            ) : (
                              <div className="grid gap-3 md:grid-cols-2">
                                <div>
                                  <Label>Temporary delivery personnel name</Label>
                                  <Input value={externalPersonnelName} onChange={(event) => setExternalPersonnelName(event.target.value)} placeholder="Full name" />
                                </div>
                                <div>
                                  <Label>Temporary delivery personnel phone</Label>
                                  <Input value={externalPersonnelPhone} onChange={(event) => setExternalPersonnelPhone(event.target.value)} placeholder="Phone" />
                                </div>
                              </div>
                            )}

                            <div>
                              <Label>Dispatch notes</Label>
                              <Textarea rows={2} value={dispatchNote} onChange={(event) => setDispatchNote(event.target.value)} placeholder="Transport/handling notes" />
                            </div>

                            {!dispatchEligibility.canDispatch ? (
                              <p className="text-sm text-destructive">{dispatchEligibility.reason}</p>
                            ) : null}

                            <Button size="sm" disabled={actionLoading || !dispatchEligibility.canDispatch} onClick={() => handleDispatch(request)}>
                              <Truck className="h-4 w-4 mr-2" /> Assign delivery personnel and dispatch
                            </Button>
                          </>
                        ) : (
                          <div className="space-y-3">
                            {paymentIsWaived ? (
                              <div className="rounded-xl border bg-background p-4">
                                <p className="text-sm font-medium">Payment waived</p>
                                <p className="text-sm text-muted-foreground">
                                  {request.paymentNote || 'Supplier waived payment. You can continue without payment steps.'}
                                </p>
                              </div>
                            ) : (
                              <div className="space-y-2 rounded-xl border bg-background p-4">
                                <p className="text-sm font-medium">Requester payment action</p>
                                <p className="text-xs text-muted-foreground">Latest payment ID: {effectiveLatestPaymentId || 'not available yet'}</p>
                                <p className="text-xs text-muted-foreground">
                                  Latest transaction status: {request.latestPaymentTransactionStatus || request.paymentStatus || 'not available yet'}
                                </p>
                                <div className="flex flex-wrap gap-2">
                                  <Button
                                    size="sm"
                                    disabled={actionLoading || paymentState === 'success'}
                                    onClick={() => handleRequesterPayment(request)}
                                  >
                                    {paymentLoadingId === request.id ? (
                                      <>
                                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                        Opening gateway...
                                      </>
                                    ) : paymentState === 'success' ? (
                                      'Payment completed'
                                    ) : (
                                      'Proceed to SSLCommerz'
                                    )}
                                  </Button>
                                  {paymentRedirectByRequest[request.id] ? (
                                    <Button size="sm" variant="outline" asChild>
                                      <a href={paymentRedirectByRequest[request.id]} target="_blank" rel="noreferrer">
                                        Open saved gateway link
                                      </a>
                                    </Button>
                                  ) : null}
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    disabled={actionLoading}
                                    onClick={() => handleManualPaymentRefresh(request)}
                                  >
                                    Refresh payment status
                                  </Button>
                                </div>
                                {paymentPollingTimedOutByRequest[request.id] ? (
                                  <p className="text-xs text-warning">Auto polling timed out. Use Refresh payment status to continue.</p>
                                ) : null}
                              </div>
                            )}

                            <p className="text-sm"><span className="font-medium">Delivery personnel:</span> {shipment?.deliveryPersonnelName || 'Not assigned yet'}</p>
                            <p className="text-sm"><span className="font-medium">Contact:</span> {shipment?.deliveryPersonnelPhone || 'Not available'}</p>
                          </div>
                        )}
                      </div>
                    )}

                    {currentStage === 'IN_TRANSIT' && isReceiver && (
                      <div className="space-y-3 rounded-xl border bg-background/80 p-4">
                        <p className="text-sm font-medium">In Transit</p>

                        <div className="space-y-3 rounded-xl border bg-background p-4">
                          <p className="text-sm font-medium">Receiver confirmation</p>
                          <div>
                            <Label>Scanned QR payload</Label>
                            <Input
                              value={receiverTokenInput}
                              onChange={(event) => {
                                const value = event.target.value;
                                setReceiverTokenInputByRequest((prev) => ({
                                  ...prev,
                                  [request.id]: value,
                                }));
                              }}
                              placeholder="Scan or paste opaque qrPayload"
                            />
                          </div>
                          {!receiverTokenInput && !effectiveDispatchQrPayload ? (
                            <p className="text-xs text-muted-foreground">
                              Scan QR or paste qrPayload to confirm receiver handover.
                            </p>
                          ) : null}
                          <div className="flex flex-wrap items-center gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => {
                                try {
                                  window.sessionStorage.setItem(DISPATCH_SCAN_REQUEST_CONTEXT_KEY, request.id);
                                } catch {
                                  // Ignore storage errors.
                                }
                                window.location.assign(`/dispatch/scan?requestId=${encodeURIComponent(request.id)}`);
                              }}
                            >
                              Scan QR
                            </Button>
                            <Button
                              size="sm"
                              disabled={actionLoading || !canReceiverConfirm || !canReceiverSubmitToken || receiverConfirmed}
                              onClick={() => {
                                void handleConfirmReceipt(request, receiverTokenInput, effectiveDispatchQrPayload);
                              }}
                            >
                              {receiverConfirmed ? 'Delivery already confirmed' : 'Confirm receiver handover'}
                            </Button>
                          </div>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={!receiverTokenInput}
                            onClick={() => clearReceiverTokenInput(request.id)}
                          >
                            Clear entered payload
                          </Button>
                          {!canReceiverConfirm && !receiverConfirmed ? (
                            <p className="text-xs text-warning">{receiverConfirmDisabledReason}</p>
                          ) : null}
                        </div>
                      </div>
                    )}

                    {currentStage === 'COMPLETED' && (
                      <div className="space-y-3 rounded-xl border bg-background/80 p-4">
                        <p className="text-sm font-medium">Completed</p>
                        <p className="text-sm text-muted-foreground">
                          Delivery was confirmed by QR scan and the workflow is now closed.
                        </p>
                      </div>
                    )}

                    {['FAILED', 'CANCELLED', 'EXPIRED'].includes(currentStage) && (
                      <div className="space-y-3 rounded-xl border bg-background/80 p-4">
                        <p className="text-sm font-medium">Terminal State</p>
                        {currentStage === 'FAILED' ? (
                          <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4">
                            <p className="text-sm font-medium text-destructive">Payment failure context</p>
                            <p className="text-sm text-muted-foreground mt-1">{paymentFailureContext}</p>
                          </div>
                        ) : null}
                        <div className="grid gap-2 md:grid-cols-2 text-sm">
                          <p><span className="font-medium">Workflow state:</span> {currentStage}</p>
                          <p><span className="font-medium">Shipment:</span> {shipment?.id || 'N/A'}</p>
                          <p><span className="font-medium">Resource:</span> {request.resourceName}</p>
                        </div>
                      </div>
                    )}
                  </CardContent>
                </CollapsibleContent>
              </Card>
            </Collapsible>
          );
        })}
      </div>
    </AppLayout>
  );
};

export default RequestWorkflow;
