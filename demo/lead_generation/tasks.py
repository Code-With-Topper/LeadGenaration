"""
Background task runner using Python threading.
Replaces Celery/Redis for local development on Windows.
No external services (Redis, RabbitMQ) required.
"""
import threading
import logging

logger = logging.getLogger("lead_generation")


def run_generation_job_task(job_id):
    """
    Launch the lead generation job in a background thread.
    This replaces the Celery .delay() pattern entirely.
    The Django web request returns immediately while the
    scraper runs in the background.
    """
    from lead_generator import run_generation_job

    def _worker():
        try:
            logger.info(f"[Thread Worker] Starting generation job #{job_id}")
            run_generation_job(job_id)
            logger.info(f"[Thread Worker] Finished generation job #{job_id}")
        except Exception as e:
            logger.error(f"[Thread Worker] Job #{job_id} failed: {e}")
            # Update job status to FAILED
            try:
                import django
                django.setup()
                from lead_generation.models import GenerationJob
                job = GenerationJob.objects.get(id=job_id)
                job.status = 'FAILED'
                job.error_message = str(e)
                job.save()
            except Exception:
                pass

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    logger.info(f"[Thread Worker] Background thread launched for job #{job_id}")
