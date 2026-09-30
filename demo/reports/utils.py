import logging

LOGGER = logging.getLogger('reports')


def log_audit(action, model_name='', object_id='', changes='', user=None):
    """
    Record one audit entry.

    Never raises: an audit failure must not break the action the user was
    performing. It is logged instead, so a broken trail is still visible.
    """
    from .models import AuditLog

    try:
        return AuditLog.objects.create(
            action=action,
            model_name=model_name,
            object_id=str(object_id) if object_id else '',
            changes=changes,
            performed_by=user if (user is not None
                                  and getattr(user, 'is_authenticated', False))
            else None,
        )
    except Exception:
        LOGGER.warning('Could not write audit log for %s', action, exc_info=True)
        return None
