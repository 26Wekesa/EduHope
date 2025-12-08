from django.contrib import admin
from django.urls import path
from eduapp import views

urlpatterns = [
    path('', views.index, name='index'),
    
    path('events/', views.events, name='events'),

    path('services/', views.services, name='services'),

    path('terms/', views.terms, name='terms'),

    path('privacy/', views.privacy, name='privacy'),

    path('sponser/', views.sponser, name='sponser'),

    path('mentor/', views.mentor, name='mentor'),

    path('admin_view/', views.admin, name='admin_view'),

    path('patner/', views.patner, name='patner'),
    path('learner/', views.learner, name='learner'),

    path('message/', views.message, name='message'),
]