from .models import AuditLog

def log_audit(action, model_name, object_id, changes='', user=None):
    """Helper function to record system audit logs."""
    try:
        AuditLog.objects.create(
            action=action,
            model_name=model_name,
            object_id=str(object_id),
            changes=changes,
            performed_by=user
        )
    except Exception:
        pass
