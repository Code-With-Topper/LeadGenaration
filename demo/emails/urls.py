from django.urls import path

from . import views

app_name = 'emails'

urlpatterns = [
    path('compose/<int:lead_id>/', views.compose, name='compose'),
    path('preview/<int:lead_id>/<int:template_id>/', views.template_preview,
         name='template_preview'),
    path('log/', views.log_list, name='log_list'),
    path('templates/', views.template_list, name='template_list'),
    path('templates/new/', views.template_edit, name='template_create'),
    path('templates/<int:template_id>/', views.template_edit, name='template_edit'),
    path('templates/<int:template_id>/delete/', views.template_delete,
         name='template_delete'),
    path('suppression/', views.suppression_list, name='suppression_list'),
    path('unsubscribe/<str:token>/', views.unsubscribe, name='unsubscribe'),
]
