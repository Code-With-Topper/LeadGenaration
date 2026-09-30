from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dashboard.urls')),
    path('leads/', include('leads.urls')),
    path('lead-generation/', include('lead_generation.urls')),
    path('crm/', include('crm.urls')),
    path('emails/', include('emails.urls')),
    path('followups/', include('followups.urls')),
    path('quotations/', include('quotations.urls')),
    path('imports/', include('imports.urls')),
    path('reports/', include('reports.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
