## **Project Title**

**HealthSync: Inter-Healthcare Resource Sharing Platform**

---

## **Project Objective**

To design and build a web-based coordination and intelligence platform that improves the way healthcares:

* Predict shortages and wastage
* Share critical drugs, blood units, and organs
* Coordinate during emergencies

The platform works alongside existing Healthcare Management Systems — not as a replacement — and focuses on visibility, communication, and decision support.It also supports small pharmacies by providing them an inventory management interface.

---

## RBAC Authorization Layer

The backend uses dual-scope RBAC with:

- Permission catalog table:
   - `staff_permission`
- Platform-scope RBAC tables:
   - `platform_role`
   - `platform_role_permission`
   - `user_platform_role`
- Healthcare-scope RBAC tables:
   - `hospital_role`
   - `hospital_role_permission`
   - `user_hospital_role`
- Authorization audit table:
   - `audit_log` (`user_id`, `action`, `resource`, `hospital_id`, `timestamp`, `metadata`)

Seed dual-scope permissions and platform roles:

```bash
docker compose exec backend python manage.py seed_dual_scope_rbac
```

Run hard cutover to dual-scope role assignments and purge legacy role rows:

```bash
docker compose exec backend python manage.py cutover_dual_scope_rbac
```

Dual-scope API base path:

- `GET|POST /api/v1/rbac/platform-roles/`
- `GET|POST /api/v1/rbac/hospital-roles/`
- `GET|POST /api/v1/rbac/users/{user_pk}/platform-roles/`
- `GET|PUT|DELETE /api/v1/rbac/users/{user_pk}/hospital-role/`
- `GET /api/v1/rbac/users/{user_pk}/permissions/effective/`

Detailed design and extension guide:

- `integration_docs/rbac_authorization.md`

---

## Real-Time Chat (WebSocket + REST)

The backend now includes an isolated `chat` module implemented with Django Channels and Redis channel layers.

WebSocket endpoint:

- `ws://localhost:8000/ws/chat/{conversation_id}/?token=<jwt_access_token>`
- `ws://localhost:8000/ws/broadcasts/?token=<jwt_access_token>`

WebSocket role capability matrix:

| Role | Broadcast WS | Chat WS |
| --- | --- | --- |
| `SUPER_ADMIN` / `SYSTEM_ADMIN` | allowed | denied |
| `ML_ENGINEER` | denied | denied |
| Other authenticated roles | allowed | existing participant-based behavior |

Backend enforcement notes:

- Role checks are enforced server-side during websocket handshake and channel group join.
- Chat unread websocket publish routing skips role-ineligible recipients.
- Broadcast websocket publish routing skips role-ineligible recipients.

REST endpoints:

- `GET /api/v1/chat/unread-count/`
- `POST /api/v1/chat/direct-conversations/open/`
- `GET /api/v1/chat/direct-conversations/`
- `GET /api/v1/chat/conversations/{conversation_id}/messages/`
- `GET /api/v1/chat/conversations/{conversation_id}/messages/sync/?after=<message_id>`
- `GET /api/v1/chat/conversations/{conversation_id}/unread-count/`
- `POST /api/v1/chat/conversations/{conversation_id}/read/`
- `POST /api/v1/chat/conversations/{conversation_id}/attachments/`
- `POST /api/v1/chat/conversations/{conversation_id}/messages/delete/`
- `POST /api/v1/chat/conversations/{conversation_id}/delete/`
- `GET /api/v1/chat/conversations/{conversation_id}/audit-events/`
- `GET /api/v1/chat/conversations/{conversation_id}/export/?export_format=json|csv&include_audit=true|false`

Group conversation management endpoints (communications API):

- `GET /api/v1/conversations/` (includes `unread_count` per conversation)
- `POST /api/v1/conversations/`
- `POST /api/v1/conversations/{conversation_id}/participants/add/`
- `POST /api/v1/conversations/{conversation_id}/participants/remove/`
- `POST /api/v1/conversations/{conversation_id}/read/`
- `GET /api/v1/conversations/{conversation_id}/unread-count/`

Broadcast unread endpoint:

- `GET /api/v1/broadcasts/unread-count/` (returns `total_unread` and `unread_count` alias)

Broadcast creation authorization:

- `POST /api/v1/broadcasts/` allows `HEALTHCARE_ADMIN`, `SUPER_ADMIN`/`PLATFORM_ADMIN`, or any hospital role granted `hospital:broadcast.manage`.

Supported websocket client events:

- `message.send`
- `typing.start`
- `typing.stop`
- `message.read`

`message.read` payload:

- preferred: `{ "type": "message.read", "last_read_message_id": "<uuid>" }`
- backward-compatible alias: `message_id`

Unread calculations use participant read pointers (`last_read_message_id`) with timestamp fallback for legacy rows, and exclude the caller's own messages.

Unread badge semantics:

- `GET /api/v1/chat/unread-count/` returns conversation-level badge totals (`total_unread`, `direct_unread`, `group_unread`), so multiple unread messages in one conversation still count as 1 notification badge.
- Message-level totals remain available via `total_unread_messages`, `direct_unread_messages`, and `group_unread_messages`.
- Per-conversation message counts remain in `conversation_unread[].unread_count`.

Reconnect recovery flow:

- Initial load: `GET /api/v1/chat/conversations/{conversation_id}/messages/?page=1&limit=25`
- On websocket reconnect: call `GET /api/v1/chat/conversations/{conversation_id}/messages/sync/?after=<last_cached_message_id>`
- Sync response uses the same message payload shape as history and returns only missed messages in chronological order.

Server websocket events include:

- `message.created`
- `message.read`
- `typing.start`
- `typing.stop`
- `unread_count.updated`

Websocket behavior is incremental-only:

- `new_message` class -> `message.created`
- `message_status_update` class -> `message.read` and `unread_count.updated`
- `typing` class -> `typing.start` and `typing.stop`
- No full conversation snapshot is pushed on connect/reconnect; clients should use the sync REST endpoint for gap recovery.

Server message payload includes modern delivery/read fields:

- `status`: `sent|delivered|read`
- `read_by`: list of user IDs
- `attachments`: normalized metadata

Attachment types supported:

- `image`
- `file`
- `voice`
- `video`

Attachment metadata shape:

```json
{
   "id": "uuid",
   "name": "image.png",
   "type": "image",
   "url": "/media/chat/attachments/2026/03/16/image.png",
   "size": 120000,
   "media_kind": "image",
   "processing_status": "ready"
}
```

Attachment upload request (`multipart/form-data`):

- `file`: required
- `body`: optional text caption
- `media_kind`: optional hint (`image|file|voice|video`)

Message delete request supports optional global delete:

```json
{
   "message_id": "<uuid>",
   "delete_for_everyone": true
}
```

If `delete_for_everyone=true` (sender only), the message is hidden for all participants and linked files are removed from object storage.

## Chat Attachment Storage (MinIO)

Chat attachments use S3-compatible object storage (MinIO in local Docker).

Local dev MinIO endpoints:

- API: `http://localhost:9100`
- Console: `http://localhost:9101`

Required environment variables:

```env
USE_MINIO_CHAT_STORAGE=true
MINIO_ENDPOINT_URL=http://minio:9000
MINIO_PUBLIC_ENDPOINT=http://localhost:9100
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET_NAME=hrsp-chat-attachments
MINIO_AUTO_CREATE_BUCKET=true
MINIO_PUBLIC_READ=true
```

Upload workflow:

1. Client posts multipart attachment to chat upload endpoint.
2. Backend ensures MinIO bucket exists.
3. File is written to object storage and metadata row is created in `chat_message_attachment`.
4. Message history response returns attachment metadata with URL.
5. WebSocket broadcasts `message.created` including the same attachment metadata.

Video encoding pipeline:

- For large video uploads, backend marks attachment as `pending` and queues Celery task `transcode_chat_video_attachment_task`.
- Worker uses `ffmpeg` to encode MP4 (H.264 + AAC) with faststart for better streaming.
- Attachment metadata is updated (`processing_status`, `encoded_codec`, size, content type).

Encoding-related env vars:

```env
CHAT_VIDEO_TRANSCODE_ENABLED=true
CHAT_VIDEO_TRANSCODE_THRESHOLD_BYTES=12582912
CHAT_FFMPEG_BINARY=ffmpeg
CHAT_VIDEO_CRF=28
```

The chat module reuses existing `Conversation`, `ConversationParticipant`, and `Message` models from the communications domain, adds canonical direct-thread mapping in `apps.chat.DirectConversation`, encrypts message bodies at rest, stores per-user visibility states for privacy-preserving deletion, and stores metadata-only chat audit events.

Environment:
- `CHAT_MESSAGE_ENCRYPTION_KEY` should be set to a Fernet-compatible key in production.

---

**Access the application**
   - Backend API: http://localhost:8000
   - Backend Health Check: http://localhost:8000/api/health/
   - Swagger UI: http://localhost:8000/api/v1/docs/
   - Django Admin: http://localhost:8000/admin/

---

## Real ML E2E Workflow (Docker)

Run the full real ML lifecycle hardening check (inference + training + activation + rollback) with one command:

```bash
docker compose run --rm e2e-ml-workflow
```

What this run validates:
- model1 and model2 inference via `/api/v1/ml/model1/predict/` and `/api/v1/ml/model2/predict/`
- training dataset generation + approval + dispatch to real Server B
- training callback persistence and model version creation in Server A
- review/approve/activate workflow for new model versions
- rollback to target version and post-rollback inference verification
- snapshot mirror integrity checks between Server A and Server B MinIO

Generated report:
- default path: `/app/logs/real_ml_e2e_report.json`
- configurable via `ML_E2E_REPORT_PATH`

Optional credentials override for the runner:

```env
ML_E2E_EMAIL=admin@medibridge.com
ML_E2E_PASSWORD=Admin@1234
```

---

## Email Configuration (SMTP)

The backend sends password reset and invitation emails using SMTP credentials from environment variables.

Set these variables in your `.env` (or deployment environment):

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=example@gmail.com
EMAIL_HOST_PASSWORD=app_password_here
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=Hospital Platform <example@gmail.com>
FRONTEND_URL=http://localhost:8080
BACKEND_PORT=8000
```

Email workflows:
- `POST /api/auth/password-reset` sends reset email with `{{FRONTEND_URL}}/reset-password?token=<token>`.
- `GET /api/auth/reset-password/validate?token=<token>` validates token status.
- `POST /api/auth/reset-password` consumes token and sets password with body `{ "token": "...", "newPassword": "..." }`.
- `POST /api/v1/staff/` sends set-password invitation with `{{FRONTEND_URL}}/set-password?token=<token>`.
- `POST /api/v1/invitations/` sends invitation with `{{FRONTEND_URL}}/accept-invitation?token=<token>`.
- `POST /api/v1/admin/hospital-registrations/{id}/approve/` creates the hospital admin account and sends a password setup email with `{{FRONTEND_URL}}/reset-password?token=<token>`.

Email delivery errors are logged and do not fail API requests.

---

## Hospital Offboarding Workflow

Hospitals can request to leave the platform without deleting historical records.

Workflow:
- `POST /api/v1/hospitals/{id}/offboarding-request/` by `HEALTHCARE_ADMIN` (same hospital only) creates a pending request.
- `GET /api/v1/admin/hospital-offboarding-requests/` and `GET /api/v1/admin/hospital-offboarding-requests/{id}/` are `SUPER_ADMIN` review endpoints.
- `POST /api/v1/admin/hospital-offboarding-requests/{id}/approve/` marks request approved, sets hospital status to `offboarded`, disables hospital-linked accounts, and disables active API integrations.
- `POST /api/v1/admin/hospital-offboarding-requests/{id}/reject/` marks request rejected with optional admin notes.

Role naming note:
- `HEALTHCARE_ADMIN` is the single hospital-admin role name.

Notes:
- Offboarding is not physical deletion. Historical data remains available (requests, shipments, inventory history, shares, credits, messages, logs, notifications).
- Rejected requests can be submitted again later.

---

## Request Workflow-v2 and Payment APIs

Base prefix: `/api/v1/requests/`

Core lifecycle endpoints:

- `GET /api/v1/requests/`
- `POST /api/v1/requests/`
- `GET /api/v1/requests/{id}/`
- `DELETE /api/v1/requests/{id}/` (cancel alias)
- `POST /api/v1/requests/{id}/approve/`
- `POST /api/v1/requests/{id}/dispatch/`
- `POST /api/v1/requests/confirm-delivery/`
- `POST /api/v1/requests/{id}/reserve/`
- `POST /api/v1/requests/{id}/transfer-confirm/`
- `POST /api/v1/requests/{id}/verify-return/`
- `POST /api/v1/requests/expire/`

Payment and refund endpoints:

- `POST /api/v1/requests/{id}/payments/initiate/`
- `POST /api/v1/requests/{id}/payments/confirm/`
- `POST /api/v1/requests/{id}/refunds/initiate/`
- `POST /api/v1/requests/{id}/refunds/confirm/`
- `GET /api/v1/requests/payments/report/`
- `POST /api/v1/requests/payments/reconcile/`

Compatibility endpoint retained for existing clients:

- `POST /api/v1/requests/{id}/confirm-payment/`

Idempotency:

- `Idempotency-Key` header is optional (recommended) on:
   - `POST /api/v1/requests/{id}/reserve/`
   - `POST /api/v1/requests/{id}/transfer-confirm/`
- `Idempotency-Key` header is required on:
   - `POST /api/v1/requests/{id}/payments/initiate/`
   - missing header returns validation error.

SSLCommerz callback architecture (backend-first source of truth):

```text
SSLCommerz
-> /api/v1/requests/payments/webhooks/sslcommerz/
-> provider validation (val_id) + mapping checks
-> payment/request state mutation
-> optional 302 redirect to frontend success/cancel route
```

Local development payment testing (Docker + localhost frontend):

- Do not use Docker-internal or loopback callback hosts for provider callbacks.
- Configure a public callback base URL (tunnel or public domain):

```env
PAYMENT_PUBLIC_BASE_URL=https://abc123.ngrok-free.app
```

- Callback URL generation rejects non-public hosts such as `localhost`, `127.0.0.1`, `backend`, and `host.docker.internal`.
- Legacy fallback: if `PAYMENT_PUBLIC_BASE_URL` is empty, backend will try `SSLCZ_CALLBACK_BASE_URL`.
- Compose-managed tunnel (recommended):
   - Set `NGROK_AUTH_TOKEN` in `.env`.
   - Start the service with `docker compose -f docker-compose.dev.yml up -d ngrok`.
   - Backend can auto-discover the active tunnel from `NGROK_API_URL` when callback base env vars are empty.

Tunnel examples:

```bash
# compose-managed ngrok
docker compose -f docker-compose.dev.yml up -d ngrok

# inspect current tunnel URL
curl http://localhost:4040/api/tunnels

# cloudflared
cloudflared tunnel --url http://localhost:8000
```

Set the returned HTTPS URL as `PAYMENT_PUBLIC_BASE_URL`, then recreate backend service:

```bash
docker compose -f docker-compose.dev.yml up -d --force-recreate backend
```

Frontend payment success UX recommendation:

- After redirect to frontend success route, immediately fetch latest request/payment state from backend.
- Show `Verifying payment...` and poll every 2 seconds (up to 5-10 seconds) while status remains pending.
- Treat backend response as source of truth; redirect alone is not payment confirmation.
- Prefer sending `return_url`/`cancel_url` in payment initiation from the exact calling page URL.
- If initiation omits those fields, backend infers them from `X-Frontend-Return-Url` / `X-Frontend-Cancel-Url`, then `Referer`, then `Origin`.
- If no inferable frontend URL is available, backend falls back to `FRONTEND_URL` for browser callback redirects.

Backward compatibility notes:

- `SSLCZ_CALLBACK_BASE_URL` remains supported as a fallback but is deprecated in favor of `PAYMENT_PUBLIC_BASE_URL`.
- Existing frontend `return_url`/`cancel_url` contracts remain unchanged.
- Existing webhook endpoint path remains unchanged: `POST /api/v1/requests/payments/webhooks/sslcommerz/`.

Authorization and workflow guardrails:

- Supplying hospital side (or `SUPER_ADMIN`) only:
   - `POST /api/v1/requests/{id}/approve/`
   - `POST /api/v1/requests/{id}/dispatch/`
   - `POST /api/v1/requests/{id}/reserve/`
   - `POST /api/v1/requests/{id}/verify-return/`
- Requesting hospital side (or `SUPER_ADMIN`) only:
   - `POST /api/v1/requests/{id}/confirm-payment/` (legacy)
   - `POST /api/v1/requests/{id}/payments/initiate/`
   - `POST /api/v1/requests/{id}/payments/confirm/`
   - `POST /api/v1/requests/{id}/refunds/initiate/`
   - `POST /api/v1/requests/{id}/refunds/confirm/`
- Delivery/transfer guards:
   - `quantity_received` must be less than or equal to approved quantity.
   - `quantity_received` must be less than or equal to reserved quantity.
   - partial transfer automatically releases residual reservation.
- Payment report scoping:
   - hospital admins can only view their own hospital totals; cross-hospital query is blocked for non-`SUPER_ADMIN`.

Workflow-v2 states:

- `PENDING`
- `APPROVED`
- `RESERVED`
- `PAYMENT_PENDING`
- `PAYMENT_COMPLETED`
- `IN_TRANSIT`
- `COMPLETED`
- `FAILED`
- `CANCELLED`
- `EXPIRED`

Background jobs (auto maintenance):

- Request expiry sweep task: `apps.requests.tasks.expire_due_requests_task`
   - interval: `REQUEST_EXPIRY_SWEEP_INTERVAL_SECONDS` (default 300s)
- Pending payment reconciliation task: `apps.requests.tasks.reconcile_pending_payments_task`
   - interval: `PAYMENT_RECONCILIATION_INTERVAL_SECONDS` (default 600s)

---

## ML Orchestration APIs

Base prefix: `/api/v1/ml/`

Model inference APIs (JSON input workflow):

- `POST /api/v1/ml/model1/predict/`
- `POST /api/v1/ml/model2/predict/`

Notes:

- These endpoints are additive and do not replace legacy `POST /api/v1/ml/jobs/`.
- They support both immediate and scheduled prediction through `scheduled_time`.
- Job lifecycle/status polling remains through existing job/result endpoints.

Job APIs:

- `POST /api/v1/ml/jobs/`
- `GET /api/v1/ml/jobs/`
- `GET /api/v1/ml/jobs/{job_id}/`
- `POST /api/v1/ml/jobs/{job_id}/retry/`
- `POST /api/v1/ml/jobs/{job_id}/cancel/`
- `GET /api/v1/ml/jobs/{job_id}/events/`
- `GET /api/v1/ml/jobs/{job_id}/results/forecast/`
- `GET /api/v1/ml/jobs/{job_id}/results/outbreak/`

Schedule APIs:

- `POST /api/v1/ml/schedules/`
- `GET /api/v1/ml/schedules/`
- `PATCH /api/v1/ml/schedules/{schedule_id}/`
- `POST /api/v1/ml/schedules/{schedule_id}/activate/`
- `POST /api/v1/ml/schedules/{schedule_id}/deactivate/`

Facility result/settings APIs:

- `GET /api/v1/ml/facilities/{facility_id}/latest-forecast/`
- `GET /api/v1/ml/facilities/{facility_id}/latest-outbreak/`
- `GET /api/v1/ml/facilities/{facility_id}/request-suggestions/`
- `PATCH /api/v1/ml/facilities/{facility_id}/settings/`

Training and model version lifecycle APIs:

- `POST /api/v1/ml/training/datasets/generate/`
- `GET /api/v1/ml/training/datasets/`
- `GET /api/v1/ml/training/datasets/{dataset_id}/`
- `POST /api/v1/ml/training/datasets/{dataset_id}/approve/`
- `POST /api/v1/ml/training/datasets/{dataset_id}/reject/`
- `POST /api/v1/ml/training/jobs/`
- `GET /api/v1/ml/training/jobs/`
- `GET /api/v1/ml/training/jobs/{training_job_id}/`
- `POST /api/v1/ml/training/callbacks/server-b/`
- `GET /api/v1/ml/model-versions/`
- `GET /api/v1/ml/model-versions/{version_id}/`
- `POST /api/v1/ml/model-versions/{version_id}/review/`
- `POST /api/v1/ml/model-versions/{version_id}/approve/`
- `POST /api/v1/ml/model-versions/{version_id}/activate/`
- `POST /api/v1/ml/model-versions/{version_id}/deactivate/`
- `POST /api/v1/ml/model-versions/{version_id}/rollback/`
- `GET /api/v1/ml/models/active/`

Role and permission model:

- `ML_ENGINEER`: dataset generation/review and training job lifecycle.
- `ML_ADMIN`: model version approval/activation/deactivation/rollback.
- Existing `SUPER_ADMIN`/`PLATFORM_ADMIN` retain access.

Callback API (Server B -> Server A):

- `POST /api/v1/ml/callbacks/server-b/`

Callback security headers:

- `X-Signature`
- `X-Timestamp`
- `X-Request-Id`

Supported callback signature schemes:

- Legacy: `HMAC-SHA256("<timestamp>.<canonical_json_payload>")`
- Server B runtime compatibility: `SHA256("<timestamp>.<request_id>.<canonical_json_payload>.<secret>")`

Training callback payload compatibility:

- `training_job_id` or `job_id` is accepted as the callback job identifier.
- `version_name` or `model_version` is accepted for version registration.

---

## Inventory CSV AI Assistant

The CSV assistant helps users troubleshoot validation/import errors through chat while reusing existing validation outputs.

Flow:

- Validate CSV using `POST /api/v1/inventory-module/imports/validate/`
- Store returned `file_id`
- Ask question using `POST /api/csv/chat`

Chat request example:

```json
{
   "file_id": "uuid",
   "query": "Why is this CSV failing?",
   "language": "en"
}
```

Chat response contract (`data` payload):

```json
{
   "success": true,
   "summary": "Validation completed",
   "issues": [
      {
         "row": 1,
         "message": "Missing batch number"
      }
   ],
   "recommendation": "Fix the invalid rows and reupload"
}
```

Notes:

- LLM prompt enforces JSON-only output (no markdown/code fences).
- Backend validates response shape before returning to clients.
- If LLM output is malformed, backend performs lightweight cleanup and falls back safely.

Environment variables:

- `GROQ_API_KEY`
- `GROQ_API_URL` (default: `https://api.groq.com/openai/v1/chat/completions`)
- `GROQ_MODEL` (default: `llama-3.1-8b-instant`)
- `GROQ_REQUEST_TIMEOUT_SECONDS`
- `GEMINI_API_KEY`
- `GEMINI_API_URL` (default: `https://generativelanguage.googleapis.com/v1beta/models`)
- `GEMINI_MODEL` (default: `gemini-2.5-flash`)
- `GEMINI_MODEL_FALLBACKS` (default: `gemini-2.5-flash,gemini-2.0-flash,gemini-flash-latest`)
- `GEMINI_REQUEST_TIMEOUT_SECONDS`
- `LLM_PROVIDER_PRIORITY` (default: `groq,gemini`)
- `INVENTORY_CSV_EXPECTED_SCHEMA`
- `INVENTORY_CSV_CHAT_SAMPLE_ROW_LIMIT`
- `INVENTORY_CSV_CHAT_CONTEXT_MAX_ERRORS`

Provider behavior:

- CSV AI chat tries providers in `LLM_PROVIDER_PRIORITY` order.
- If one provider is unavailable, it automatically falls back to the next configured provider.

Medicine information fallback chain:

- `MEDICINE_INFO_PRIMARY_API`
- `MEDICINE_INFO_FALLBACK_APIS` (comma- or semicolon-separated)
- `MEDICINE_INFO_RESOLUTION_ORDER` (default: `llm,api`; supports API fallback)
- `MEDICINE_INFO_LLM_LANGUAGE` (default: `en`)
- `MEDICINE_INFO_REQUEST_TIMEOUT_SECONDS` (default: `5`)
- `MEDICINE_INFO_RETRY_COUNT` (default: `1`)
- `MEDICINE_INFO_CACHE_TTL` (default: `86400` / 24h)
- `MEDICINE_INFO_CACHE_TTL_SECONDS` (legacy alias; defaults to `MEDICINE_INFO_CACHE_TTL`)
- `MEDICINE_INFO_STALE_CACHE_TTL` (default: `MEDICINE_INFO_CACHE_TTL * 7`)
- `MEDICINE_INFO_ENABLE_CATALOG_ENRICHMENT` (default: `true`)

Catalog detail enrichment:

- `GET /api/v1/catalog/{id}/` includes `medicine_info` for medication-like resource types by default.
- Use query param `include_medicine_info=true|false` to explicitly control enrichment per request.
- Optional language param for medicine info: `lang=en|bn` (unsupported values fall back to `en`).
- Dedicated endpoint for frontend/manual panels: `GET /api/v1/catalog/{id}/medicine-info/`
- Manual cache refresh endpoint: `POST /api/v1/catalog/{id}/medicine-info/refresh/`
- Refresh route also accepts `lang` in request body (or query param).

Medicine localization metadata:

- Response payload now includes `language` and `translated`.
- When Bengali translation succeeds, payload also includes `sourceLanguage="en"`.
- If translation fails, endpoint still returns English content with `language="en"` and `translated=false`.

Cache-aside behavior:

- Backend checks medicine cache first using language-aware keys like `medicine:paracetamol:en` and `medicine:paracetamol:bn`.
- Cache hit returns immediately with `cache.hit=true` and `cache.stale=false`.
- Cache miss fetches providers, stores successful response, then returns `cache.hit=false`.
- If provider fetch fails and stale cache exists, stale data is returned with `cache.hit=true` and `cache.stale=true`.

ML dataset generation/upload workflow details and real-container validation steps:

- `integration_docs/ml_dataset_pipeline_audit_and_validation.md`

---

## Backend Validation Commands

Run targeted workflow-v2 + ML integration tests:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest tests/integration/test_requests_workflow_v2.py tests/integration/test_ml_api.py -q
```

Run full backend suite:

```bash
docker compose -f docker-compose.dev.yml exec backend pytest -q
```

Generate/apply migrations in Docker:

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py makemigrations hospitals resources requests ml
docker compose -f docker-compose.dev.yml exec backend python manage.py migrate
```

---

## Database Schema Recovery (Development)

If migration history and schema drift (for example, `UndefinedColumn` errors), rebuild the dev DB from migrations instead of patching tables manually.

Quick fix for the specific chat/conversations drift seen as
`ProgrammingError: column "last_read_message_id" of relation "communications_participant" does not exist`:

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py migrate communications
```

```bash
# 1) Optional backup
docker compose -f docker-compose.dev.yml exec -T db \
   pg_dump -U hrsp_user -d hrsp_db --inserts --clean --if-exists > backups/db_backup.sql

# 2) Reset dev volumes
docker compose -f docker-compose.dev.yml down -v

# 3) Start services
docker compose -f docker-compose.dev.yml up -d --build db redis backend

# 4) Apply migrations
docker compose -f docker-compose.dev.yml exec backend python manage.py migrate

# 5) Seed test users
docker compose -f docker-compose.dev.yml exec backend python manage.py create_test_users
```

Validation commands:

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py showmigrations
docker compose -f docker-compose.dev.yml exec backend python manage.py makemigrations --check --dry-run
docker compose -f docker-compose.dev.yml exec backend pytest -q
```

The `create_test_users` command now bootstraps the `system` hospital automatically on a fresh database so the 4 login test accounts are always created successfully:

- `admin@medibridge.com` / `Admin@1234` (`SUPER_ADMIN`)
- `ml_admin@medibridge.com` / `MlAdmin@1234` (`ML_ADMIN`)
- `hospital_admin@medibridge.com` / `HospAdmin@123` (`HEALTHCARE_ADMIN`)
- `staff@medibridge.com` / `Staff@123456` (`HEALTHCARE_ADMIN`)
