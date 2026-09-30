from django.urls import path

from . import views

app_name = 'leads'

urlpatterns = [
    path('', views.index, name='index'),
    path('export/', views.export_leads, name='export_leads'),
    path('companies/', views.companies_list, name='company_list'),
    path('contacts/', views.contacts_index, name='contacts_index'),
    path('recalculate-quality/', views.recalculate_quality,
         name='recalculate_quality'),

    path('review/', views.review_list, name='review_list'),
    path('review/<int:review_id>/', views.review_detail, name='review_detail'),
    path('review/<int:review_id>/resolve/', views.review_resolve,
         name='review_resolve'),

    path('<int:lead_id>/', views.detail, name='detail'),
    path('<int:lead_id>/update/', views.update_lead, name='update_lead'),
    path('<int:lead_id>/profile-sent/', views.mark_profile_sent,
         name='mark_profile_sent'),
]
