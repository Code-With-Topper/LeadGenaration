from django.urls import path
from . import views

urlpatterns = [
    path('', views.public_home, name='public-home'),
    path('dashboard/', views.index, name='dashboard-index'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('privacy-policy/', views.privacy_policy, name='privacy'),
    path('terms/', views.terms, name='terms'),
    path('sitemap.xml', views.sitemap, name='sitemap'),
    path('robots.txt', views.robots, name='robots'),
]
