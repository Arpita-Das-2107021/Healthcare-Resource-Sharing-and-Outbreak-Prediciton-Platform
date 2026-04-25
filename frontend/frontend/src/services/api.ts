import { REFUND_CAUSE } from '@/types/refund';

/**
 * API Service for Hospital Resource Sharing System
 *
 * Base URL: http://localhost:8000
 * All v1 endpoints are under /api/v1/
 * Auth endpoints are under /api/auth/
 */

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const API_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 15000);

async function fetchWithTimeout(url: string, options: RequestInit = {}, timeoutMs = API_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);

  if (options.signal) {
    if (options.signal.aborted) {
      controller.abort();
    } else {
      options.signal.addEventListener('abort', () => controller.abort(), { once: true });
    }
  }

  try {
    return await fetch(url, {
      ...options,
      signal: controller.signal,
    });
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function getStoredAccessToken(): string | null {
  return localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
}

function hasStoredRefreshToken(): boolean {
  return !!(
    localStorage.getItem('refresh_token') ||
    sessionStorage.getItem('refresh_token')
  );
}

function createIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

async function parseResponseBody(response: Response): Promise<unknown> {
  if (response.status === 204 || response.status === 205) {
    return null;
  }

  const rawBody = await response.text();
  if (!rawBody) {
    return null;
  }

  try {
    return JSON.parse(rawBody);
  } catch {
    return { detail: rawBody };
  }
}

function extractErrorMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== 'object') {
    return fallback;
  }

  const envelopeError = payload.error;
  if (typeof envelopeError === 'string' && envelopeError.trim()) {
    return envelopeError;
  }

  if (typeof envelopeError === 'object' && envelopeError) {
    const nested = envelopeError.message || envelopeError.detail;
    if (typeof nested === 'string' && nested.trim()) {
      return nested;
    }
  }

  if (typeof payload.message === 'string' && payload.message.trim()) {
    return payload.message;
  }

  if (typeof payload.detail === 'string' && payload.detail.trim()) {
    return payload.detail;
  }

  return fallback;
}

type ApiRequestError = Error & {
  status?: number;
  payload?: unknown;
};

function createApiRequestError(message: string, status?: number, payload?: unknown): ApiRequestError {
  const error = new Error(message) as ApiRequestError;
  if (typeof status === 'number') {
    error.status = status;
  }
  if (typeof payload !== 'undefined') {
    error.payload = payload;
  }
  return error;
}

function extractListPayload<T = unknown>(responseBody: unknown): T[] {
  if (Array.isArray(responseBody)) return responseBody;
  if (Array.isArray(responseBody?.data)) return responseBody.data;
  if (Array.isArray(responseBody?.results)) return responseBody.results;
  if (Array.isArray(responseBody?.data?.results)) return responseBody.data.results;
  return [];
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function toPositiveInteger(value: unknown, fallback = 1): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 1) {
    return fallback;
  }
  return Math.floor(parsed);
}

function mergeListPayloadShape(originalPayload: unknown, mergedItems: unknown[]): unknown {
  if (Array.isArray(originalPayload)) {
    return mergedItems;
  }

  const root = asRecord(originalPayload);

  if (Array.isArray(root.data)) {
    return {
      ...root,
      data: mergedItems,
    };
  }

  if (Array.isArray(root.results)) {
    return {
      ...root,
      results: mergedItems,
      count: mergedItems.length,
    };
  }

  const data = asRecord(root.data);
  if (Array.isArray(data.results)) {
    return {
      ...root,
      data: {
        ...data,
        results: mergedItems,
        count: mergedItems.length,
      },
    };
  }

  return mergedItems;
}

export interface PlatformSummaryAnalytics {
  healthcare_registered_count: number;
  healthcare_pending_count: number;
  staff_system_count: number;
  ml_count: number;
  healthcare_admin_count: number;
  others_count: number;
  healthcare_verified_count: number;
  healthcare_pending_verification_count: number;
  pending_registration_requests_count: number;
  total_users_count: number;
  active_users_count: number;
  inactive_users_count: number;
  pending_staff_invitations_count: number;
  generated_at: string;
}

const PLATFORM_SUMMARY_NUMERIC_FIELDS: Array<keyof Omit<PlatformSummaryAnalytics, 'generated_at'>> = [
  'healthcare_registered_count',
  'healthcare_pending_count',
  'staff_system_count',
  'ml_count',
  'healthcare_admin_count',
  'others_count',
  'healthcare_verified_count',
  'healthcare_pending_verification_count',
  'pending_registration_requests_count',
  'total_users_count',
  'active_users_count',
  'inactive_users_count',
  'pending_staff_invitations_count',
];

function toAnalyticsCount(value: unknown): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return 0;
  }
  return Math.max(0, Math.floor(parsed));
}

function normalizePlatformSummaryPayload(payload: unknown): PlatformSummaryAnalytics | null {
  const root = asRecord(payload);
  const nested = asRecord(root.data);
  const source = Object.keys(nested).length > 0 ? nested : root;

  const hasKnownField = PLATFORM_SUMMARY_NUMERIC_FIELDS.some((field) =>
    Object.prototype.hasOwnProperty.call(source, field),
  );

  if (!hasKnownField) {
    return null;
  }

  const result = {
    healthcare_registered_count: 0,
    healthcare_pending_count: 0,
    staff_system_count: 0,
    ml_count: 0,
    healthcare_admin_count: 0,
    others_count: 0,
    healthcare_verified_count: 0,
    healthcare_pending_verification_count: 0,
    pending_registration_requests_count: 0,
    total_users_count: 0,
    active_users_count: 0,
    inactive_users_count: 0,
    pending_staff_invitations_count: 0,
    generated_at: '',
  } satisfies PlatformSummaryAnalytics;

  PLATFORM_SUMMARY_NUMERIC_FIELDS.forEach((field) => {
    result[field] = toAnalyticsCount(source[field]);
  });

  result.generated_at = typeof source.generated_at === 'string' ? source.generated_at : '';

  return result;
}

// Generic API request function
async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE_URL}${path}`;

  const accessToken = getStoredAccessToken();
  const isMultipart = typeof FormData !== 'undefined' && options.body instanceof FormData;

  const defaultHeaders: Record<string, string> = isMultipart
    ? {}
    : { 'Content-Type': 'application/json' };

  if (accessToken) {
    defaultHeaders['Authorization'] = `Bearer ${accessToken}`;
  }

  const config: RequestInit = {
    ...options,
    headers: {
      ...defaultHeaders,
      ...(options.headers as Record<string, string> | undefined),
    },
  };

  try {
    const response = await fetchWithTimeout(url, config);

    if (!response.ok) {
      if (response.status === 401 && hasStoredRefreshToken()) {
        const authService = (await import('./authService')).default;
        try {
          await authService.refreshAccessToken();
          const newToken = getStoredAccessToken();
          if (newToken) {
            defaultHeaders['Authorization'] = `Bearer ${newToken}`;
            const retryResponse = await fetchWithTimeout(url, {
              ...config,
              headers: {
                ...defaultHeaders,
                ...(options.headers as Record<string, string> | undefined),
              },
            });
            if (retryResponse.ok) {
              return (await parseResponseBody(retryResponse)) as T;
            }
          }
        } catch {
          window.location.href = '/login';
          throw new Error('Session expired. Please login again.');
        }
      }

      const errorBody = await parseResponseBody(response);
      throw createApiRequestError(
        extractErrorMessage(errorBody, `API Error: ${response.status} ${response.statusText}`),
        response.status,
        errorBody,
      );
    }

    const body = await parseResponseBody(response);

    if (body && typeof body === 'object' && body.success === false) {
      throw createApiRequestError(extractErrorMessage(body, 'API request failed'), response.status, body);
    }

    // Keep return shape compatible with existing callers (envelope or direct payload).
    return (body ?? ({} as T)) as T;
  } catch (error: unknown) {
    if (error?.name === 'AbortError') {
      throw new Error(`API request timed out after ${Math.round(API_TIMEOUT_MS / 1000)}s`);
    }
    console.error('API Request failed:', error);
    throw error;
  }
}

async function apiRequestWithStatus<T>(
  path: string,
  options: RequestInit = {},
): Promise<{ status: number; data: T }> {
  const url = `${API_BASE_URL}${path}`;

  const accessToken = getStoredAccessToken();
  const isMultipart = typeof FormData !== 'undefined' && options.body instanceof FormData;

  const defaultHeaders: Record<string, string> = isMultipart
    ? {}
    : { 'Content-Type': 'application/json' };

  if (accessToken) {
    defaultHeaders['Authorization'] = `Bearer ${accessToken}`;
  }

  const config: RequestInit = {
    ...options,
    headers: {
      ...defaultHeaders,
      ...(options.headers as Record<string, string> | undefined),
    },
  };

  try {
    const response = await fetchWithTimeout(url, config);

    if (!response.ok) {
      if (response.status === 401 && hasStoredRefreshToken()) {
        const authService = (await import('./authService')).default;
        try {
          await authService.refreshAccessToken();
          const newToken = getStoredAccessToken();
          if (newToken) {
            defaultHeaders['Authorization'] = `Bearer ${newToken}`;
            const retryResponse = await fetchWithTimeout(url, {
              ...config,
              headers: {
                ...defaultHeaders,
                ...(options.headers as Record<string, string> | undefined),
              },
            });
            const retryBody = await parseResponseBody(retryResponse);

            if (!retryResponse.ok) {
              throw createApiRequestError(
                extractErrorMessage(
                  retryBody,
                  `API Error: ${retryResponse.status} ${retryResponse.statusText}`,
                ),
                retryResponse.status,
                retryBody,
              );
            }

            if (retryBody && typeof retryBody === 'object' && retryBody.success === false) {
              throw createApiRequestError(
                extractErrorMessage(retryBody, 'API request failed'),
                retryResponse.status,
                retryBody,
              );
            }

            return {
              status: retryResponse.status,
              data: (retryBody ?? ({} as T)) as T,
            };
          }
        } catch {
          window.location.href = '/login';
          throw new Error('Session expired. Please login again.');
        }
      }

      const errorBody = await parseResponseBody(response);
      throw createApiRequestError(
        extractErrorMessage(errorBody, `API Error: ${response.status} ${response.statusText}`),
        response.status,
        errorBody,
      );
    }

    const body = await parseResponseBody(response);

    if (body && typeof body === 'object' && body.success === false) {
      throw createApiRequestError(extractErrorMessage(body, 'API request failed'), response.status, body);
    }

    return {
      status: response.status,
      data: (body ?? ({} as T)) as T,
    };
  } catch (error: unknown) {
    if (error?.name === 'AbortError') {
      throw new Error(`API request timed out after ${Math.round(API_TIMEOUT_MS / 1000)}s`);
    }
    console.error('API Request failed:', error);
    throw error;
  }
}

// ─── Hospitals ────────────────────────────────────────────────────────────────

export const hospitalsApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/hospitals/${qs}`);
  },
  getById: (id: string) => apiRequest<unknown>(`/api/v1/hospitals/${id}/`),
  getMyHospital: () => apiRequest<unknown>('/api/v1/hospitals/my-hospital/'),
  updateMyHospital: (data: unknown) => {
    const isMultipart = typeof FormData !== 'undefined' && data instanceof FormData;
    return apiRequest<unknown>('/api/v1/hospitals/my-hospital/', {
      method: 'PATCH',
      body: isMultipart ? data : JSON.stringify(data),
      headers: isMultipart ? {} : undefined,
    });
  },
  uploadMyHospitalLogo: (file: File) => {
    const formData = new FormData();
    formData.append('logo', file);
    return apiRequest<unknown>('/api/v1/hospitals/my-hospital/', {
      method: 'PATCH',
      body: formData,
      headers: {},
    });
  },
  create: (data: unknown) =>
    apiRequest<unknown>('/api/v1/hospitals/', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: unknown) => {
    const isMultipart = typeof FormData !== 'undefined' && data instanceof FormData;
    return apiRequest<unknown>(`/api/v1/hospitals/${id}/`, {
      method: 'PATCH',
      body: isMultipart ? data : JSON.stringify(data),
      headers: isMultipart ? {} : undefined,
    });
  },
  verify: (id: string) =>
    apiRequest<unknown>(`/api/v1/hospitals/${id}/verify/`, { method: 'POST' }),
  suspend: (id: string) =>
    apiRequest<unknown>(`/api/v1/hospitals/${id}/suspend/`, { method: 'POST' }),
  adminOffboard: (id: string, payload?: { reason?: string; admin_notes?: string }) =>
    apiRequest<unknown>(`/api/v1/hospitals/${id}/admin-offboard/`, {
      method: 'POST',
      body: JSON.stringify(payload ?? {}),
    }),
  sendOffboardingReviewEmail: (
    id: string,
    payload: { recipient_email?: string; subject: string; message: string },
  ) =>
    apiRequest<unknown>(`/api/v1/hospitals/${id}/admin-offboard/send-review-email/`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getCapacity: (id: string) => apiRequest<unknown>(`/api/v1/hospitals/${id}/capacity/`),
  getStaff: (id: string, params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/hospitals/${id}/staff/${qs}`);
  },
  updateCapacity: (id: string, data: unknown) =>
    apiRequest<unknown>(`/api/v1/hospitals/${id}/capacity/`, { method: 'PUT', body: JSON.stringify(data) }),
  submitOffboardingRequest: (id: string, reason: string) =>
    apiRequest<unknown>(`/api/v1/hospitals/${id}/offboarding-request/`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
};

// ─── Hospital Registration (Two-Step Onboarding) ──────────────────────────────

export const hospitalRegistrationApi = {
  submit: (data: unknown) => {
    if (data instanceof FormData) {
      return apiRequest<unknown>('/api/v1/hospital-registration/', {
        method: 'POST',
        body: data,
        headers: {},
      });
    }
    return apiRequest<unknown>('/api/v1/hospital-registration/', { method: 'POST', body: JSON.stringify(data) });
  },
  listAdminRegistrations: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/admin/hospital-registrations/${qs}`);
  },
  getAdminRegistration: (id: string) =>
    apiRequest<unknown>(`/api/v1/admin/hospital-registrations/${id}/`),
  approve: (id: string, notes?: string) =>
    apiRequest<unknown>(`/api/v1/admin/hospital-registrations/${id}/approve/`, {
      method: 'POST',
      body: JSON.stringify(notes ? { notes } : {}),
    }),
  reject: (id: string, rejection_reason: string) =>
    apiRequest<unknown>(`/api/v1/admin/hospital-registrations/${id}/reject/`, {
      method: 'POST',
      body: JSON.stringify({ rejection_reason }),
    }),
};

// ─── Resource Catalog ─────────────────────────────────────────────────────────

export const catalogApi = {
  getTypes: () => apiRequest<unknown>('/api/v1/catalog/types/'),
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/catalog/${qs}`);
  },
  getById: (id: string, params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/catalog/${id}/${qs}`);
  },
  getMedicineInfo: (id: string, params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/catalog/${id}/medicine-info/${qs}`);
  },
  refreshMedicineInfo: (id: string, params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/catalog/${id}/medicine-info/refresh/${qs}`, { method: 'POST' });
  },
  create: (data: unknown) =>
    apiRequest<unknown>('/api/v1/catalog/', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: unknown) =>
    apiRequest<unknown>(`/api/v1/catalog/${id}/`, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: (id: string) =>
    apiRequest<unknown>(`/api/v1/catalog/${id}/`, { method: 'DELETE' }),
};

// ─── Resource Inventory ───────────────────────────────────────────────────────
// Note: inventory is READ-ONLY (created automatically when catalog items are restocked)
// Use adjust() to change quantities

export const inventoryApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/inventory/${qs}`);
  },
  getById: (id: string) => apiRequest<unknown>(`/api/v1/inventory/${id}/`),
  adjust: (id: string, data: { quantity_delta: number; notes?: string; transaction_type?: string }) =>
    apiRequest<unknown>(`/api/v1/inventory/${id}/adjust/`, { method: 'POST', body: JSON.stringify(data) }),
  getTransactions: (id: string) =>
    apiRequest<unknown>(`/api/v1/inventory/${id}/transactions/`),
  getShareVisibility: () => apiRequest<unknown>('/api/v1/inventory/share-visibility/'),
  updateShareVisibility: async (data: {
    inventory_id?: string;
    inventory_item_id?: string;
    inventory?: string;
    catalog_item?: string;
    catalog_item_id?: string;
    shared_quantity: number;
    quantity_offered?: number;
    share_record_id?: string;
    resource_share_id?: string;
  }) => {
    const attempts: Record<string, unknown>[] = [];

    // Try explicit inventory-based payload first (new endpoint contract).
    if (data.inventory_id || data.inventory_item_id || data.inventory) {
      attempts.push({
        inventory_id: data.inventory_id || data.inventory_item_id || data.inventory,
        shared_quantity: data.shared_quantity,
        share_record_id: data.share_record_id || data.resource_share_id,
      });
    }

    // Fallback to catalog_item key if backend identifies by catalog entry.
    if (data.catalog_item || data.catalog_item_id) {
      attempts.push({
        catalog_item: data.catalog_item || data.catalog_item_id,
        shared_quantity: data.shared_quantity,
        share_record_id: data.share_record_id || data.resource_share_id,
      });
    }

    // Backward-compatible fallback using resource-share naming.
    attempts.push({
      catalog_item: data.catalog_item || data.catalog_item_id,
      quantity_offered: data.quantity_offered ?? data.shared_quantity,
      share_record_id: data.share_record_id || data.resource_share_id,
    });

    let lastError: unknown;
    for (const payload of attempts) {
      try {
        return await apiRequest<unknown>('/api/v1/inventory/share-visibility/', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
      } catch (err) {
        lastError = err;
      }
    }

    throw lastError instanceof Error ? lastError : new Error('Failed to save share visibility.');
  },
  // Inventory scope is derived by backend from authenticated JWT hospital context.
  getAnalytics: async () => {
    const res = await apiRequest<unknown>('/api/v1/inventory/');
    const items: unknown[] = (res as unknown)?.data ?? (res as unknown)?.results ?? [];
    const total = items.length;
    const totalValue = items.reduce((s: number, i: unknown) => s + ((i.quantity_available ?? 0) * (i.unit_price ?? 0)), 0);
    const lowStock = items.filter((i: unknown) => (i.quantity_available ?? 0) < (i.reorder_level ?? 5));
    return {
      summary: {
        total_items: total,
        total_value: totalValue,
        low_stock_count: lowStock.length,
        out_of_stock_count: items.filter((i: unknown) => (i.quantity_available ?? 0) === 0).length,
      },
      days_of_supply: [],
      clinical_impact: [],
      expiry_risk: [],
      turnover_by_category: [],
      top_value_items: items.slice(0, 5).map((i: unknown) => ({
        name: i.catalog_item_name ?? i.name ?? '',
        value: (i.quantity_available ?? 0) * (i.unit_price ?? 0),
        quantity: i.quantity_available ?? 0,
      })),
      attention_required: lowStock.map((i: unknown) => ({
        name: i.catalog_item_name ?? i.name ?? '',
        status: 'warning',
        current: i.quantity_available ?? 0,
        required: i.reorder_level ?? 5,
      })),
    };
  },
};

// ─── Internal Sales ──────────────────────────────────────────────────────────

export const salesApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/sales/records/${qs}`);
  },
  create: (data: unknown, idempotencyKey?: string) =>
    apiRequestWithStatus<unknown>('/api/v1/sales/records/', {
      method: 'POST',
      headers: idempotencyKey
        ? {
            'Idempotency-Key': idempotencyKey,
          }
        : undefined,
      body: JSON.stringify(data),
    }),
  getById: (saleId: string) => apiRequest<unknown>(`/api/v1/sales/records/${saleId}/`),
};

export interface CsvAssistantIssue {
  row: number;
  message: string;
  recommendation: string;
}

export interface CsvAssistantResponsePayload {
  success: boolean;
  summary: string;
  issues: CsvAssistantIssue[];
}

export interface CsvChatResponseEnvelope {
  success?: boolean;
  data?: CsvAssistantResponsePayload;
  error?: unknown;
  meta?: unknown;
}

export interface CsvSessionMessageResponseEnvelope {
  success?: boolean;
  data?: {
    response?: CsvAssistantResponsePayload;
    out_of_scope?: boolean;
    is_out_of_scope?: boolean;
    reply_mode?: string;
    [key: string]: unknown;
  };
  error?: unknown;
  meta?: unknown;
}

// ─── Inventory Module (CSV / Quick Update) ──────────────────────────────────

export const inventoryModuleApi = {
  validateImport: (
    file: File,
    payload?: {
      mode?: 'MERGE' | 'REPLACE_UPLOADED_SCOPE' | 'FULL_REPLACE';
      confirm_full_replace?: boolean;
      idempotency_key?: string;
      language?: 'en' | 'bn';
    },
    idempotencyKey?: string,
  ) => {
    const resolvedIdempotencyKey =
      idempotencyKey || payload?.idempotency_key || createIdempotencyKey();

    const formData = new FormData();
    formData.append('file', file);

    if (payload?.mode) formData.append('mode', payload.mode);
    if (typeof payload?.confirm_full_replace === 'boolean') {
      formData.append('confirm_full_replace', String(payload.confirm_full_replace));
    }
    formData.append('idempotency_key', resolvedIdempotencyKey);
    if (payload?.language) formData.append('language', payload.language);

    return apiRequest<unknown>('/api/v1/inventory-module/imports/validate/', {
      method: 'POST',
      headers: {
        'Idempotency-Key': resolvedIdempotencyKey,
      },
      body: formData,
    });
  },

  chatImportErrors: (data: { file_id: string; query: string; language: 'en' | 'bn' }) =>
    apiRequest<CsvChatResponseEnvelope>('/api/csv/chat', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  createChatSession: (data: { file_id: string; language: 'en' | 'bn' }) =>
    apiRequest<unknown>('/api/csv/sessions/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  sendChatSessionMessage: (
    sessionId: string,
    data: { query: string; language: 'en' | 'bn' },
  ) =>
    apiRequest<CsvSessionMessageResponseEnvelope>(`/api/csv/sessions/${sessionId}/messages/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  getChatSessionMessages: (sessionId: string, params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/csv/sessions/${sessionId}/messages/${qs}`);
  },

  commitImport: (
    file: File,
    payload?: {
      mode?: 'MERGE' | 'REPLACE_UPLOADED_SCOPE' | 'FULL_REPLACE';
      confirm_full_replace?: boolean;
      idempotency_key?: string;
    },
    idempotencyKey?: string,
  ) => {
    const resolvedIdempotencyKey =
      idempotencyKey || payload?.idempotency_key || createIdempotencyKey();

    const formData = new FormData();
    formData.append('file', file);
    if (payload?.mode) formData.append('mode', payload.mode);
    if (typeof payload?.confirm_full_replace === 'boolean') {
      formData.append('confirm_full_replace', String(payload.confirm_full_replace));
    }
    formData.append('idempotency_key', resolvedIdempotencyKey);

    return apiRequest<unknown>('/api/v1/inventory-module/imports/commit/', {
      method: 'POST',
      headers: {
        'Idempotency-Key': resolvedIdempotencyKey,
      },
      body: formData,
    });
  },

  getImportJob: (jobId: string) =>
    apiRequest<unknown>(`/api/v1/inventory-module/imports/${jobId}/`),

  getImportJobErrors: (jobId: string, params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/inventory-module/imports/${jobId}/errors/${qs}`);
  },

  quickUpdate: (
    data: {
      catalog_item?: string;
      inventory_id?: string;
      quantity_delta: number;
      transaction_type?: string;
      notes?: string;
      expiry_date?: string;
      lot_number?: string;
      unit_price?: number;
      price_per_unit?: number;
    },
    idempotencyKey?: string,
  ) =>
    apiRequest<unknown>('/api/v1/inventory-module/quick-update/', {
      method: 'POST',
      headers: {
        'Idempotency-Key': idempotencyKey || createIdempotencyKey(),
      },
      body: JSON.stringify(data),
    }),
};

type PharmacyCsvDataset = 'sales' | 'staff' | 'movement';

const PHARMACY_CSV_IMPORT_BASE_PATH: Record<PharmacyCsvDataset, string> = {
  sales: '/api/v1/pharmacy-csv/sales/imports',
  staff: '/api/v1/pharmacy-csv/staff/imports',
  movement: '/api/v1/pharmacy-csv/movements/imports',
};

const resolvePharmacyCsvImportPath = (
  dataset: PharmacyCsvDataset,
  action: 'validate' | 'commit',
) => `${PHARMACY_CSV_IMPORT_BASE_PATH[dataset]}/${action}/`;

// ─── Pharmacy CSV (Sales / Staff / Movement) ───────────────────────────────

export const pharmacyCsvApi = {
  validateImport: (
    dataset: PharmacyCsvDataset,
    file: File,
    payload?: {
      language?: 'en' | 'bn';
      idempotency_key?: string;
    },
    idempotencyKey?: string,
  ) => {
    const resolvedIdempotencyKey =
      idempotencyKey || payload?.idempotency_key || createIdempotencyKey();

    const formData = new FormData();
    formData.append('file', file);
    formData.append('idempotency_key', resolvedIdempotencyKey);
    if (payload?.language) {
      formData.append('language', payload.language);
    }

    return apiRequest<unknown>(resolvePharmacyCsvImportPath(dataset, 'validate'), {
      method: 'POST',
      headers: {
        'Idempotency-Key': resolvedIdempotencyKey,
      },
      body: formData,
    });
  },

  commitImport: (
    dataset: PharmacyCsvDataset,
    file: File,
    payload?: {
      language?: 'en' | 'bn';
      idempotency_key?: string;
    },
    idempotencyKey?: string,
  ) => {
    const resolvedIdempotencyKey =
      idempotencyKey || payload?.idempotency_key || createIdempotencyKey();

    const formData = new FormData();
    formData.append('file', file);
    formData.append('idempotency_key', resolvedIdempotencyKey);
    if (payload?.language) {
      formData.append('language', payload.language);
    }

    return apiRequest<unknown>(resolvePharmacyCsvImportPath(dataset, 'commit'), {
      method: 'POST',
      headers: {
        'Idempotency-Key': resolvedIdempotencyKey,
      },
      body: formData,
    });
  },

  createChatSession: (data: { file_id: string; language: 'en' | 'bn' }) =>
    apiRequest<unknown>('/api/v1/pharmacy-csv/chat/sessions/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  sendChatSessionMessage: (
    sessionId: string,
    data: { query: string; language: 'en' | 'bn' },
  ) =>
    apiRequest<unknown>(`/api/v1/pharmacy-csv/chat/sessions/${sessionId}/messages/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  getChatSessionMessages: (sessionId: string, params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/pharmacy-csv/chat/sessions/${sessionId}/messages/${qs}`);
  },
};

// ─── ML Operations ───────────────────────────────────────────────────────────

export const mlApi = {
  listJobs: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/ml/jobs/${qs}`);
  },

  createJob: (data: {
    job_type: 'forecast' | 'outbreak';
    facility_id?: string;
    scope?: string;
    parameters?: Record<string, unknown>;
    priority?: string;
  }) =>
    apiRequest<unknown>('/api/v1/ml/jobs/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  getJob: (jobId: string) => apiRequest<unknown>(`/api/v1/ml/jobs/${jobId}/`),

  cancelJob: (jobId: string) =>
    apiRequest<unknown>(`/api/v1/ml/jobs/${jobId}/cancel/`, {
      method: 'POST',
    }),

  retryJob: (jobId: string) =>
    apiRequest<unknown>(`/api/v1/ml/jobs/${jobId}/retry/`, {
      method: 'POST',
    }),

  getJobEvents: (jobId: string) => apiRequest<unknown>(`/api/v1/ml/jobs/${jobId}/events/`),

  getJobForecastResults: (jobId: string) =>
    apiRequest<unknown>(`/api/v1/ml/jobs/${jobId}/results/forecast/`),

  getJobOutbreakResults: (jobId: string) =>
    apiRequest<unknown>(`/api/v1/ml/jobs/${jobId}/results/outbreak/`),

  getLatestForecast: (facilityId: string) =>
    apiRequest<unknown>(`/api/v1/ml/facilities/${facilityId}/latest-forecast/`),

  getLatestOutbreak: (facilityId: string) =>
    apiRequest<unknown>(`/api/v1/ml/facilities/${facilityId}/latest-outbreak/`),

  getRequestSuggestions: (facilityId: string) =>
    apiRequest<unknown>(`/api/v1/ml/facilities/${facilityId}/request-suggestions/`),

  updateFacilitySettings: (
    facilityId: string,
    data: {
      forecast_enabled?: boolean;
      outbreak_enabled?: boolean;
      alert_threshold?: number;
      lookback_days?: number;
      model_profile?: string;
    },
  ) =>
    apiRequest<unknown>(`/api/v1/ml/facilities/${facilityId}/settings/`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  listSchedules: () => apiRequest<unknown>('/api/v1/ml/schedules/'),

  createSchedule: (data: {
    job_type: 'forecast' | 'outbreak';
    facility_id: string;
    frequency: string;
    run_time?: string;
    cron?: string;
    scope?: string;
    parameters?: Record<string, unknown>;
    active?: boolean;
    is_active?: boolean;
  }) =>
    apiRequest<unknown>('/api/v1/ml/schedules/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  updateSchedule: (scheduleId: string, data: Record<string, unknown>) =>
    apiRequest<unknown>(`/api/v1/ml/schedules/${scheduleId}/`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  activateSchedule: (scheduleId: string) =>
    apiRequest<unknown>(`/api/v1/ml/schedules/${scheduleId}/activate/`, {
      method: 'POST',
    }),

  deactivateSchedule: (scheduleId: string) =>
    apiRequest<unknown>(`/api/v1/ml/schedules/${scheduleId}/deactivate/`, {
      method: 'POST',
    }),

  refresh: (
    facilityId: string,
    data: {
      job_type?: 'forecast' | 'outbreak';
      prediction_horizon_days?: number;
    } = {},
  ) =>
    apiRequest<unknown>(`/api/v1/ml/facilities/${facilityId}/refresh/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
};

// ─── Resource Shares ──────────────────────────────────────────────────────────

export const resourceSharesApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/resource-shares/${qs}`);
  },
  getMine: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/my-resource-shares/${qs}`);
  },
  getById: (id: string) => apiRequest<unknown>(`/api/v1/resource-shares/${id}/`),
  create: (data: unknown) =>
    // Required: hospital (UUID), catalog_item (UUID), quantity_offered (int)
    // Optional: status, notes, valid_until (datetime)
    apiRequest<unknown>('/api/v1/resource-shares/', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: unknown) =>
    apiRequest<unknown>(`/api/v1/resource-shares/${id}/`, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: (id: string) =>
    apiRequest<unknown>(`/api/v1/resource-shares/${id}/`, { method: 'DELETE' }),
};

// ─── Resource Requests ────────────────────────────────────────────────────────

export const requestsApi = {
  getAll: async (params?: Record<string, string>) => {
    const searchParams = new URLSearchParams(params || {});
    const qs = searchParams.toString();
    const firstResponse = await apiRequest<unknown>(`/api/v1/requests/${qs ? `?${qs}` : ''}`);

    // Respect explicit single-page requests so callers can opt into pagination views.
    if (searchParams.has('page') || searchParams.has('offset') || searchParams.has('cursor')) {
      return firstResponse;
    }

    const root = asRecord(firstResponse);
    const meta = asRecord(root.meta);
    const totalPages = toPositiveInteger(meta.total_pages ?? meta.totalPages, 1);

    if (totalPages <= 1) {
      return firstResponse;
    }

    const pageRequests: Promise<unknown>[] = [];
    for (let page = 2; page <= totalPages; page += 1) {
      const pageParams = new URLSearchParams(searchParams);
      pageParams.set('page', String(page));
      pageRequests.push(apiRequest<unknown>(`/api/v1/requests/?${pageParams.toString()}`));
    }

    const remainingPages = await Promise.all(pageRequests);
    const mergedItems = [
      ...extractListPayload(firstResponse),
      ...remainingPages.flatMap((pagePayload) => extractListPayload(pagePayload)),
    ];

    return mergeListPayloadShape(firstResponse, mergedItems);
  },
  getById: (id: string) => apiRequest<unknown>(`/api/v1/requests/${id}/`),
  getByIdFresh: (id: string) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/?__ts=${Date.now()}`, { cache: 'no-store' }),
  create: (data: unknown) =>
    // Required: supplying_hospital (UUID), catalog_item (UUID), quantity_requested (int)
    // Optional: priority (normal|urgent|emergency), notes, needed_by (datetime)
    apiRequest<unknown>('/api/v1/requests/', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: unknown) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/`, { method: 'PATCH', body: JSON.stringify(data) }),
  approve: (id: string, data: { decision: 'approved' | 'rejected'; quantity_approved?: number; reason?: string }) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/approve/`, { method: 'POST', body: JSON.stringify(data) }),
  reserve: (
    id: string,
    data: { requested_quantity?: number; strategy?: string } = {},
    idempotencyKey?: string
  ) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/reserve/`, {
      method: 'POST',
      headers: {
        'Idempotency-Key': idempotencyKey || createIdempotencyKey(),
      },
      body: JSON.stringify(data),
    }),
  dispatch: (
    id: string,
    payload?:
      | string
      | {
          notes?: string;
        }
  ) => {
    const body =
      typeof payload === 'string'
        ? { notes: payload }
        : {
            notes: payload?.notes || '',
          };

    return apiRequest<unknown>(`/api/v1/requests/${id}/dispatch/`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },
  transferConfirm: (
    id: string,
    data: {
      qrPayload: string;
      quantity_received: number;
      notes?: string;
    },
    idempotencyKey?: string
  ) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/transfer-confirm/`, {
      method: 'POST',
      headers: {
        'Idempotency-Key': idempotencyKey || createIdempotencyKey(),
      },
      body: JSON.stringify(data),
    }),
  initiatePayment: (
    id: string,
    data: {
      gateway: string;
      reservation_timeout_minutes?: number;
      return_url?: string;
      cancel_url?: string;
    },
    idempotencyKey?: string
  ) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/payments/initiate/`, {
      method: 'POST',
      headers: {
        'Idempotency-Key': idempotencyKey || createIdempotencyKey(),
      },
      body: JSON.stringify(data),
    }),
  confirmPayment: (
    id: string,
    data: { payment_status: string; payment_note?: string }
  ) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/confirm-payment/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  confirmGatewayPayment: (
    id: string,
    data: {
      payment_id: string;
      payment_status: string;
      provider_transaction_id?: string;
      raw_payload?: unknown;
    }
  ) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/payments/confirm/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  initiateRefund: (
    id: string,
    data: {
      cause: string;
      cause_note?: string;
      idempotency_key?: string;
      returned_quantity?: number | null;
      damaged_quantity?: number;
    }
  ) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/refunds/initiate/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  initiateReturn: (id: string, data: { reason: string }) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/refunds/initiate/`, {
      method: 'POST',
      body: JSON.stringify({
        cause: REFUND_CAUSE.REQUESTER_CANCELLATION,
        cause_note: data.reason,
      }),
    }),
  confirmRefund: (
    id: string,
    data: { payment_status: string; provider_transaction_id?: string }
  ) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/refunds/confirm/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  getRefundStatus: (id: string) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/refunds/status/`),
  expire: (limit?: number) =>
    apiRequest<unknown>('/api/v1/requests/expire/', {
      method: 'POST',
      body: JSON.stringify(limit ? { limit } : {}),
    }),
  reconcilePayments: () =>
    apiRequest<unknown>('/api/v1/requests/payments/reconcile/', { method: 'POST' }),
  getPaymentsReport: () =>
    apiRequest<unknown>('/api/v1/requests/payments/report/'),
  cancel: (id: string) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/`, {
      method: 'DELETE',
    }),
  verifyReturn: (
    id: string,
    data: {
      return_token: string;
      quantity_returned?: number | null;
      quantity_damaged?: number;
      quantity_lost?: number;
      verification_notes?: string;
    }
  ) =>
    apiRequest<unknown>(`/api/v1/requests/${id}/verify-return/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
};

// ─── Shipments ────────────────────────────────────────────────────────────────

export const shipmentsApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/shipments/${qs}`);
  },
  getById: (id: string) => apiRequest<unknown>(`/api/v1/shipments/${id}/`),
  create: (data: unknown) =>
    // Required: origin_hospital (UUID), destination_hospital (UUID)
    // Optional: carrier_name, tracking_number, estimated_delivery_at, notes, status
    apiRequest<unknown>('/api/v1/shipments/', { method: 'POST', body: JSON.stringify(data) }),
  dispatch: (id: string, data: {
    rider_name: string;
    rider_phone: string;
    vehicle_info: string;
    carrier_name?: string;
    tracking_number?: string;
    estimated_delivery_at?: string;
    notes?: string;
  }) =>
    apiRequest<unknown>(`/api/v1/shipments/${id}/`, {
      method: 'PATCH',
      body: JSON.stringify({
        status: 'dispatched',
        ...data,
      }),
    }),
  update: (id: string, data: unknown) =>
    apiRequest<unknown>(`/api/v1/shipments/${id}/`, { method: 'PATCH', body: JSON.stringify(data) }),
  addTracking: (id: string, data: { status: string; location?: string; notes?: string }) =>
    apiRequest<unknown>(`/api/v1/shipments/${id}/tracking/`, { method: 'POST', body: JSON.stringify(data) }),
  getTracking: (id: string) =>
    apiRequest<unknown>(`/api/v1/shipments/${id}/tracking/`),
  updateLocation: (id: string, data: { lat: number; lng: number; eta?: string }) =>
    apiRequest<unknown>(`/api/v1/shipments/${id}/tracking/`, {
      method: 'POST',
      body: JSON.stringify({
        status: 'in_transit',
        location: `${data.lat},${data.lng}`,
        notes: data.eta ? `ETA: ${data.eta}` : undefined,
      }),
    }),
  confirmHandover: (id: string, data: { condition?: string; notes?: string; receiver_name?: string; receiver_position?: string }) =>
    apiRequest<unknown>(`/api/v1/shipments/${id}/tracking/`, {
      method: 'POST',
      body: JSON.stringify({
        status: 'delivered',
        notes: [
          data.notes,
          data.receiver_name ? `Receiver: ${data.receiver_name}` : '',
          data.receiver_position ? `Role: ${data.receiver_position}` : '',
          data.condition ? `Condition: ${data.condition}` : '',
        ].filter(Boolean).join(' | '),
      }),
    }),
};

// ─── Staff ────────────────────────────────────────────────────────────────────

export const staffApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/staff/${qs}`);
  },
  getById: (id: string) => apiRequest<unknown>(`/api/v1/staff/${id}/`),
  create: (data: unknown) =>
    apiRequest<unknown>('/api/v1/staff/', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: unknown) =>
    apiRequest<unknown>(`/api/v1/staff/${id}/`, { method: 'PATCH', body: JSON.stringify(data) }),
  suspend: (id: string) =>
    apiRequest<unknown>(`/api/v1/staff/${id}/suspend/`, { method: 'POST' }),
};

// ─── Roles ────────────────────────────────────────────────────────────────────

export const rolesApi = {
  getAll: () => apiRequest<unknown>('/api/v1/roles/'),
  create: async () => {
    throw new Error('Role write endpoints are not available in the current backend API contract.');
  },
  update: async () => {
    throw new Error('Role write endpoints are not available in the current backend API contract.');
  },
  delete: async () => {
    throw new Error('Role write endpoints are not available in the current backend API contract.');
  },
};

export interface RolePermissionUpdateRequest {
  permission_codes?: string[];
  permissions?: string[];
  permission_ids?: string[];
}

// ─── Permissions Catalog ─────────────────────────────────────────────────────

export const permissionsApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/permissions/${qs}`, { cache: 'no-store' });
  },
  getById: (id: string) => apiRequest<unknown>(`/api/v1/permissions/${id}/`),
};

// ─── RBAC v2 ─────────────────────────────────────────────────────────────────

export const rbacApi = {
  // Platform roles
  getPlatformRoles: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/rbac/platform-roles/${qs}`);
  },
  createPlatformRole: (data: Record<string, unknown>) =>
    apiRequest<unknown>('/api/v1/rbac/platform-roles/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updatePlatformRole: (roleId: string, data: Record<string, unknown>) =>
    apiRequest<unknown>(`/api/v1/rbac/platform-roles/${roleId}/`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deletePlatformRole: (roleId: string) =>
    apiRequest<unknown>(`/api/v1/rbac/platform-roles/${roleId}/`, { method: 'DELETE' }),

  getPlatformRolePermissions: (roleId: string) =>
    apiRequest<unknown>(`/api/v1/rbac/platform-roles/${roleId}/permissions/`),
  assignPlatformRolePermissions: (roleId: string, data: RolePermissionUpdateRequest) =>
    apiRequest<unknown>(`/api/v1/rbac/platform-roles/${roleId}/permissions/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  clearPlatformRolePermissions: (roleId: string) =>
    apiRequest<unknown>(`/api/v1/rbac/platform-roles/${roleId}/permissions/`, { method: 'DELETE' }),

  // Hospital roles
  getHospitalRoles: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/rbac/hospital-roles/${qs}`);
  },
  createHospitalRole: (data: Record<string, unknown>) =>
    apiRequest<unknown>('/api/v1/rbac/hospital-roles/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateHospitalRole: (roleId: string, data: Record<string, unknown>) =>
    apiRequest<unknown>(`/api/v1/rbac/hospital-roles/${roleId}/`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteHospitalRole: (roleId: string) =>
    apiRequest<unknown>(`/api/v1/rbac/hospital-roles/${roleId}/`, { method: 'DELETE' }),

  getHospitalRolePermissions: (roleId: string) =>
    apiRequest<unknown>(`/api/v1/rbac/hospital-roles/${roleId}/permissions/`),
  assignHospitalRolePermissions: (roleId: string, data: RolePermissionUpdateRequest) =>
    apiRequest<unknown>(`/api/v1/rbac/hospital-roles/${roleId}/permissions/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  clearHospitalRolePermissions: (roleId: string) =>
    apiRequest<unknown>(`/api/v1/rbac/hospital-roles/${roleId}/permissions/`, { method: 'DELETE' }),

  // User role assignments
  getUserPlatformRoles: (userId: string) =>
    apiRequest<unknown>(`/api/v1/rbac/users/${userId}/platform-roles/`),
  assignUserPlatformRole: (userId: string, data: Record<string, unknown>) =>
    apiRequest<unknown>(`/api/v1/rbac/users/${userId}/platform-roles/`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  removeUserPlatformRole: (userId: string, assignmentId: string) =>
    apiRequest<unknown>(`/api/v1/rbac/users/${userId}/platform-roles/${assignmentId}/`, {
      method: 'DELETE',
    }),

  getUserHospitalRole: (userId: string) =>
    apiRequest<unknown>(`/api/v1/rbac/users/${userId}/hospital-role/`),
  setUserHospitalRole: (userId: string, data: Record<string, unknown>) =>
    apiRequest<unknown>(`/api/v1/rbac/users/${userId}/hospital-role/`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  removeUserHospitalRole: (userId: string) =>
    apiRequest<unknown>(`/api/v1/rbac/users/${userId}/hospital-role/`, { method: 'DELETE' }),

  // Effective permissions
  getUserEffectivePermissions: async (userId: string) => {
    try {
      return await apiRequest<unknown>(`/api/v1/rbac/users/${userId}/permissions/effective/`);
    } catch (error) {
      if ((error as ApiRequestError)?.status === 404) {
        return apiRequest<unknown>(`/api/v1/users/${userId}/permissions/effective/`);
      }
      throw error;
    }
  },
};

// ─── Invitations ─────────────────────────────────────────────────────────────

export const invitationsApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/invitations/${qs}`);
  },
  getById: (id: string) => apiRequest<unknown>(`/api/v1/invitations/${id}/`),
  send: (data: unknown) =>
    apiRequest<unknown>('/api/v1/invitations/', { method: 'POST', body: JSON.stringify(data) }),
  revoke: (id: string) =>
    apiRequest<unknown>(`/api/v1/invitations/${id}/`, { method: 'DELETE' }),
  accept: (data: { token: string; password: string }) =>
    apiRequest<unknown>('/api/v1/invitations/accept/', { method: 'POST', body: JSON.stringify(data) }),
};

// ─── Notifications ────────────────────────────────────────────────────────────

export const notificationsApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/notifications/${qs}`);
  },
  getById: (id: string) => apiRequest<unknown>(`/api/v1/notifications/${id}/`),
  markRead: (id: string) =>
    apiRequest<unknown>(`/api/v1/notifications/${id}/read/`, { method: 'POST' }),
  markAllRead: () =>
    apiRequest<unknown>('/api/v1/notifications/mark-all-read/', { method: 'POST' }),
};

// ─── Broadcasts ───────────────────────────────────────────────────────────────

export const broadcastsApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/broadcasts/${qs}`).catch(() =>
      apiRequest<unknown>(`/api/v1/emergency-broadcasts/${qs}`)
    );
  },
  getUnreadCount: () =>
    apiRequest<unknown>('/api/v1/broadcasts/unread-count/').catch(() =>
      apiRequest<unknown>('/api/v1/emergency-broadcasts/unread-count/')
    ),
  getById: (id: string) =>
    apiRequest<unknown>(`/api/v1/broadcasts/${id}/`).catch(() =>
      apiRequest<unknown>(`/api/v1/emergency-broadcasts/${id}/`)
    ),
  create: async (data: unknown) => {
    // Required: title, message
    // Optional: scope ('all'|'hospitals'), priority ('normal'|'urgent'|'emergency'), allow_response (bool), target_hospitals (UUID[]), location ({ lat, lng, address })
    const source = asRecord(data);

    const normalizedPayload: Record<string, unknown> = { ...source };
    if (source.allowResponse !== undefined && source.allow_response === undefined) {
      normalizedPayload.allow_response = source.allowResponse;
    }
    if (source.targetHospitals !== undefined && source.target_hospitals === undefined) {
      normalizedPayload.target_hospitals = source.targetHospitals;
    }

    if (source.location !== undefined) {
      const normalizedLocation: Record<string, unknown> = {};

      if (typeof source.location === 'string') {
        const trimmedAddress = source.location.trim();
        if (trimmedAddress) {
          normalizedLocation.address = trimmedAddress;
        }
      } else {
        const locationRecord = asRecord(source.location);

        const latRaw = Number(locationRecord.lat ?? locationRecord.latitude);
        const lngRaw = Number(locationRecord.lng ?? locationRecord.lon ?? locationRecord.longitude);
        const hasValidLat = Number.isFinite(latRaw) && latRaw >= -90 && latRaw <= 90;
        const hasValidLng = Number.isFinite(lngRaw) && lngRaw >= -180 && lngRaw <= 180;

        if (hasValidLat && hasValidLng) {
          normalizedLocation.lat = Number(latRaw.toFixed(6));
          normalizedLocation.lng = Number(lngRaw.toFixed(6));
        }

        const rawAddress = locationRecord.address;
        if (typeof rawAddress === 'string' && rawAddress.trim()) {
          normalizedLocation.address = rawAddress.trim();
        }
      }

      if (Object.keys(normalizedLocation).length > 0) {
        normalizedPayload.location = normalizedLocation;
      } else {
        delete normalizedPayload.location;
      }
    }

    const fallbackPayload: Record<string, unknown> = { ...normalizedPayload };
    delete fallbackPayload.send_email;
    delete fallbackPayload.notify_recipients;

    const fallbackWithoutLocationPayload: Record<string, unknown> = { ...fallbackPayload };
    delete fallbackWithoutLocationPayload.location;

    const hasLocationPayload = Object.prototype.hasOwnProperty.call(normalizedPayload, 'location');

    const attempts: Array<{ path: string; payload: Record<string, unknown> }> = [
      { path: '/api/v1/broadcasts/', payload: normalizedPayload },
      { path: '/api/v1/broadcasts/', payload: fallbackPayload },
      ...(hasLocationPayload
        ? [{ path: '/api/v1/broadcasts/', payload: fallbackWithoutLocationPayload }]
        : []),
      { path: '/api/v1/emergency-broadcasts/', payload: normalizedPayload },
      { path: '/api/v1/emergency-broadcasts/', payload: fallbackPayload },
      ...(hasLocationPayload
        ? [{ path: '/api/v1/emergency-broadcasts/', payload: fallbackWithoutLocationPayload }]
        : []),
    ];

    const seen = new Set<string>();
    let lastError: unknown = null;

    for (const attempt of attempts) {
      const key = `${attempt.path}|${JSON.stringify(attempt.payload)}`;
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);

      try {
        return await apiRequest<unknown>(attempt.path, {
          method: 'POST',
          body: JSON.stringify(attempt.payload),
        });
      } catch (error) {
        lastError = error;
      }
    }

    throw lastError ?? new Error('Failed to create broadcast');
  },
  close: (id: string) =>
    apiRequest<unknown>(`/api/v1/broadcasts/${id}/close/`, { method: 'POST' }).catch(() =>
      apiRequest<unknown>(`/api/v1/emergency-broadcasts/${id}/close/`, { method: 'POST' })
    ),
  delete: (id: string) =>
    apiRequest<unknown>(`/api/v1/broadcasts/${id}/`, { method: 'DELETE' }).catch(() =>
      apiRequest<unknown>(`/api/v1/emergency-broadcasts/${id}/`, { method: 'DELETE' })
    ),
  respond: (id: string, data: { response: string }) =>
    apiRequest<unknown>(`/api/v1/broadcasts/${id}/respond/`, { method: 'POST', body: JSON.stringify(data) }).catch(() =>
      apiRequest<unknown>(`/api/v1/emergency-broadcasts/${id}/respond/`, { method: 'POST', body: JSON.stringify(data) })
    ),
  markRead: (id: string) =>
    apiRequest<unknown>(`/api/v1/broadcasts/${id}/read/`, { method: 'POST' }).catch(() =>
      apiRequest<unknown>(`/api/v1/emergency-broadcasts/${id}/read/`, { method: 'POST' })
    ),
  getResponses: (id: string) =>
    apiRequest<unknown>(`/api/v1/broadcasts/${id}/responses/`).catch(() =>
      apiRequest<unknown>(`/api/v1/emergency-broadcasts/${id}/responses/`)
    ),
};

// ─── Role-Scoped Badges ──────────────────────────────────────────────────────

export const badgesApi = {
  getHealthcareBadges: (signal?: AbortSignal) =>
    apiRequest<unknown>('/api/v1/healthcare/badges', { signal }),
  getPlatformBadges: (signal?: AbortSignal) =>
    apiRequest<unknown>('/api/v1/platform/badges', { signal }),
  acknowledgeHealthcareBadges: () =>
    apiRequest<unknown>('/api/v1/healthcare/badges/acknowledge', { method: 'POST' }),
};

// ─── Analytics ────────────────────────────────────────────────────────────────

const fetchPlatformSummary = async (): Promise<PlatformSummaryAnalytics | null> => {
  const response = await apiRequest<unknown>('/api/v1/analytics/platform-summary/');
  return normalizePlatformSummaryPayload(response);
};

export const analyticsApi = {
  get: async () => {
    const data = await fetchPlatformSummary();
    return {
      success: true,
      data,
    };
  },
  getPlatformSummary: fetchPlatformSummary,
};

// ─── Integrations ─────────────────────────────────────────────────────────────
// HospitalAPIConfig: per-resource external API integration config for a hospital

export const integrationsApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/integrations/${qs}`);
  },
  getById: (id: string) => apiRequest<unknown>(`/api/v1/integrations/${id}/`),
  create: (data: unknown) =>
    // Required: resource_type (UUID), integration_type ('api'|'manual'|'csv_upload'), api_endpoint
    // Optional: http_method ('GET'|'POST'|'PUT'), auth_type ('bearer'|'basic'|'api_key'|'none'),
    //           api_token (raw, will be encrypted), headers (JSON), sync_frequency (int), is_active
    apiRequest<unknown>('/api/v1/integrations/', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: unknown) =>
    apiRequest<unknown>(`/api/v1/integrations/${id}/`, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: (id: string) =>
    apiRequest<unknown>(`/api/v1/integrations/${id}/`, { method: 'DELETE' }),
  sync: (id: string) =>
    apiRequest<unknown>(`/api/v1/integrations/${id}/sync/`, { method: 'POST' }),
};

// ─── Audit Logs ────────────────────────────────────────────────────────────────

export const auditApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/audit-logs/${qs}`);
  },
};

// ─── Conversations & Messaging ────────────────────────────────────────────────

export const conversationsApi = {
  openDirectConversation: (participantId: string) =>
    apiRequest<unknown>('/api/v1/chat/direct-conversations/open/', {
      method: 'POST',
      body: JSON.stringify({ participant_id: participantId }),
    }),
  getGlobalUnreadCount: () => apiRequest<unknown>('/api/v1/chat/unread-count/'),
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/conversations/${qs}`);
  },
  getById: (id: string) =>
    apiRequest<unknown>(`/api/v1/conversations/${id}/`),
  create: (data: unknown) => {
    // Backend expects participant_ids (array of user UUIDs), subject, resource_request (optional)
    const payload: unknown = { subject: data.subject || '' };
    if (data.participant_ids) payload.participant_ids = data.participant_ids;
    if (data.participants) payload.participant_ids = data.participants;
    if (data.resource_request) payload.resource_request = data.resource_request;
    return apiRequest<unknown>('/api/v1/conversations/', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  getMessages: (id: string, params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/chat/conversations/${id}/messages/${qs}`).catch(() =>
      apiRequest<unknown>(`/api/v1/conversations/${id}/messages/${qs}`)
    );
  },
  syncMessages: (id: string, after: string) => {
    const query = `?after=${encodeURIComponent(after)}`;
    return apiRequest<unknown>(`/api/v1/chat/conversations/${id}/messages/sync/${query}`).catch(() =>
      apiRequest<unknown>(`/api/v1/conversations/${id}/messages/sync/${query}`)
    );
  },
  sendMessage: (id: string, data: unknown) => {
    const input = asRecord(data);
    const payload: Record<string, unknown> = {
      body: String(input.body ?? input.content ?? input.message ?? '').trim(),
    };

    if (Array.isArray(input.mentions) && input.mentions.length > 0) {
      payload.mentions = input.mentions;
    }

    return apiRequest<unknown>(`/api/v1/conversations/${id}/messages/`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }).catch(() =>
      apiRequest<unknown>(`/api/v1/chat/conversations/${id}/messages/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    );
  },
  uploadAttachment: (
    id: string,
    file: File,
    options?: {
      body?: string;
      mediaKind?: 'image' | 'file' | 'voice' | 'video';
    },
  ) => {
    const formData = new FormData();
    formData.append('file', file);
    if (options?.body) {
      formData.append('body', options.body);
    }
    if (options?.mediaKind) {
      formData.append('media_kind', options.mediaKind);
    }
    return apiRequest<unknown>(`/api/v1/chat/conversations/${id}/attachments/`, {
      method: 'POST',
      body: formData,
      headers: {},
    });
  },
  deleteMessage: (conversationId: string, messageId: string, deleteForEveryone = false) =>
    apiRequest<unknown>(`/api/v1/chat/conversations/${conversationId}/messages/delete/`, {
      method: 'POST',
      body: JSON.stringify({
        message_id: messageId,
        delete_for_everyone: deleteForEveryone,
      }),
    }),
  deleteConversation: (conversationId: string) =>
    apiRequest<unknown>(`/api/v1/chat/conversations/${conversationId}/delete/`, {
      method: 'POST',
    }),
  markRead: (
    id: string,
    data?: {
      last_read_message_id?: string;
      message_id?: string;
      last_read_at?: string;
    },
  ) => {
    const payload: Record<string, unknown> = {};

    if (data?.last_read_message_id) {
      payload.last_read_message_id = data.last_read_message_id;
    } else if (data?.message_id) {
      payload.last_read_message_id = data.message_id;
    }

    if (data?.last_read_at) {
      payload.last_read_at = data.last_read_at;
    }

    return apiRequest<unknown>(`/api/v1/conversations/${id}/read/`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }).catch(() =>
      apiRequest<unknown>(`/api/v1/chat/conversations/${id}/read/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    );
  },
};

export const templatesApi = {
  getAll: () => apiRequest<unknown>('/api/v1/templates/'),
  getById: (id: string) => apiRequest<unknown>(`/api/v1/templates/${id}/`),
  create: (data: unknown) =>
    apiRequest<unknown>('/api/v1/templates/', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: unknown) =>
    apiRequest<unknown>(`/api/v1/templates/${id}/`, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: (id: string) =>
    apiRequest<unknown>(`/api/v1/templates/${id}/`, { method: 'DELETE' }),
};

// ─── Offboarding ──────────────────────────────────────────────────────────────

export const offboardingApi = {
  listAdminRequests: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/admin/hospital-offboarding-requests/${qs}`);
  },
  getById: (id: string) => apiRequest<unknown>(`/api/v1/admin/hospital-offboarding-requests/${id}/`),
  sendReviewEmail: (
    id: string,
    payload: { recipient_email?: string; subject: string; message: string },
  ) =>
    apiRequest<unknown>(`/api/v1/admin/hospital-offboarding-requests/${id}/send-review-email/`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  approve: (id: string, admin_notes?: string) =>
    apiRequest<unknown>(`/api/v1/admin/hospital-offboarding-requests/${id}/approve/`, {
      method: 'POST',
      body: JSON.stringify(admin_notes ? { admin_notes } : {}),
    }),
  reject: (id: string, admin_notes?: string) =>
    apiRequest<unknown>(`/api/v1/admin/hospital-offboarding-requests/${id}/reject/`, {
      method: 'POST',
      body: JSON.stringify(admin_notes ? { admin_notes } : {}),
    }),
};

export const hospitalUpdateRequestsApi = {
  getAll: (params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params)}` : '';
    return apiRequest<unknown>(`/api/v1/admin/hospital-update-requests/${qs}`);
  },
  getById: (id: string) => apiRequest<unknown>(`/api/v1/admin/hospital-update-requests/${id}/`),
  sendReviewEmail: (
    id: string,
    payload: { recipient_email?: string; subject: string; message: string },
  ) =>
    apiRequest<unknown>(`/api/v1/admin/hospital-update-requests/${id}/send-review-email/`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  approve: (id: string, admin_notes?: string) =>
    apiRequest<unknown>(`/api/v1/admin/hospital-update-requests/${id}/approve/`, {
      method: 'POST',
      body: JSON.stringify(admin_notes ? { admin_notes } : {}),
    }),
  reject: (id: string, rejection_reason?: string) =>
    apiRequest<unknown>(`/api/v1/admin/hospital-update-requests/${id}/reject/`, {
      method: 'POST',
      body: JSON.stringify(rejection_reason ? { rejection_reason } : {}),
    }),
};

// ─── Credits ──────────────────────────────────────────────────────────────────

export const creditsApi = {
  get: () => apiRequest<unknown>('/api/v1/credits/'),
  getBalance: () => apiRequest<unknown>('/api/v1/analytics/balance/'),
};

// ─── Public ───────────────────────────────────────────────────────────────────

export const publicApi = {
  health: () => apiRequest<unknown>('/api/health/'),
  info: () => apiRequest<unknown>('/api/v1/public/'),
};

export default {
  hospitals: hospitalsApi,
  hospitalRegistration: hospitalRegistrationApi,
  catalog: catalogApi,
  inventory: inventoryApi,
  sales: salesApi,
  inventoryModule: inventoryModuleApi,
  pharmacyCsv: pharmacyCsvApi,
  resourceShares: resourceSharesApi,
  requests: requestsApi,
  shipments: shipmentsApi,
  staff: staffApi,
  roles: rolesApi,
  permissions: permissionsApi,
  rbac: rbacApi,
  invitations: invitationsApi,
  notifications: notificationsApi,
  broadcasts: broadcastsApi,
  badges: badgesApi,
  analytics: analyticsApi,
  credits: creditsApi,
  conversations: conversationsApi,
  templates: templatesApi,
  offboarding: offboardingApi,
  hospitalUpdateRequests: hospitalUpdateRequestsApi,
  integrations: integrationsApi,
  audit: auditApi,
  ml: mlApi,
  public: publicApi,
};
