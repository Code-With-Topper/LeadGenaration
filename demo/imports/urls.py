from django.urls import path
from . import views

app_name = 'imports'

urlpatterns = [
    path('', views.import_index, name='index'),
    path('upload/', views.upload_file, name='upload'),
    path('map/<int:job_id>/', views.map_columns, name='map_columns'),
    path('process/<int:job_id>/', views.process_import, name='process_import'),
]
