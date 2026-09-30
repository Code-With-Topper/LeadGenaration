from django.urls import path
from . import views

app_name = 'followups'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('lead/<int:lead_id>/create/', views.create_followup, name='create_followup'),
    path('<int:followup_id>/complete/', views.complete_followup, name='complete_followup'),
]
