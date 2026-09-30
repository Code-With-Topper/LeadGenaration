from django.db import models
from django.contrib.auth.models import User

class ImportJob(models.Model):
    file_name = models.CharField(max_length=255)
    file_path = models.FileField(upload_to='imports/')
    status = models.CharField(max_length=50, default='PENDING')
    total_rows = models.IntegerField(default=0)
    imported_rows = models.IntegerField(default=0)
    failed_rows = models.IntegerField(default=0)
    column_mapping = models.JSONField(blank=True, null=True)
    error_log = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Import Job #{self.id} - {self.file_name}"
