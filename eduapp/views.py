import json
import logging
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.db import OperationalError, connection
from django.utils import timezone
from django.utils.text import slugify
from django.conf import settings
from .models import (
    Message, Mentor, MpesaTransaction, Event,
    SupportApplication, PartnershipRequest,
    MentorshipBooking, Donation
)
from .mpesa import initiate_stk_push


TERMS_VERSION = '2026-09-16'
TERMS_SESSION_KEY = 'edulink_terms_accepted'
logger = logging.getLogger(__name__)


def terms_accepted(request):
    return request.session.get(TERMS_SESSION_KEY) == TERMS_VERSION


def require_terms(request):
    if terms_accepted(request):
        return None

    terms_url = reverse('terms')
    return redirect(f'{terms_url}?next={request.get_full_path()}')


# ─── Public Pages ───────────────────────────────────────────────────────────

def health_check(request):
    try:
        connection.ensure_connection()
    except OperationalError:
        logger.exception('Health check failed because the database is unavailable')
        return JsonResponse({'status': 'unhealthy'}, status=503)

    return JsonResponse({'status': 'ok'})


def index(request):
    featured_events = Event.objects.filter(is_featured=True, is_published=True)[:3]
    upcoming_events = Event.objects.filter(is_published=True).order_by('date')[:6]
    return render(request, 'index.html', {
        'featured_events': featured_events,
        'upcoming_events': upcoming_events,
    })


def events_list(request):
    now = timezone.now()
    upcoming_events = Event.objects.filter(is_published=True, date__gte=now).order_by('date')
    past_events = Event.objects.filter(is_published=True, date__lt=now).order_by('-date')
    featured_event = Event.objects.filter(is_published=True, is_featured=True).order_by('date').first()
    return render(request, 'events.html', {
        'upcoming_events': upcoming_events,
        'past_events': past_events,
        'featured_event': featured_event,
        'events': upcoming_events | past_events,
    })


def event_detail(request, slug):
    event = get_object_or_404(Event, slug=slug, is_published=True)
    return render(request, 'event_detail.html', {'event': event})


def event_support(request, slug):
    terms_redirect = require_terms(request)
    if terms_redirect:
        return terms_redirect

    event = get_object_or_404(Event, slug=slug, is_published=True)

    if request.method == 'POST':
        donor_name = request.POST.get('donor_name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        amount = request.POST.get('amount', '0').strip()
        donation_type = request.POST.get('donation_type', 'one_time')
        message = request.POST.get('message', '').strip()

        if not donor_name or not email or not phone or not amount:
            messages.error(request, 'Please complete all the required donation details.')
            return redirect('event_detail', slug=slug)

        try:
            amount_value = Decimal(amount)
        except Exception:
            messages.error(request, 'The donation amount is invalid.')
            return redirect('event_detail', slug=slug)

        Donation.objects.create(
            event=event,
            donor_name=donor_name,
            email=email,
            phone=phone,
            amount=amount_value,
            donation_type=donation_type,
            message=f"Support for event: {event.title}. {message}".strip(),
        )

        messages.success(
            request,
            f'Thank you for supporting {event.title}. Please pay KES {amount_value} using M-Pesa till {settings.MPESA_SHORTCODE}.'
        )
        return redirect('event_detail', slug=slug)

    return redirect('event_detail', slug=slug)


def services(request):
    return render(request, 'service-details.html')


def terms(request):
    if request.method == 'POST':
        if request.POST.get('accept_terms') != 'on':
            messages.error(request, 'Please read and accept the Terms and Conditions before continuing.')
            return render(request, 'terms.html', {'next_url': request.POST.get('next', '')})

        request.session[TERMS_SESSION_KEY] = TERMS_VERSION
        next_url = request.POST.get('next', '')
        if not url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            next_url = reverse('index')
        return redirect(next_url)

    next_url = request.GET.get('next', '')
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = ''
    return render(request, 'terms.html', {'next_url': next_url})


def privacy(request):
    return render(request, 'privacy.html')


def sponser(request):
    return render(request, 'sponser.html')


# ─── Contact ────────────────────────────────────────────────────────────────

def message(request):
    if request.method == 'POST':
        Message.objects.create(
            name=request.POST.get('name', ''),
            email=request.POST.get('email', ''),
            subject=request.POST.get('subject', ''),
            message=request.POST.get('message', ''),
        )
        messages.success(request, 'Your message has been sent. We will be in touch soon!')
        return redirect('/')
    return redirect('/')


# ─── Mentor Application ──────────────────────────────────────────────────────

def mentor(request):
    """Step 1: Fill application form."""
    terms_redirect = require_terms(request)
    if terms_redirect:
        return terms_redirect

    if request.method == 'POST':
        mentor_obj = Mentor.objects.create(
            name=request.POST.get('name', ''),
            email=request.POST.get('email', ''),
            phone=request.POST.get('phone', ''),
            organization=request.POST.get('organization', ''),
            years=request.POST.get('years', 0),
            subject=request.POST.get('subject', ''),
            message=request.POST.get('message', ''),
            status='payment_pending',
        )
        # Link to logged-in user if available
        if request.user.is_authenticated:
            mentor_obj.user = request.user
            mentor_obj.save()

        # Redirect to payment step
        return redirect('payment_initiate', mentor_id=mentor_obj.id)

    return render(request, 'mentor.html')


def payment_initiate(request, mentor_id):
    """Step 2: Fixed KES 100 mentor registration payment."""
    terms_redirect = require_terms(request)
    if terms_redirect:
        return terms_redirect

    mentor_obj = get_object_or_404(Mentor, id=mentor_id)
    fee = int(settings.MENTOR_APPLICATION_FEE)

    if request.method == 'POST':
        phone = request.POST.get('phone', '').strip()
        submitted_amount = request.POST.get('amount', fee)

        try:
            submitted_amount = int(submitted_amount)
        except (TypeError, ValueError):
            submitted_amount = fee

        if submitted_amount != fee:
            messages.error(request, f'Mentor registration fee is fixed at KES {fee}.')
            return render(request, 'payment_page.html', {'mentor': mentor_obj, 'fee': fee})

        if phone.startswith('0'):
            phone = '254' + phone[1:]
        elif phone.startswith('+'):
            phone = phone[1:]

        result = initiate_stk_push(
            phone_number=phone,
            amount=fee,
            account_ref=mentor_obj.name[:12],
            description='Mentor Application'
        )

        if result.get('ResponseCode') == '0':
            MpesaTransaction.objects.create(
                mentor=mentor_obj,
                phone_number=phone,
                amount=fee,
                checkout_request_id=result.get('CheckoutRequestID', ''),
                merchant_request_id=result.get('MerchantRequestID', ''),
            )
            request.session['checkout_request_id'] = result.get('CheckoutRequestID')
            request.session['mentor_id'] = mentor_id
            messages.success(request, 'Payment prompt sent to your phone. Enter your M-Pesa PIN.')
            return redirect('payment_status')
        else:
            error_msg = result.get('errorMessage', 'Payment initiation failed. Try again.')
            messages.error(request, error_msg)

    return render(request, 'payment_page.html', {
        'mentor': mentor_obj,
        'fee': fee,
    })


def payment_status(request):
    """Step 3: Polling page that checks payment status."""
    checkout_id = request.session.get('checkout_request_id')
    mentor_id = request.session.get('mentor_id')

    if not checkout_id:
        return redirect('mentor')

    try:
        transaction = MpesaTransaction.objects.get(checkout_request_id=checkout_id)
    except MpesaTransaction.DoesNotExist:
        return redirect('mentor')

    return render(request, 'payment_status.html', {
        'transaction': transaction,
        'mentor_id': mentor_id,
    })


def check_payment_status(request):
    """AJAX endpoint — called by payment_status.html every 5 seconds."""
    checkout_id = request.session.get('checkout_request_id')
    if not checkout_id:
        return JsonResponse({'status': 'error', 'message': 'No transaction found'})

    try:
        transaction = MpesaTransaction.objects.get(checkout_request_id=checkout_id)
    except MpesaTransaction.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Transaction not found'})

    return JsonResponse({
        'status': transaction.status,
        'receipt': transaction.mpesa_receipt_number,
        'description': transaction.result_description,
    })


@csrf_exempt
def mpesa_callback(request):
    """Daraja calls this URL after payment is completed/failed."""
    if request.method != 'POST':
        return JsonResponse({'ResultCode': 1, 'ResultDesc': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
        callback_data = data['Body']['stkCallback']
        checkout_request_id = callback_data['CheckoutRequestID']
        result_code = int(callback_data['ResultCode'])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        logger.warning('Received malformed M-Pesa callback')
        return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Invalid callback'}, status=400)

    transaction = MpesaTransaction.objects.filter(
        checkout_request_id=checkout_request_id
    ).select_related('mentor').first()
    if transaction is None:
        logger.warning('M-Pesa callback references unknown checkout %s', checkout_request_id)
        return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

    if transaction.status != 'pending':
        return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

    transaction.result_code = result_code
    transaction.result_description = callback_data.get('ResultDesc', '')
    if result_code == 0:
        callback_items = callback_data.get('CallbackMetadata', {}).get('Item', [])
        metadata = {item.get('Name'): item.get('Value') for item in callback_items}
        try:
            callback_amount = Decimal(str(metadata['Amount']))
            receipt_number = metadata['MpesaReceiptNumber']
        except (KeyError, ArithmeticError, TypeError, ValueError):
            logger.warning('Successful M-Pesa callback is missing amount or receipt')
            return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Incomplete callback'}, status=400)

        if callback_amount != transaction.amount or not receipt_number:
            logger.warning('M-Pesa callback amount/receipt does not match checkout %s', checkout_request_id)
            return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Payment details do not match'}, status=400)

        transaction.status = 'success'
        transaction.mpesa_receipt_number = str(receipt_number)
        transaction.completed_at = timezone.now()
        transaction.mentor.status = 'pending_review'
        transaction.mentor.save(update_fields=['status', 'updated_at'])
    else:
        transaction.status = 'failed'
        transaction.completed_at = timezone.now()

    transaction.save()
    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})


# ─── Mentor Dashboard ────────────────────────────────────────────────────────

@login_required
def mentor_dashboard(request):
    """Logged-in mentor sees their application status."""
    try:
        mentor_obj = Mentor.objects.get(user=request.user)
        transactions = mentor_obj.transactions.all()
    except Mentor.DoesNotExist:
        mentor_obj = None
        transactions = []

    return render(request, 'mentor_dashboard.html', {
        'mentor': mentor_obj,
        'transactions': transactions,
    })


# ─── Admin Dashboard ─────────────────────────────────────────────────────────

@login_required(login_url='login')
def admin_view(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        messages.error(request, 'Only authenticated staff users can access the admin dashboard.')
        return redirect('login')

    mentors = Mentor.objects.all().order_by('-created_at')
    applications = SupportApplication.objects.all().order_by('-submitted_at')
    partnerships = PartnershipRequest.objects.all().order_by('-submitted_at')
    all_messages = Message.objects.all().order_by('-created_at')
    events = Event.objects.all().order_by('-created_at')
    upcoming_events = events.filter(date__gte=timezone.now()).order_by('-created_at')
    past_events = events.filter(date__lt=timezone.now()).order_by('-date')
    bookings = MentorshipBooking.objects.all().order_by('-submitted_at')
    donations = Donation.objects.all().order_by('-submitted_at')
    transactions = MpesaTransaction.objects.select_related('mentor').order_by('-initiated_at')

    stats = {
        'total_mentors': mentors.count(),
        'approved_mentors': mentors.filter(status='approved').count(),
        'pending_mentors': mentors.filter(status='pending_review').count(),
        'total_applications': applications.count(),
        'total_events': events.count(),
        'successful_payments': MpesaTransaction.objects.filter(status='success').count(),
        'approved_learners': applications.filter(approved=True).count(),
        'approved_partners': partnerships.filter(approved=True).count(),
        'total_messages': all_messages.count(),
        'total_bookings': bookings.count(),
        'total_donations': donations.count(),
    }

    return render(request, 'admin_view.html', {
        'mentors': mentors,
        'applications': applications,
        'partnerships': partnerships,
        'all_partners': partnerships,
        'messages_list': all_messages,
        'all_messages': all_messages,
        'events': events,
        'upcoming_events': upcoming_events,
        'past_events': past_events,
        'bookings': bookings,
        'donations': donations,
        'transactions': transactions,
        'recent_transactions': transactions[:20],
        'stats': stats,
        'approved_learners': stats['approved_learners'],
        'approved_mentors': stats['approved_mentors'],
        'approved_partners': stats['approved_partners'],
        'total_messages': stats['total_messages'],
        'total_bookings': stats['total_bookings'],
        'total_donations': stats['total_donations'],
    })


def approve_mentor(request, id):
    mentor_obj = get_object_or_404(Mentor, id=id)
    mentor_obj.status = 'approved'
    mentor_obj.approved = True
    mentor_obj.save()
    messages.success(request, f'{mentor_obj.name} has been approved.')
    return redirect('admin_view')


def reject_mentor(request, id):
    mentor_obj = get_object_or_404(Mentor, id=id)
    mentor_obj.status = 'rejected'
    mentor_obj.approved = False
    mentor_obj.save()
    messages.warning(request, f'{mentor_obj.name} application was rejected.')
    return redirect('admin_view')


def delete_mentor(request, id):
    mentor_obj = get_object_or_404(Mentor, id=id)
    mentor_obj.delete()
    messages.success(request, 'Mentor deleted.')
    return redirect('admin_view')


def approve_learner(request, id):
    app = get_object_or_404(SupportApplication, id=id)
    app.approved = True
    app.save()
    messages.success(request, 'Application approved.')
    return redirect('admin_view')


def delete_learner(request, id):
    get_object_or_404(SupportApplication, id=id).delete()
    return redirect('admin_view')


def approve_partner(request, id):
    partner = get_object_or_404(PartnershipRequest, id=id)
    partner.approved = True
    partner.save()
    messages.success(request, 'Partnership request approved.')
    return redirect('admin_view')


def delete_partner(request, id):
    get_object_or_404(PartnershipRequest, id=id).delete()
    return redirect('admin_view')


def delete_message(request, id):
    get_object_or_404(Message, id=id).delete()
    return redirect('admin_view')


# ─── Events Admin ────────────────────────────────────────────────────────────

@login_required
def event_create(request):
    if not request.user.is_staff:
        return redirect('/')
    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()
        description = (request.POST.get('description') or '').strip()
        date_value = request.POST.get('date')
        location = (request.POST.get('location') or '').strip()

        if not title:
            messages.error(request, 'Event title is required.')
            return redirect('admin_view')
        if not date_value:
            messages.error(request, 'Event date is required.')
            return redirect('admin_view')
        if not location:
            messages.error(request, 'Event location is required.')
            return redirect('admin_view')

        slug_value = slugify(request.POST.get('slug') or title) or slugify(title)
        original_slug = slug_value
        slug_counter = 2
        while Event.objects.filter(slug=slug_value).exists():
            slug_value = f'{original_slug}-{slug_counter}'
            slug_counter += 1

        event = Event.objects.create(
            title=title,
            slug=slug_value,
            description=description,
            date=date_value,
            location=location,
            image=request.FILES.get('image'),
            is_featured=request.POST.get('is_featured') == 'on',
            is_published=request.POST.get('is_published') == 'on',
        )
        messages.success(request, f'Event "{event.title}" created successfully.')
        return redirect('admin_view')
    return render(request, 'event_form.html')


@login_required
def event_edit(request, id):
    if not request.user.is_staff:
        return redirect('/')
    event = get_object_or_404(Event, id=id)
    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()
        description = (request.POST.get('description') or '').strip()
        date_value = request.POST.get('date')
        location = (request.POST.get('location') or '').strip()

        if not title:
            messages.error(request, 'Event title is required.')
            return redirect('admin_view')
        if not date_value:
            messages.error(request, 'Event date is required.')
            return redirect('admin_view')
        if not location:
            messages.error(request, 'Event location is required.')
            return redirect('admin_view')

        event.title = title
        event.description = description
        event.date = date_value
        event.location = location
        event.is_featured = request.POST.get('is_featured') == 'on'
        event.is_published = request.POST.get('is_published') == 'on'

        slug_value = slugify(request.POST.get('slug') or '')
        if slug_value:
            candidate = slug_value
        else:
            candidate = slugify(event.title)

        slug_counter = 2
        original_slug = candidate
        while Event.objects.filter(slug=candidate).exclude(id=event.id).exists():
            candidate = f'{original_slug}-{slug_counter}'
            slug_counter += 1
        event.slug = candidate

        if request.FILES.get('image'):
            event.image = request.FILES.get('image')
        event.save()
        messages.success(request, 'Event updated.')
        return redirect('admin_view')
    return render(request, 'event_form.html', {'event': event})


@login_required
def event_delete(request, id):
    if not request.user.is_staff:
        return redirect('/')
    get_object_or_404(Event, id=id).delete()
    messages.success(request, 'Event deleted.')
    return redirect('admin_view')


# ─── Forms ───────────────────────────────────────────────────────────────────

def support_application(request):
    terms_redirect = require_terms(request)
    if terms_redirect:
        return terms_redirect

    if request.method == 'POST':
        SupportApplication.objects.create(
            learner_name=request.POST.get('learner_name'),
            dob=request.POST.get('dob'),
            grade=request.POST.get('grade'),
            school=request.POST.get('school'),
            support_type=request.POST.get('support_type'),
            situation_description=request.POST.get('situation_description'),
            guardian_name=request.POST.get('guardian_name'),
            relationship=request.POST.get('relationship'),
            guardian_contact=request.POST.get('guardian_contact'),
            address=request.POST.get('address'),
            informant_name=request.POST.get('informant_name'),
            informant_phone=request.POST.get('informant_phone'),
            informant_relationship=request.POST.get('informant_relationship'),
            informant_remarks=request.POST.get('informant_remarks'),
        )
        messages.success(request, 'Application submitted successfully.')
        return redirect('learner')
    return render(request, 'learner.html')


def patner(request):
    terms_redirect = require_terms(request)
    if terms_redirect:
        return terms_redirect

    if request.method == 'POST':
        PartnershipRequest.objects.create(
            org_name=request.POST.get('org_name'),
            partner_type=request.POST.get('partner_type'),
            email=request.POST.get('email'),
            phone=request.POST.get('phone'),
            website=request.POST.get('website'),
            partnership_area=request.POST.get('partnership_area'),
            proposal_description=request.POST.get('proposal_description'),
            reason=request.POST.get('reason'),
        )
        messages.success(request, 'Partnership request submitted.')
        return redirect('patner')
    return render(request, 'patner.html')


def mentorship_booking(request):
    terms_redirect = require_terms(request)
    if terms_redirect:
        return terms_redirect

    if request.method == 'POST':
        MentorshipBooking.objects.create(
            school_name=request.POST.get('school_name'),
            contact_person=request.POST.get('contact_person'),
            email=request.POST.get('email'),
            phone=request.POST.get('phone'),
            number_of_learners=request.POST.get('number_of_learners'),
            preferred_date=request.POST.get('preferred_date'),
            preferred_time=request.POST.get('preferred_time'),
            duration_hours=request.POST.get('duration_hours'),
            topics=request.POST.get('topics'),
            additional_notes=request.POST.get('additional_notes'),
        )
        messages.success(request, 'Booking submitted.')
        return redirect('mentorship_booking')
    return render(request, 'mentorship_booking.html')


# ─── Auth ────────────────────────────────────────────────────────────────────

def login_user(request):
    if request.user.is_authenticated:
        return redirect('/')
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            messages.success(request, 'Welcome back!')
            if user.is_superuser or user.is_staff:
                return redirect('admin_view')
            return redirect('mentor_dashboard')
        else:
            messages.error(request, 'Invalid username or password.')
    return render(request, 'login.html')


def logout_user(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('login')


def register(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        password2 = request.POST.get('password2')

        if password != password2:
            messages.error(request, 'Passwords do not match.')
            return render(request, 'register.html')

        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already taken.')
            return render(request, 'register.html')

        user = User.objects.create_user(username=username, email=email, password=password)
        login(request, user)
        messages.success(request, 'Account created! Welcome.')
        return redirect('mentor_dashboard')

    return render(request, 'register.html')