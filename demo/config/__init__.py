# Celery is optional — background tasks now use Python threading.
# This import is kept for backward compatibility but will not crash
# if celery is not installed or Redis is unavailable.
try:
    from .celery import app as celery_app
    __all__ = ('celery_app',)
except ImportError:
    celery_app = None
    __all__ = ()
