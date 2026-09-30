from django.urls import path
from . import views

app_name = 'lead_generation'

urlpatterns = [
    path('', views.index, name='index'),
    path('live-data/', views.live_job_data, name='live_data'),
]
