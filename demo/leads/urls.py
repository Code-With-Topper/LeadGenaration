from django.urls import path
from . import views

app_name = 'leads'

urlpatterns = [
    path('', views.index, name='index'),
    path('<int:lead_id>/', views.detail, name='detail'),
    path('contacts/', views.contacts_index, name='contacts_index'),
    path('companies/', views.companies_list, name='company_list'),
    path('export/', views.export_leads, name='export_leads'),
]
