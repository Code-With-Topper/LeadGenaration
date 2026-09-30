from django.urls import path

from . import views

app_name = 'lead_generation'

urlpatterns = [
    path('', views.index, name='index'),
    path('start/', views.start, name='start'),
    path('<int:job_id>/<str:action>/', views.control, name='control'),
    path('live-data/', views.live_data, name='live_data'),
    path('cities/', views.cities_for_district, name='cities'),
]
