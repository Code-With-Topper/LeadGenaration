from django.urls import path

from . import views

app_name = 'imports'

urlpatterns = [
    path('', views.index, name='index'),
    path('upload/', views.upload, name='upload'),
    path('template/', views.sample_file, name='sample_file'),
    path('<int:job_id>/map/', views.map_columns, name='map_columns'),
    path('<int:job_id>/preview/', views.preview, name='preview'),
    path('<int:job_id>/commit/', views.commit, name='commit'),
    path('<int:job_id>/cancel/', views.cancel, name='cancel'),
    path('<int:job_id>/rejected.csv', views.download_rejected,
         name='download_rejected'),
]
