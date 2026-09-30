from django.db import models


class AuditLog(models.Model):
    """Who changed what, and when. Written by `reports.utils.log_audit`."""

    action = models.CharField(max_length=100, db_index=True)
    model_name = models.CharField(max_length=100, blank=True, default='')
    object_id = models.CharField(max_length=50, blank=True, default='')
    changes = models.TextField(blank=True, default='')
    performed_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL,
                                     null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        who = self.performed_by.username if self.performed_by else 'system'
        return f'{self.action} by {who}'
