from django.urls import path
from . import views

app_name = 'quotations'

urlpatterns = [
    path('list/', views.quotation_list, name='quotation_list'),
    path('create/<int:lead_id>/', views.create_quotation, name='create_quotation'),
    path('view/<int:quotation_id>/', views.view_quotation, name='view_quotation'),
    path('pdf/<int:quotation_id>/', views.generate_pdf, name='generate_pdf'),
    path('send/<int:quotation_id>/', views.send_quotation, name='send_quotation'),
]
