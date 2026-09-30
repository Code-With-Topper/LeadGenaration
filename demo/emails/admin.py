from django.contrib import admin

from .models import EmailLog, EmailQuota, EmailTemplate


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'subject', 'is_default')


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ('sent_at', 'recipient', 'subject', 'status')
    list_filter = ('status',)
    search_fields = ('recipient', 'subject')


@admin.register(EmailQuota)
class EmailQuotaAdmin(admin.ModelAdmin):
    list_display = ('date', 'sent_count')
