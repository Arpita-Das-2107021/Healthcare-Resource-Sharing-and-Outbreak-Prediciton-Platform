"""Test settings for deterministic CI/local pytest runs."""

from .dev import *  # noqa: F401,F403


# Keep API tests independent from Redis/network state.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "hrsp-tests",
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# Execute tasks in-process during tests; prevents broker-related flakiness.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

# Disable global throttling in tests to avoid cache coupling and request order flakiness.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_CLASSES": [],
    # Keep scope definitions available for views that declare explicit throttle classes.
    "DEFAULT_THROTTLE_RATES": {
        **REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {}),
        "anon": "100000/minute",
        "user": "100000/minute",
        "login": "100000/minute",
        "password_reset": "100000/minute",
        "hospital_registration": "100000/minute",
    },
}

# Keep test email assertions local and side-effect free.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Keep catalog API tests deterministic by default; dedicated tests can override this setting.
MEDICINE_INFO_ENABLE_CATALOG_ENRICHMENT = False
