from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('logs/', views.audit_logs, name='audit_logs'),
]
