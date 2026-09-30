from django.contrib import admin

from .models import FollowUp


@admin.register(FollowUp)
class FollowUpAdmin(admin.ModelAdmin):
    list_display = ('lead', 'date', 'follow_up_type', 'status', 'assigned_to')
    list_filter = ('status', 'follow_up_type')
    date_hierarchy = 'date'
