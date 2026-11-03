from django.urls import path
from eduapp import views

urlpatterns = [
    # Public
    path('health/', views.health_check, name='health_check'),
    path('', views.index, name='index'),
    path('events/', views.events_list, name='events'),
    path('events/create/', views.event_create, name='event_create'),
    path('events/<int:id>/edit/', views.event_edit, name='event_edit'),
    path('events/<int:id>/delete/', views.event_delete, name='event_delete'),
    path('events/<slug:slug>/', views.event_detail, name='event_detail'),
    path('events/<slug:slug>/support/', views.event_support, name='event_support'),
    path('services/', views.services, name='services'),
    path('terms/', views.terms, name='terms'),
    path('privacy/', views.privacy, name='privacy'),
    path('sponser/', views.sponser, name='sponser'),
    path('message/', views.message, name='message'),

    # Mentor application flow
    path('mentor/', views.mentor, name='mentor'),
    path('mentor/<int:mentor_id>/pay/', views.payment_initiate, name='payment_initiate'),
    path('payment/status/', views.payment_status, name='payment_status'),
    path('payment/check/', views.check_payment_status, name='check_payment_status'),
    path('mpesa/callback/', views.mpesa_callback, name='mpesa_callback'),

    # Mentor dashboard
    path('dashboard/', views.mentor_dashboard, name='mentor_dashboard'),

    # Forms
    path('learner/', views.support_application, name='learner'),
    path('patner/', views.patner, name='patner'),
    path('mentorship_booking/', views.mentorship_booking, name='mentorship_booking'),

    # Admin dashboard
    path('admin_view/', views.admin_view, name='admin_view'),
    path('approve_mentor/<int:id>/', views.approve_mentor, name='approve_mentor'),
    path('reject_mentor/<int:id>/', views.reject_mentor, name='reject_mentor'),
    path('delete_mentor/<int:id>/', views.delete_mentor, name='delete_mentor'),
    path('approve_learner/<int:id>/', views.approve_learner, name='approve_learner'),
    path('delete_learner/<int:id>/', views.delete_learner, name='delete_learner'),
    path('approve_partner/<int:id>/', views.approve_partner, name='approve_partner'),
    path('delete_partner/<int:id>/', views.delete_partner, name='delete_partner'),
    path('delete_message/<int:id>/', views.delete_message, name='delete_message'),

    # Auth
    path('login/', views.login_user, name='login'),
    path('logout/', views.logout_user, name='logout'),
    path('register/', views.register, name='register'),
]