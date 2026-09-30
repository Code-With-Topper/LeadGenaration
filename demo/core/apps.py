from django.apps import AppConfig


class CoreConfig(AppConfig):
    """
    Shared data-quality code. No models of its own — it is registered as an app
    so its management commands (`run_worker`, `seed_reference_data`,
    `backfill_normalized`, `create_demo_data`) are discoverable.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
