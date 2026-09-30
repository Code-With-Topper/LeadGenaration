from django.urls import path

from . import views

app_name = 'crm'

urlpatterns = [
    path('', views.pipeline, name='pipeline'),
    path('activity/', views.activity_log, name='activity_log'),
    path('<int:lead_id>/status/', views.update_status, name='update_status'),
    path('<int:lead_id>/note/', views.add_note, name='add_note'),
]
