from django.urls import path

from . import views

app_name = 'followups'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('history/', views.history, name='history'),
    path('lead/<int:lead_id>/create/', views.create, name='create'),
    path('lead/<int:lead_id>/snooze/', views.snooze, name='snooze'),
    path('<int:followup_id>/complete/', views.complete, name='complete'),
]
