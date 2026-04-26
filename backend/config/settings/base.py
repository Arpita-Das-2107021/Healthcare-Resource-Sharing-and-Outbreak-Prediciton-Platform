"""
Base settings shared across all environments.
"""
import os
from datetime import timedelta
from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # backend/

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, []),
    JWT_ACCESS_TOKEN_LIFETIME_MINUTES=(int, 15),
    JWT_REFRESH_TOKEN_LIFETIME_DAYS=(int, 7),
)

# Read .env file when present (development convenience - prod injects vars directly)
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "channels",
    "storages",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "axes",
    "django_filters",
    "drf_spectacular",
    "django_celery_beat",
]

LOCAL_APPS = [
    "apps.core",
    "apps.authentication",
    "apps.ml",
    "apps.hospitals",
    "apps.badges",
    "apps.staff",
    "apps.resources",
    "apps.inventory_module",
    "apps.pharmacy_csv",
    "apps.sales",
    "apps.requests",
    "apps.shipments",
    "apps.notifications",
    "apps.communications",
    "apps.chat",
    "apps.analytics",
    "apps.audit",
    "apps.public",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "common.logging.middleware.RequestLoggingMiddleware",
    "common.logging.middleware.AuditMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Custom User Model
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "authentication.UserAccount"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db("DATABASE_URL", default="postgresql://hrsp_user:hrsp_password@localhost:5432/hrsp_db")
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DATABASES["default"]["CONN_MAX_AGE"] = 60

# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & media files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = env("STATIC_ROOT", default=str(BASE_DIR / "static"))

MEDIA_URL = "/media/"
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BASE_DIR / "media"))

USE_MINIO_CHAT_STORAGE = env.bool("USE_MINIO_CHAT_STORAGE", default=False)
MINIO_ENDPOINT_URL = env("MINIO_ENDPOINT_URL", default="http://minio:9000")
MINIO_PUBLIC_ENDPOINT = env("MINIO_PUBLIC_ENDPOINT", default="http://localhost:9000")
MINIO_ACCESS_KEY = env("MINIO_ACCESS_KEY", default="minioadmin")
MINIO_SECRET_KEY = env("MINIO_SECRET_KEY", default="minioadmin")
MINIO_BUCKET_NAME = env("MINIO_BUCKET_NAME", default="hrsp-chat-attachments")
MINIO_REGION_NAME = env("MINIO_REGION_NAME", default="us-east-1")
MINIO_USE_SSL = env.bool("MINIO_USE_SSL", default=False)
MINIO_AUTO_CREATE_BUCKET = env.bool("MINIO_AUTO_CREATE_BUCKET", default=True)
MINIO_PUBLIC_READ = env.bool("MINIO_PUBLIC_READ", default=True)

CHAT_VIDEO_TRANSCODE_ENABLED = env.bool("CHAT_VIDEO_TRANSCODE_ENABLED", default=True)
CHAT_VIDEO_TRANSCODE_THRESHOLD_BYTES = env.int("CHAT_VIDEO_TRANSCODE_THRESHOLD_BYTES", default=12 * 1024 * 1024)
CHAT_FFMPEG_BINARY = env("CHAT_FFMPEG_BINARY", default="ffmpeg")
CHAT_VIDEO_CRF = env.int("CHAT_VIDEO_CRF", default=28)

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": MEDIA_ROOT,
            "base_url": MEDIA_URL,
        },
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

if USE_MINIO_CHAT_STORAGE:
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": MINIO_ACCESS_KEY,
            "secret_key": MINIO_SECRET_KEY,
            "bucket_name": MINIO_BUCKET_NAME,
            "endpoint_url": MINIO_ENDPOINT_URL,
            "region_name": MINIO_REGION_NAME,
            "default_acl": None,
            "querystring_auth": not MINIO_PUBLIC_READ,
            "addressing_style": "path",
            "file_overwrite": False,
        },
    }
    MEDIA_URL = f"{MINIO_PUBLIC_ENDPOINT.rstrip('/')}/{MINIO_BUCKET_NAME}/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "common.utils.pagination.StandardResultsPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "common.exceptions.handlers.custom_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/minute",
        "user": "300/minute",
        "login": "10/minute",
        "password_reset": "5/hour",
        "hospital_registration": "5/hour",
    },
}

# ---------------------------------------------------------------------------
# JWT Settings
# ---------------------------------------------------------------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env("JWT_ACCESS_TOKEN_LIFETIME_MINUTES")),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env("JWT_REFRESH_TOKEN_LIFETIME_DAYS")),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env("JWT_SIGNING_KEY", default=SECRET_KEY),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_OBTAIN_SERIALIZER": "apps.authentication.serializers.CustomTokenObtainPairSerializer",
}

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# Axes (account lockout)
# ---------------------------------------------------------------------------
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = ["username"]
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_CALLABLE = "common.utils.lockout.lockout_response"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# ---------------------------------------------------------------------------
# Redis & Celery
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://redis:6379/0")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [env("CHANNEL_LAYER_REDIS_URL", default="redis://redis:6379/3")],
        },
    }
}
HOSPITAL_SYNC_INTERVAL_SECONDS = env.int("HOSPITAL_SYNC_INTERVAL_SECONDS", default=3600)
REQUEST_EXPIRY_SWEEP_INTERVAL_SECONDS = env.int("REQUEST_EXPIRY_SWEEP_INTERVAL_SECONDS", default=300)
PAYMENT_RECONCILIATION_INTERVAL_SECONDS = env.int("PAYMENT_RECONCILIATION_INTERVAL_SECONDS", default=600)
SSLCZ_STORE_ID = env("SSLCZ_STORE_ID", default="")
SSLCZ_STORE_PASSWORD = env("SSLCZ_STORE_PASSWORD", default="")
SSLCZ_TESTMODE = env.bool("SSLCZ_TESTMODE", default=True)
SSLCZ_LOCALHOST = env("SSLCZ_LOCALHOST", default="localhost")
SSLCZ_REWRITE_LOCALHOST = env.bool("SSLCZ_REWRITE_LOCALHOST", default=False)
PAYMENT_PUBLIC_BASE_URL = env("PAYMENT_PUBLIC_BASE_URL", default="")
SSLCZ_CALLBACK_BASE_URL = env("SSLCZ_CALLBACK_BASE_URL", default="")
SSLCZ_REQUEST_TIMEOUT_SECONDS = env.int("SSLCZ_REQUEST_TIMEOUT_SECONDS", default=20)
ML_SCHEDULE_SWEEP_INTERVAL_SECONDS = env.int("ML_SCHEDULE_SWEEP_INTERVAL_SECONDS", default=60)
ML_PENDING_JOB_SWEEP_INTERVAL_SECONDS = env.int("ML_PENDING_JOB_SWEEP_INTERVAL_SECONDS", default=60)

ML_DATASET_PIPELINE_ENABLED = env.bool("ML_DATASET_PIPELINE_ENABLED", default=True)
ML_AUTO_DISPATCH_ON_JOB_CREATE = env.bool("ML_AUTO_DISPATCH_ON_JOB_CREATE", default=False)
ML_SCHEDULE_DEFAULT_EXECUTION_MODE = env("ML_SCHEDULE_DEFAULT_EXECUTION_MODE", default="csv_snapshot")
ML_SCHEDULE_WARNING_WINDOW_MINUTES = env.int("ML_SCHEDULE_WARNING_WINDOW_MINUTES", default=60)
ML_SCHEDULE_SAME_DAY_GRACE_MINUTES = env.int("ML_SCHEDULE_SAME_DAY_GRACE_MINUTES", default=60)
ML_DATASET_LOOKBACK_DAYS = env.int("ML_DATASET_LOOKBACK_DAYS", default=90)
ML_DATASET_BUCKET_NAME = env("ML_DATASET_BUCKET_NAME", default="ml-input")
ML_DATASET_PREFIX = env("ML_DATASET_PREFIX", default="snapshots")
ML_TRAINING_DATASET_PREFIX = env("ML_TRAINING_DATASET_PREFIX", default="training-snapshots")
ML_OUTBREAK_GROUND_TRUTH_OBJECT_KEY = env(
    "ML_OUTBREAK_GROUND_TRUTH_OBJECT_KEY",
    default="static/outbreaks_ground_truth.csv",
)
ML_SALES_BASELINE_RATIO = env.float("ML_SALES_BASELINE_RATIO", default=0.02)
ML_SALES_BASELINE_MAX_PER_ITEM = env.int("ML_SALES_BASELINE_MAX_PER_ITEM", default=50)
ML_INCLUDE_TRANSFER_OUT_IN_SALES = env.bool("ML_INCLUDE_TRANSFER_OUT_IN_SALES", default=False)

ML_SERVER_B_BASE_URL = env("ML_SERVER_B_BASE_URL", default="")
ML_SERVER_B_FORECAST_PATH = env("ML_SERVER_B_FORECAST_PATH", default="/api/v1/ml/jobs/forecast")
ML_SERVER_B_OUTBREAK_PATH = env("ML_SERVER_B_OUTBREAK_PATH", default="/api/v1/ml/jobs/outbreak")
ML_SERVER_B_MODEL1_PREDICT_PATH = env("ML_SERVER_B_MODEL1_PREDICT_PATH", default="/api/v1/inference/model1/predict")
ML_SERVER_B_MODEL2_PREDICT_PATH = env("ML_SERVER_B_MODEL2_PREDICT_PATH", default="/api/v1/inference/model2/predict")
ML_SERVER_B_MODEL_VERSIONS_PATH_TEMPLATE = env(
    "ML_SERVER_B_MODEL_VERSIONS_PATH_TEMPLATE",
    default="/api/v1/models/{model_type}/versions",
)
ML_SERVER_B_MODEL1_TRAIN_PATH = env("ML_SERVER_B_MODEL1_TRAIN_PATH", default="/api/v1/training/model1/train")
ML_SERVER_B_MODEL2_TRAIN_PATH = env("ML_SERVER_B_MODEL2_TRAIN_PATH", default="/api/v1/training/model2/train")
ML_SERVER_B_TRAIN_PATH = env("ML_SERVER_B_TRAIN_PATH", default="")
ML_SERVER_B_INCLUDE_DATASET_SNAPSHOT_URI = env.bool("ML_SERVER_B_INCLUDE_DATASET_SNAPSHOT_URI", default=False)
ML_TRAINING_MIRROR_ENABLED = env.bool("ML_TRAINING_MIRROR_ENABLED", default=True)
ML_TRAINING_MIRROR_MAX_RETRIES = env.int("ML_TRAINING_MIRROR_MAX_RETRIES", default=3)
ML_TRAINING_MIRROR_BACKOFF_SECONDS = env.int("ML_TRAINING_MIRROR_BACKOFF_SECONDS", default=1)
ML_TRAINING_MIRROR_DEAD_LETTER_LOG_PATH = env(
    "ML_TRAINING_MIRROR_DEAD_LETTER_LOG_PATH",
    default="/app/logs/ml_training_mirror_dead_letter.jsonl",
)
ML_SERVER_B_MINIO_ENDPOINT_URL = env("ML_SERVER_B_MINIO_ENDPOINT_URL", default="")
ML_SERVER_B_MINIO_ACCESS_KEY = env("ML_SERVER_B_MINIO_ACCESS_KEY", default="")
ML_SERVER_B_MINIO_SECRET_KEY = env("ML_SERVER_B_MINIO_SECRET_KEY", default="")
ML_SERVER_B_MINIO_REGION_NAME = env("ML_SERVER_B_MINIO_REGION_NAME", default="")
ML_SERVER_B_MINIO_BUCKET_NAME_OVERRIDE = env("ML_SERVER_B_MINIO_BUCKET_NAME_OVERRIDE", default="")
ML_SERVER_B_MINIO_KEY_PREFIX = env("ML_SERVER_B_MINIO_KEY_PREFIX", default="")
ML_SERVER_B_REQUEST_TIMEOUT_SECONDS = env.int("ML_SERVER_B_REQUEST_TIMEOUT_SECONDS", default=30)
ML_SERVER_B_CALLBACK_TIMEOUT_SECONDS = env.int("ML_SERVER_B_CALLBACK_TIMEOUT_SECONDS", default=10)
ML_SERVER_B_HMAC_SECRET = env("ML_SERVER_B_HMAC_SECRET", default="dev-secret")
ML_SERVER_A_CALLBACK_URL = env("ML_SERVER_A_CALLBACK_URL", default="")
ML_SERVER_A_TRAINING_CALLBACK_URL = env("ML_SERVER_A_TRAINING_CALLBACK_URL", default="")
ML_INFERENCE_AUTO_DISPATCH_ON_CREATE = env.bool("ML_INFERENCE_AUTO_DISPATCH_ON_CREATE", default=True)
ML_CALLBACK_REPLAY_TTL_SECONDS = env.int("ML_CALLBACK_REPLAY_TTL_SECONDS", default=300)

# CSV AI assistant (Groq)
GROQ_API_KEY = env("GROQ_API_KEY", default="")
GROQ_API_URL = env("GROQ_API_URL", default="https://api.groq.com/openai/v1/chat/completions")
GROQ_MODEL = env("GROQ_MODEL", default="llama-3.1-8b-instant")
GROQ_REQUEST_TIMEOUT_SECONDS = env.int("GROQ_REQUEST_TIMEOUT_SECONDS", default=20)

# CSV AI assistant (Gemini fallback)
GEMINI_API_KEY = env("GEMINI_API_KEY", default="")
GEMINI_API_URL = env("GEMINI_API_URL", default="https://generativelanguage.googleapis.com/v1beta/models")
GEMINI_MODEL = env("GEMINI_MODEL", default="gemini-2.5-flash")
GEMINI_MODEL_FALLBACKS = env.list(
    "GEMINI_MODEL_FALLBACKS",
    default=["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"],
)
GEMINI_REQUEST_TIMEOUT_SECONDS = env.int("GEMINI_REQUEST_TIMEOUT_SECONDS", default=20)

# Provider priority for AI chat fallback chain.
LLM_PROVIDER_PRIORITY = env.list("LLM_PROVIDER_PRIORITY", default=["groq", "gemini"])

# Medicine info enrichment fallback chain.
MEDICINE_INFO_PRIMARY_API = env("MEDICINE_INFO_PRIMARY_API", default="")
MEDICINE_INFO_FALLBACK_APIS = env.list("MEDICINE_INFO_FALLBACK_APIS", default=[])
MEDICINE_INFO_RESOLUTION_ORDER = env.list("MEDICINE_INFO_RESOLUTION_ORDER", default=["api", "llm"])
MEDICINE_INFO_LLM_LANGUAGE = env("MEDICINE_INFO_LLM_LANGUAGE", default="en")
MEDICINE_TRANSLATOR_API_URL = env("MEDICINE_TRANSLATOR_API_URL", default="")
MEDICINE_TRANSLATOR_API_KEY = env("MEDICINE_TRANSLATOR_API_KEY", default="")
MEDICINE_TRANSLATOR_API_KEY_HEADER = env("MEDICINE_TRANSLATOR_API_KEY_HEADER", default="Authorization")
MEDICINE_TRANSLATOR_API_KEY_PREFIX = env("MEDICINE_TRANSLATOR_API_KEY_PREFIX", default="Bearer")
MEDICINE_TRANSLATOR_TIMEOUT_SECONDS = env.int("MEDICINE_TRANSLATOR_TIMEOUT_SECONDS", default=10)
MEDICINE_INFO_SYNONYM_MAP = env.list(
    "MEDICINE_INFO_SYNONYM_MAP",
    default=[
        "pcm:paracetamol",
        "acetaminophen:paracetamol",
        "tylenol:paracetamol",
    ],
)
MEDICINE_INFO_ENABLE_FUZZY_MATCH = env.bool("MEDICINE_INFO_ENABLE_FUZZY_MATCH", default=True)
MEDICINE_INFO_FUZZY_THRESHOLD = env.float("MEDICINE_INFO_FUZZY_THRESHOLD", default=0.88)
MEDICINE_INFO_FUZZY_CANDIDATES = env.list("MEDICINE_INFO_FUZZY_CANDIDATES", default=["paracetamol"])
MEDICINE_INFO_ENABLE_GEMINI_PARSE_FALLBACK = env.bool("MEDICINE_INFO_ENABLE_GEMINI_PARSE_FALLBACK", default=True)
MEDICINE_INFO_LLM_FALLBACK_UNCERTAIN_ONLY = env.bool("MEDICINE_INFO_LLM_FALLBACK_UNCERTAIN_ONLY", default=True)
MEDICINE_INFO_DAILYMED_PAGE_SIZE = env.int("MEDICINE_INFO_DAILYMED_PAGE_SIZE", default=8)
MEDICINE_INFO_REQUEST_TIMEOUT_SECONDS = env.int("MEDICINE_INFO_REQUEST_TIMEOUT_SECONDS", default=5)
MEDICINE_INFO_RETRY_COUNT = env.int("MEDICINE_INFO_RETRY_COUNT", default=1)
MEDICINE_INFO_CACHE_TTL = env.int("MEDICINE_INFO_CACHE_TTL", default=86400)
MEDICINE_INFO_CACHE_TTL_SECONDS = env.int("MEDICINE_INFO_CACHE_TTL_SECONDS", default=MEDICINE_INFO_CACHE_TTL)
MEDICINE_INFO_STALE_CACHE_TTL = env.int("MEDICINE_INFO_STALE_CACHE_TTL", default=MEDICINE_INFO_CACHE_TTL * 7)
MEDICINE_INFO_ENABLE_CATALOG_ENRICHMENT = env.bool("MEDICINE_INFO_ENABLE_CATALOG_ENRICHMENT", default=True)

INVENTORY_CSV_EXPECTED_SCHEMA = env.list(
    "INVENTORY_CSV_EXPECTED_SCHEMA",
    default=[
        "name",
        "quantity",
        "price",
        "expiry_date",
        "manufacturer",
        "batch_number",
        "unit",
        "resource_type",
        "currency",
        "description",
    ],
)
INVENTORY_CSV_CHAT_SAMPLE_ROW_LIMIT = env.int("INVENTORY_CSV_CHAT_SAMPLE_ROW_LIMIT", default=5)
INVENTORY_CSV_CHAT_CONTEXT_MAX_ERRORS = env.int("INVENTORY_CSV_CHAT_CONTEXT_MAX_ERRORS", default=20)
INVENTORY_CSV_CHAT_MAX_HISTORY_MESSAGES = env.int("INVENTORY_CSV_CHAT_MAX_HISTORY_MESSAGES", default=10)

PHARMACY_CSV_CHAT_SAMPLE_ROW_LIMIT = env.int("PHARMACY_CSV_CHAT_SAMPLE_ROW_LIMIT", default=5)
PHARMACY_CSV_CHAT_CONTEXT_MAX_ERRORS = env.int("PHARMACY_CSV_CHAT_CONTEXT_MAX_ERRORS", default=20)
PHARMACY_CSV_CHAT_CONTEXT_MAX_CONFLICTS = env.int("PHARMACY_CSV_CHAT_CONTEXT_MAX_CONFLICTS", default=20)
PHARMACY_CSV_CHAT_MAX_HISTORY_MESSAGES = env.int("PHARMACY_CSV_CHAT_MAX_HISTORY_MESSAGES", default=10)

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://redis:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://redis:6379/2")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Task execution settings for performance
CELERY_TASK_TRACK_STARTED = True  # Track when tasks start
CELERY_TASK_TIME_LIMIT = 30 * 60  # Hard limit: 30 minutes per task
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # Soft limit: 25 minutes (allows cleanup)
CELERY_BROKER_POOL_LIMIT = 10  # Broker connection pool size
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # Fetch 1 task at a time (prevents task hoarding)
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000  # Restart worker after 1000 tasks to prevent memory leaks

CELERY_BEAT_SCHEDULE = {
    "sync-all-active-hospitals": {
        "task": "apps.hospitals.tasks.sync_all_active_hospitals_task",
        "schedule": HOSPITAL_SYNC_INTERVAL_SECONDS,
    },
    "expire-due-requests": {
        "task": "apps.requests.tasks.expire_due_requests_task",
        "schedule": REQUEST_EXPIRY_SWEEP_INTERVAL_SECONDS,
        "args": (500,),
    },
    "reconcile-pending-payments": {
        "task": "apps.requests.tasks.reconcile_pending_payments_task",
        "schedule": PAYMENT_RECONCILIATION_INTERVAL_SECONDS,
    },
    "enqueue-due-ml-schedules": {
        "task": "apps.ml.tasks.enqueue_due_ml_schedules_task",
        "schedule": ML_SCHEDULE_SWEEP_INTERVAL_SECONDS,
    },
    "dispatch-pending-ml-jobs": {
        "task": "apps.ml.tasks.dispatch_pending_ml_jobs_task",
        "schedule": ML_PENDING_JOB_SWEEP_INTERVAL_SECONDS,
    },
}

# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = _env_bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "noreply@hrsp.example.com")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
FRONTEND_PAYMENT_RETURN_URL = os.getenv("FRONTEND_PAYMENT_RETURN_URL", "").strip()
NGROK_API_URL = os.getenv("NGROK_API_URL", "").strip()
NGROK_API_TIMEOUT_SECONDS = int(os.getenv("NGROK_API_TIMEOUT_SECONDS", "3"))
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8080"))
RBAC_SEED_CONFIG = env("RBAC_SEED_CONFIG", default=str(BASE_DIR / "apps" / "staff" / "seeds" / "rbac.default.json"))
RBAC_DUAL_SCOPE_SEED_CONFIG = env(
    "RBAC_DUAL_SCOPE_SEED_CONFIG",
    default=str(BASE_DIR / "apps" / "staff" / "seeds" / "rbac.dual_scope.default.json"),
)

# ---------------------------------------------------------------------------
# Encryption key for HospitalAPIConfig tokens
# ---------------------------------------------------------------------------
API_CONFIG_ENCRYPTION_KEY = env("API_CONFIG_ENCRYPTION_KEY", default="")
CHAT_MESSAGE_ENCRYPTION_KEY = env("CHAT_MESSAGE_ENCRYPTION_KEY", default="")

# ---------------------------------------------------------------------------
# Hospital Sync Configuration
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# drf-spectacular (OpenAPI / Swagger)
# ---------------------------------------------------------------------------
SPECTACULAR_SETTINGS = {
    "TITLE": "Hospital Resource Sharing Platform API",
    "DESCRIPTION": "API for inter-hospital resource sharing, requests, and coordination.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SECURITY": [{"jwtAuth": []}],
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "json": {
            "()": "common.logging.middleware.JsonFormatter",
        },
    },
    "filters": {
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "app_file": {
            "level": "INFO",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOGS_DIR / "app.log"),
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 10,
            "formatter": "json",
        },
        "error_file": {
            "level": "WARNING",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOGS_DIR / "error.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 10,
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console", "app_file"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["error_file"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["error_file"],
            "level": "WARNING",
            "propagate": False,
        },
        "hrsp": {
            "handlers": ["console", "app_file", "error_file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "celery": {
            "handlers": ["console", "app_file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
