from django.db import models


class ImportJob(models.Model):
    """
    One CSV/Excel upload.

    The flow is deliberately upload → map → **dry run** → commit. Nothing is
    written until the user has seen how many rows are new, how many merge into
    existing companies, and how many are rejected and why. An import that
    silently changes 10,000 rows is not something a non-technical user can
    trust.
    """
    STATUS_CHOICES = [
        ('UPLOADED', 'Uploaded'),
        ('MAPPED', 'Columns mapped'),
        ('PREVIEWED', 'Preview ready'),
        ('QUEUED', 'Queued'),
        ('RUNNING', 'Importing'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
    ]

    file_name = models.CharField(max_length=255)
    file_path = models.FileField(upload_to='imports/%Y/%m/')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
                              default='UPLOADED', db_index=True)

    detected_columns = models.JSONField(default=list, blank=True)
    column_mapping = models.JSONField(default=dict, blank=True)

    total_rows = models.IntegerField(default=0)
    created_rows = models.IntegerField(default=0)
    merged_rows = models.IntegerField(default=0)
    review_rows = models.IntegerField(default=0)
    rejected_rows = models.IntegerField(default=0)
    # Rows that were imported but had a field thrown away by validation.
    warned_rows = models.IntegerField(default=0)

    # Per-row outcome from the dry run, so the preview and the rejected-rows
    # download do not need to re-read the file.
    preview = models.JSONField(default=dict, blank=True)
    rejections = models.JSONField(default=list, blank=True)

    error_log = models.TextField(blank=True, default='')
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL,
                                   null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Import #{self.id} - {self.file_name} ({self.status})"

    @property
    def is_finished(self):
        return self.status in ('COMPLETED', 'FAILED', 'CANCELLED')

    @property
    def accepted_rows(self):
        return self.created_rows + self.merged_rows + self.review_rows
