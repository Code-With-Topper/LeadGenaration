from django.urls import path
from . import views

app_name = 'crm'

urlpatterns = [
    path('pipeline/', views.pipeline_view, name='pipeline'),
    path('pipeline/update/<int:lead_id>/', views.update_lead_status, name='update_lead_status'),
]
