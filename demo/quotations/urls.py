from django.urls import path

from . import views

app_name = 'quotations'

urlpatterns = [
    path('', views.quotation_list, name='quotation_list'),
    path('create/<int:lead_id>/', views.create_quotation, name='create_quotation'),
    path('<int:quotation_id>/', views.view_quotation, name='view_quotation'),
    path('<int:quotation_id>/pdf/', views.generate_pdf, name='generate_pdf'),
    path('<int:quotation_id>/send/', views.send_quotation, name='send_quotation'),
    path('<int:quotation_id>/status/', views.update_status, name='update_status'),
]
