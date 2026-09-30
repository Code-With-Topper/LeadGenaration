import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables
load_dotenv(BASE_DIR / '.env')

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-default-key-for-dev')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')
if DEBUG:
    ALLOWED_HOSTS += ['testserver']


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Local apps
    'core',
    'dashboard',
    'leads',
    'lead_generation',
    'crm',
    'emails',
    'followups',
    'quotations',
    'reports',
    'imports',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Serves static files from Gunicorn itself, so a one-server deployment
    # needs no extra Nginx configuration for them.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'config.middleware.LoginRequiredMiddleware',
]

ROOT_URLCONF = 'config.urls'

SESSION_COOKIE_AGE = int(os.environ.get('SESSION_HOURS', 12)) * 3600
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
# Behind Nginx, trust its forwarded-protocol header so Django knows it is HTTPS.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.crm',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

# SQLite is enough for one user and ~10,000 leads. Switching to PostgreSQL
# later is a change here only — no application code depends on the engine.
if os.environ.get('DATABASE_URL', '').startswith('postgres'):
    import dj_database_url  # type: ignore
    DATABASES = {'default': dj_database_url.parse(os.environ['DATABASE_URL'])}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'OPTIONS': {
                # WAL lets the background worker write while the user browses,
                # and the timeout replaces "database is locked" with a wait.
                'init_command': 'PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;',
                'timeout': 30,
            },
        }
    }


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = os.environ.get('TIME_ZONE', 'Asia/Kolkata')

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'static_root'

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        # Compression only, deliberately without manifest hashing. The
        # bundled admin theme ships third-party minified files whose internal
        # source-map references cannot be rewritten, and the hashing backend
        # makes collectstatic fail on them — which would break deployment for
        # no benefit here, since those vendor files never change.
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'
# Imported spreadsheets and email attachments. Uploads are capped so a stray
# large file cannot fill the server's disk.
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Business rules
# ---------------------------------------------------------------------------
# All of these are the client's stated requirements, kept in one place so they
# can be changed without touching code.

# "7 or 10 days after the profile is sent" — the reminder delay.
FOLLOW_UP_DELAY_DAYS = int(os.environ.get('FOLLOW_UP_DELAY_DAYS', 7))
# How long to wait before each later reminder on the same lead.
FOLLOW_UP_REPEAT_DAYS = int(os.environ.get('FOLLOW_UP_REPEAT_DAYS', 7))
# Stop chasing after this many reminders.
FOLLOW_UP_MAX_ROUNDS = int(os.environ.get('FOLLOW_UP_MAX_ROUNDS', 3))

# "About 10 emails a day is enough." Enforced in code, not by good intentions:
# an uncapped send from a new domain is the fastest way to get blacklisted.
EMAIL_DAILY_LIMIT = int(os.environ.get('EMAIL_DAILY_LIMIT', 10))

# Tier 3 email validation: a DNS MX lookup. Free, but needs network access,
# so it can be turned off for offline work.
VALIDATE_EMAIL_MX = os.environ.get('VALIDATE_EMAIL_MX', 'True') == 'True'

# Minimum data quality score before a lead is offered for emailing.
MIN_QUALITY_TO_CONTACT = int(os.environ.get('MIN_QUALITY_TO_CONTACT', 40))

# ---------------------------------------------------------------------------
# Lead generation browser
# ---------------------------------------------------------------------------
# Set these when Chrome and chromedriver are not on the default path, which is
# the usual case on a VPS. Leave blank to let Selenium find them itself.
CHROME_BINARY = os.environ.get('CHROME_BINARY', '')
CHROMEDRIVER_PATH = os.environ.get('CHROMEDRIVER_PATH', '')

# Start background work in a thread right after the user clicks, instead of
# waiting up to a minute for cron. Off during tests, where a worker thread
# would race the test database.
WORKER_AUTOSTART = (
    os.environ.get('WORKER_AUTOSTART', 'True') == 'True'
    and 'test' not in sys.argv
)

# Rows per page on every list screen. Without this the 10,000-lead
# requirement cannot be met on a phone.
PAGE_SIZE = int(os.environ.get('PAGE_SIZE', 50))

# Used to build absolute links (unsubscribe) inside outgoing email.
SITE_URL = os.environ.get('SITE_URL', 'http://127.0.0.1:8000').rstrip('/')

# ---------------------------------------------------------------------------
# Email delivery
# ---------------------------------------------------------------------------
# At 10 messages a day a free Zoho or Gmail mailbox is sufficient. No paid
# email API, and no mandatory subscription.
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.environ.get('EMAIL_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_PASSWORD', '')
EMAIL_TIMEOUT = 20

# With no mailbox configured, print to the console instead of failing. This is
# what lets the system be demonstrated before the client's domain is ready.
if EMAIL_HOST_USER and EMAIL_HOST_PASSWORD:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

DEFAULT_FROM_EMAIL = os.environ.get(
    'DEFAULT_FROM_EMAIL', EMAIL_HOST_USER or 'no-reply@localhost')
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# Shown in the signature and the unsubscribe footer of every message.
COMPANY_NAME = os.environ.get('COMPANY_NAME', 'S.D Enterprise')
COMPANY_ADDRESS = os.environ.get('COMPANY_ADDRESS', 'West Bengal, India')

# HTTPS Settings
SECURE_SSL_REDIRECT = os.environ.get('ENFORCE_HTTPS', 'False') == 'True'
SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT
if SECURE_SSL_REDIRECT:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# Logging — show lead generation activity in the console
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'lead_generator': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'lead_generation': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'core': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'worker': {
            'handlers': ['console'],
            'level': 'INFO',
        },
    },
}
