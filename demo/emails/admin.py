from django.contrib import admin
from .models import EmailTemplate, EmailCampaign, EmailLog

admin.site.register(EmailTemplate)
admin.site.register(EmailCampaign)
admin.site.register(EmailLog)
