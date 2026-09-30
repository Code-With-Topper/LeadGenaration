from django.urls import path
from . import views

app_name = 'emails'

urlpatterns = [
    path('manual/<int:lead_id>/', views.send_manual_email, name='send_manual'),
    path('campaigns/', views.campaign_list, name='campaign_list'),
    path('campaigns/create/', views.campaign_create, name='campaign_create'),
    path('unsubscribe/<str:email_b64>/', views.unsubscribe, name='unsubscribe'),
]
