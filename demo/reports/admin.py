from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'action', 'model_name', 'object_id',
                    'performed_by')
    list_filter = ('action',)
    search_fields = ('changes', 'object_id')
    readonly_fields = ('created_at',)
