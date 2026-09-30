from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dashboard.urls')),
    path('lead-generation/', include('lead_generation.urls')),
    path('leads/', include('leads.urls')),
    path('emails/', include('emails.urls')),
    path('followups/', include('followups.urls')),
    path('quotations/', include('quotations.urls')),
    path('crm/', include('crm.urls')),
    path('imports/', include('imports.urls')),
    path('reports/', include('reports.urls')),
]
