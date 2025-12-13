from django.contrib import admin
from django.urls import path
from eduapp import views

urlpatterns = [
    path('', views.index, name='index'),

    path('login/', views.login_user, name='login'),
    
    path('events/', views.events, name='events'),

    path('services/', views.services, name='services'),

    path('terms/', views.terms, name='terms'),

    path('privacy/', views.privacy, name='privacy'),

    path('sponser/', views.sponser, name='sponser'),

    path('mentor/', views.mentor, name='mentor'),

    path('admin_view/', views.admin, name='admin_view'),

    path('patner/', views.patner, name='patner'),

    path('mentorship_booking/', views.mentorship_booking, name='mentorship_booking'),
    
    path('learner/', views.support_application, name='learner'),

    path('message/', views.message, name='message'),

    path('register/', views.register, name='register'),
    
    path('delete/<int:id>/', views.delete, name='delete'),
    
    path('approve_mentor/<int:id>/', views.approve_mentor, name='approve_mentor'),
    path('delete_mentor/<int:id>/', views.delete_mentor, name='delete_mentor'),
    path('approve_learner/<int:id>/', views.approve_learner, name='approve_learner'),
    path('delete_learner/<int:id>/', views.delete_learner, name='delete_learner'),
    path('approve_partner/<int:id>/', views.approve_partner, name='approve_partner'),
    path('delete_partner/<int:id>/', views.delete_partner, name='delete_partner'),
    
]