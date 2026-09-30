"""
Background work without a message broker.

There is no Redis and no Celery here on purpose: the client asked for no
mandatory subscription, and a broker is one more service to pay for, monitor
and restart. Instead the database *is* the queue — a job's status column says
what still needs doing — and one management command run from cron picks it up:

    * * * * * cd /srv/leadcrm && .venv/bin/python manage.py run_worker --once

`wake()` additionally kicks a job off in a thread straight after the user
clicks, so the common case feels immediate rather than waiting for the minute
to tick over. If that thread dies with the web process, cron finds the job
again — which is exactly the recovery the old threading-only approach lacked.
"""
from __future__ import annotations

import logging
import threading

from django.utils import timezone

LOGGER = logging.getLogger('worker')

# A RUNNING job whose heartbeat is older than this lost its worker.
STALE_AFTER_MINUTES = 15

_lock = threading.Lock()
_running = False


# --------------------------------------------------------------------------
# One pass of all pending work
# --------------------------------------------------------------------------

def run_pending(*, max_jobs: int = 1) -> dict:
    """
    Do whatever the database says is outstanding, then return.

    Safe to call from cron every minute: it takes the work, or finds none and
    exits quietly.
    """
    summary = {
        'recovered': recover_stale_jobs(),
        'followups': sweep_followups(),
        'imports': run_queued_imports(max_jobs=max_jobs),
        'generation': run_queued_generation(max_jobs=max_jobs),
    }
    return summary


def wake() -> None:
    """
    Start a single background pass, unless one is already running.

    Does nothing when WORKER_AUTOSTART is off (during tests, or where the
    deployment relies on cron alone). The work is not lost either way — the
    next cron pass finds it in the database.
    """
    from django.conf import settings

    if not getattr(settings, 'WORKER_AUTOSTART', True):
        return

    global _running
    with _lock:
        if _running:
            return
        _running = True

    def _worker():
        global _running
        try:
            run_pending()
        except Exception:
            LOGGER.exception('Background pass failed')
        finally:
            with _lock:
                globals()['_running'] = False

    threading.Thread(target=_worker, daemon=True, name='leadcrm-worker').start()


# --------------------------------------------------------------------------
# Follow-up reminders — the client's 7/10 day rule
# --------------------------------------------------------------------------

def sweep_followups() -> int:
    """
    Move every lead whose follow-up date has arrived into Follow-up Due.

    This is the whole reminder feature: the date is set when the profile is
    sent, and this sweep is what makes it appear on the dashboard. No external
    reminder service, no subscription.
    """
    from leads.models import Lead

    today = timezone.localdate()
    due = Lead.objects.filter(
        follow_up_date__lte=today,
        follow_up_date__isnull=False,
    ).exclude(
        status__in=list(Lead.CLOSED_STATUSES) + [Lead.FOLLOW_UP_DUE]
    )

    count = 0
    for lead in due:
        lead.status = Lead.FOLLOW_UP_DUE
        lead.save(update_fields=['status', 'updated_at'])
        count += 1

    if count:
        LOGGER.info('Marked %d lead(s) as Follow-up Due', count)
    return count


# --------------------------------------------------------------------------
# Imports
# --------------------------------------------------------------------------

def run_queued_imports(*, max_jobs: int = 1) -> int:
    from imports.models import ImportJob
    from imports.services import run_import

    done = 0
    for job in ImportJob.objects.filter(status='QUEUED').order_by('id')[:max_jobs]:
        # Claim it first: two workers must not process the same file.
        claimed = ImportJob.objects.filter(pk=job.pk, status='QUEUED') \
                                   .update(status='RUNNING')
        if not claimed:
            continue
        job.refresh_from_db()
        LOGGER.info('Importing job #%s (%s rows)', job.id, job.total_rows)
        try:
            run_import(job, dry_run=False)
            LOGGER.info('Import #%s finished: %s new, %s merged, %s rejected',
                        job.id, job.created_rows, job.merged_rows,
                        job.rejected_rows)
        except Exception as exc:
            LOGGER.exception('Import #%s failed', job.id)
            job.status = 'FAILED'
            job.error_log = str(exc)
            job.save(update_fields=['status', 'error_log'])
        done += 1
    return done


# --------------------------------------------------------------------------
# Lead generation
# --------------------------------------------------------------------------

def run_queued_generation(*, max_jobs: int = 1) -> int:
    from lead_generation.models import GenerationJob

    done = 0
    for job in GenerationJob.objects.filter(status='PENDING').order_by('id')[:max_jobs]:
        claimed = GenerationJob.objects.filter(pk=job.pk, status='PENDING').update(
            status='RUNNING', started_at=timezone.now(),
            heartbeat_at=timezone.now())
        if not claimed:
            continue
        LOGGER.info('Starting generation job #%s', job.id)
        try:
            from lead_generation.engine import run_generation_job
            run_generation_job(job.id)
        except Exception as exc:
            LOGGER.exception('Generation job #%s failed', job.id)
            GenerationJob.objects.filter(pk=job.pk).update(
                status='FAILED', error_message=str(exc)[:2000])
        done += 1
    return done


# --------------------------------------------------------------------------
# Recovery
# --------------------------------------------------------------------------

def recover_stale_jobs() -> int:
    """
    Release jobs whose worker died.

    Without this a job left RUNNING by a server restart blocks the user
    forever, with no way to clear it from the interface.
    """
    from datetime import timedelta

    from imports.models import ImportJob
    from lead_generation.models import GenerationJob

    cutoff = timezone.now() - timedelta(minutes=STALE_AFTER_MINUTES)
    recovered = 0

    stale_generation = GenerationJob.objects.filter(status='RUNNING').filter(
        models_q_stale(cutoff))
    for job in stale_generation:
        job.status = 'FAILED'
        job.error_message = (
            'The background worker stopped unexpectedly (server restart?). '
            'Nothing was lost — start a new run to continue.')
        job.note('Recovered by the worker: no heartbeat, marked as failed.')
        job.save(update_fields=['status', 'error_message', 'log', 'updated_at'])
        recovered += 1

    stale_imports = ImportJob.objects.filter(status='RUNNING',
                                             created_at__lt=cutoff)
    for job in stale_imports:
        job.status = 'FAILED'
        job.error_log = ('The background worker stopped unexpectedly. '
                         'Re-upload the file to try again.')
        job.save(update_fields=['status', 'error_log'])
        recovered += 1

    if recovered:
        LOGGER.warning('Recovered %d stalled job(s)', recovered)
    return recovered


def models_q_stale(cutoff):
    """RUNNING jobs with no heartbeat, or a heartbeat older than the cutoff."""
    from django.db.models import Q
    return Q(heartbeat_at__lt=cutoff) | Q(heartbeat_at__isnull=True,
                                          started_at__lt=cutoff)
