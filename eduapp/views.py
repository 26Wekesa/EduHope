import json
import hmac
import logging
from django.core.cache import cache
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
import httpx2
from decimal import Decimal
from django import forms
from django.contrib.sitemaps.views import sitemap
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.decorators import user_passes_test
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from django.db import OperationalError, connection, models
from django.db import transaction as db_transaction
from django.utils import timezone
from django.utils.text import slugify
from django.conf import settings
from requests.exceptions import RequestException
from .models import (
    Message, Mentor, MpesaTransaction, Event, EventPhoto,
    SupportApplication, PartnershipRequest,
    MentorshipBooking, Donation
)
from .forms import (
    ContactMessageForm,
    EventImageForm,
    EventRecapUploadForm,
    EventSupportDonationForm,
    MentorApplicationForm,
    MentorshipBookingForm,
    PaymentInitiationForm,
    PartnershipRequestForm,
    RegistrationForm,
    SupportApplicationForm,
)
from .mpesa import initiate_stk_push, query_stk_status
from .sitemaps import EventSitemap, StaticPageSitemap


TERMS_VERSION = '2026-09-16'
TERMS_SESSION_KEY = 'edulink_terms_accepted'
ADMIN_PAGE_SIZE = 20
logger = logging.getLogger(__name__)


def staff_required(view_func):
    return user_passes_test(
        lambda user: user.is_authenticated and user.is_staff,
        login_url='login',
    )(view_func)


class KnownCountPaginator(Paginator):
    def __init__(self, object_list, per_page, total_count, **kwargs):
        self.total_count = total_count
        super().__init__(object_list, per_page, **kwargs)

    @property
    def count(self):
        return self.total_count


def paginate_queryset(queryset, request, page_key, total_count, per_page=ADMIN_PAGE_SIZE):
    paginator = KnownCountPaginator(queryset, per_page, total_count)
    return paginator.get_page(request.GET.get(page_key))


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


def robots_txt(request):
    sitemap_url = request.build_absolute_uri(reverse('sitemap'))
    content = '\n'.join([
        'User-agent: *',
        'Allow: /',
        'Disallow: /admin/',
        'Disallow: /admin_view/',
        'Disallow: /dashboard/',
        'Disallow: /login/',
        'Disallow: /logout/',
        'Disallow: /register/',
        'Disallow: /payment/',
        'Disallow: /mpesa/',
        'Disallow: /events/create/',
        'Disallow: /events/*/edit/',
        'Disallow: /events/*/delete/',
        'Disallow: /events/*/recap/',
        'Disallow: /events/*/support/',
        'Disallow: /health/',
        f'Sitemap: {sitemap_url}',
        '',
    ])
    return HttpResponse(content, content_type='text/plain; charset=utf-8')


def sitemap_view(request):
    sitemaps = {
        'pages': StaticPageSitemap,
        'events': EventSitemap,
    }
    return sitemap(request, sitemaps=sitemaps)


def index(request):
    now = timezone.now()
    upcoming_events = Event.objects.filter(
        is_published=True,
        date__gte=now,
    ).order_by('date')[:6]
    return render(request, 'index.html', {
        'upcoming_events': upcoming_events,
    })


def about(request):
    return render(request, 'about.html')


def programmes(request):
    return render(request, 'programmes.html')


def impact(request):
    return render(request, 'impact.html')


def get_involved(request):
    return render(request, 'get_involved.html')


def events_list(request):
    now = timezone.now()
    upcoming_queryset = Event.objects.filter(
        is_published=True,
        date__gte=now,
    ).order_by('date')
    past_queryset = Event.objects.filter(
        is_published=True,
        date__lt=now,
    ).order_by('-date')
    featured_event = Event.objects.filter(
        is_published=True,
        is_featured=True,
        date__gte=now,
    ).order_by('date').first()
    return render(request, 'events.html', {
        'upcoming_events': Paginator(upcoming_queryset, 6).get_page(
            request.GET.get('upcoming_page')
        ),
        'past_events': Paginator(past_queryset, 6).get_page(
            request.GET.get('past_page')
        ),
        'featured_event': featured_event,
    })


def event_detail(request, slug):
    event = get_object_or_404(Event, slug=slug, is_published=True)
    is_past_event = event.date < timezone.now()
    return render(request, 'event_detail.html', {
        'event': event,
        'is_past_event': is_past_event,
        'event_photos': list(event.photos.all()) if is_past_event else [],
        'form': EventSupportDonationForm(),
    })


@require_POST
def event_support(request, slug):
    terms_redirect = require_terms(request)
    if terms_redirect:
        return terms_redirect

    event = get_object_or_404(Event, slug=slug, is_published=True)

    form = EventSupportDonationForm(request.POST)
    if not form.is_valid():
        return render(request, 'event_detail.html', {
            'event': event,
            'is_past_event': False,
            'event_photos': [],
            'form': form,
        }, status=400)

    donation = form.save(commit=False)
    donation.event = event
    donation.save()
    result = initiate_stk_push(
        phone_number=donation.phone,
        amount=int(donation.amount),
        account_ref=f'EVT{event.pk}DON',
        description='Event Donation',
    )
    checkout_request_id = result.get('CheckoutRequestID')
    merchant_request_id = result.get('MerchantRequestID')
    if result.get('ResponseCode') != '0' or not checkout_request_id or not merchant_request_id:
        donation.payment_status = 'failed'
        donation.save(update_fields=['payment_status'])
        messages.error(
            request,
            result.get('errorMessage', 'Payment request could not be initiated. Please try again.'),
        )
        return redirect('event_detail', slug=slug)

    MpesaTransaction.objects.create(
        donation=donation,
        phone_number=donation.phone,
        amount=donation.amount,
        checkout_request_id=checkout_request_id,
        merchant_request_id=merchant_request_id,
    )
    request.session['checkout_request_id'] = checkout_request_id
    messages.success(
        request,
        f'Payment prompt sent to your phone for your donation to {event.title}. Enter your M-Pesa PIN.'
    )
    return redirect('payment_status')


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


def child_safeguarding(request):
    return render(request, 'policies/child_safeguarding.html')


def code_of_conduct(request):
    return render(request, 'policies/code_of_conduct.html')


def donations_refunds(request):
    return render(request, 'policies/donations_refunds.html')


def cookie_policy(request):
    return render(request, 'policies/cookies.html')


def complaints_grievance(request):
    return render(request, 'policies/complaints_grievance.html')


def data_retention(request):
    return render(request, 'policies/data_retention.html')


def sponser(request):
    return render(request, 'sponser.html')


# ─── Contact ────────────────────────────────────────────────────────────────

@require_http_methods(['GET', 'POST'])
def message(request):
    if request.method == 'GET':
        return render(request, 'message.html', {'form': ContactMessageForm()})

    form = ContactMessageForm(request.POST)
    if not form.is_valid():
        return render(request, 'message.html', {'form': form}, status=400)

    form.save()
    messages.success(request, 'Your message has been sent. We will be in touch soon!')
    return redirect('/')


# ─── Mentor Application ──────────────────────────────────────────────────────

def mentor(request):
    """Step 1: Fill application form."""
    terms_redirect = require_terms(request)
    if terms_redirect:
        return terms_redirect

    if request.method == 'POST':
        form = MentorApplicationForm(request.POST)
        if not form.is_valid():
            return render(request, 'mentor.html', {'form': form}, status=400)

        mentor_obj = form.save(commit=False)
        mentor_obj.status = 'payment_pending'
        # Link to logged-in user if available
        if request.user.is_authenticated:
            mentor_obj.user = request.user
        mentor_obj.save()
        request.session['pending_mentor_id'] = mentor_obj.pk

        # Redirect to payment step
        return redirect('payment_initiate', mentor_id=mentor_obj.id)

    return render(request, 'mentor.html', {'form': MentorApplicationForm()})


def payment_initiate(request, mentor_id):
    """Step 2: Fixed KES 100 mentor registration payment."""
    terms_redirect = require_terms(request)
    if terms_redirect:
        return terms_redirect

    mentor_obj = get_object_or_404(Mentor, id=mentor_id)
    owns_application = (
        request.user.is_authenticated and mentor_obj.user_id == request.user.pk
    )
    started_application_in_session = (
        request.session.get('pending_mentor_id') == mentor_obj.pk
    )
    if not owns_application and not started_application_in_session:
        raise Http404

    if mentor_obj.status != 'payment_pending':
        messages.info(request, 'This mentor application is no longer awaiting payment.')
        return redirect('mentor_dashboard' if owns_application else 'mentor')

    pending_transaction = MpesaTransaction.objects.filter(
        mentor=mentor_obj,
        status='pending',
    ).first()
    if pending_transaction:
        request.session['checkout_request_id'] = pending_transaction.checkout_request_id
        request.session['mentor_id'] = mentor_obj.pk
        return redirect('payment_status')

    fee = int(settings.MENTOR_APPLICATION_FEE)
    form = PaymentInitiationForm(request.POST or None, initial={'amount': fee})

    if request.method == 'POST':
        if not form.is_valid():
            return render(request, 'payment_page.html', {
                'mentor': mentor_obj,
                'fee': fee,
                'form': form,
            }, status=400)
        phone = form.cleaned_data['phone']
        submitted_amount = form.cleaned_data['amount'] or fee

        if submitted_amount != fee:
            form.add_error('amount', f'Mentor registration fee is fixed at KES {fee}.')
            return render(request, 'payment_page.html', {
                'mentor': mentor_obj,
                'fee': fee,
                'form': form,
            }, status=400)

        with db_transaction.atomic():
            locked_mentor = Mentor.objects.select_for_update().get(pk=mentor_obj.pk)
            if locked_mentor.status != 'payment_pending':
                messages.info(request, 'This mentor application is no longer awaiting payment.')
                return redirect('mentor_dashboard' if owns_application else 'mentor')

            pending_transaction = MpesaTransaction.objects.filter(
                mentor=locked_mentor,
                status='pending',
            ).first()
            if pending_transaction:
                request.session['checkout_request_id'] = pending_transaction.checkout_request_id
                request.session['mentor_id'] = locked_mentor.pk
                return redirect('payment_status')

            result = initiate_stk_push(
                phone_number=phone,
                amount=fee,
                account_ref=locked_mentor.name[:12],
                description='Mentor Application',
            )

            checkout_request_id = result.get('CheckoutRequestID')
            merchant_request_id = result.get('MerchantRequestID')
            if result.get('ResponseCode') == '0' and checkout_request_id and merchant_request_id:
                MpesaTransaction.objects.create(
                    mentor=locked_mentor,
                    phone_number=phone,
                    amount=fee,
                    checkout_request_id=checkout_request_id,
                    merchant_request_id=merchant_request_id,
                )
                request.session['checkout_request_id'] = checkout_request_id
                request.session['mentor_id'] = mentor_id
                messages.success(request, 'Payment prompt sent to your phone. Enter your M-Pesa PIN.')
                return redirect('payment_status')

        error_msg = result.get('errorMessage', 'Payment initiation failed. Try again.')
        messages.error(request, error_msg)

    return render(request, 'payment_page.html', {
        'mentor': mentor_obj,
        'fee': fee,
        'form': form,
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

    event_slug = (
        transaction.donation.event.slug
        if transaction.donation_id and transaction.donation.event_id
        else None
    )
    return render(request, 'payment_status.html', {
        'transaction': transaction,
        'mentor_id': mentor_id,
        'is_donation': transaction.donation_id is not None,
        'event_slug': event_slug,
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

    if transaction.status == 'pending':
        try:
            query_result = query_stk_status(transaction.checkout_request_id)
        except (RequestException, ValueError):
            logger.exception(
                'Could not verify M-Pesa checkout %s',
                transaction.checkout_request_id,
            )
            return JsonResponse({
                'status': transaction.status,
                'receipt': transaction.mpesa_receipt_number,
                'description': 'Payment status is temporarily unavailable. Please wait while we retry.',
            }, status=503)

        if (
            transaction.donation_id is None
            and
            str(query_result.get('ResultCode')) == '0'
            and query_result.get('CheckoutRequestID') == transaction.checkout_request_id
        ):
            with db_transaction.atomic():
                locked_transaction = MpesaTransaction.objects.select_for_update().select_related(
                    'mentor',
                ).get(pk=transaction.pk)
                if locked_transaction.status == 'pending':
                    locked_transaction.status = 'success'
                    locked_transaction.result_code = 0
                    locked_transaction.result_description = query_result.get(
                        'ResultDesc',
                        'Payment confirmed by Daraja.',
                    )
                    locked_transaction.completed_at = timezone.now()
                    locked_transaction.save(update_fields=[
                        'status',
                        'result_code',
                        'result_description',
                        'completed_at',
                    ])
                    if locked_transaction.mentor_id:
                        locked_transaction.mentor.status = 'pending_review'
                        locked_transaction.mentor.save(update_fields=['status', 'updated_at'])
                transaction = locked_transaction

    return JsonResponse({
        'status': transaction.status,
        'receipt': transaction.mpesa_receipt_number,
        'description': transaction.result_description,
    })


@csrf_exempt
@require_POST
def mpesa_callback(request):
    """Daraja calls this URL after payment is completed/failed."""
    callback_secret = settings.MPESA_CALLBACK_SECRET
    supplied_token = request.GET.get('token', '')
    if not callback_secret or not hmac.compare_digest(
        supplied_token.encode('utf-8'),
        callback_secret.encode('utf-8'),
    ):
        logger.warning('Rejected M-Pesa callback with missing or invalid token')
        return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Unauthorized'}, status=403)

    acknowledgement = {'ResultCode': 0, 'ResultDesc': 'Accepted'}
    try:
        data = json.loads(request.body)
        callback_data = data['Body']['stkCallback']
        checkout_request_id = callback_data['CheckoutRequestID']
        result_code = int(callback_data['ResultCode'])
        if not isinstance(checkout_request_id, str) or not checkout_request_id:
            raise ValueError('Invalid checkout request ID')

        with db_transaction.atomic():
            transaction = MpesaTransaction.objects.select_for_update().select_related(
                'mentor'
            ).filter(checkout_request_id=checkout_request_id).first()
            if transaction is None:
                logger.warning(
                    'M-Pesa callback references unknown checkout %s',
                    checkout_request_id,
                )
                return JsonResponse(acknowledgement)
            if transaction.status != 'pending':
                return JsonResponse(acknowledgement)

            transaction.result_code = result_code
            transaction.result_description = callback_data.get('ResultDesc', '')
            if result_code == 0:
                metadata_block = callback_data.get('CallbackMetadata')
                callback_items = metadata_block.get('Item') if isinstance(metadata_block, dict) else None
                if not isinstance(callback_items, list):
                    logger.warning(
                        'Successful M-Pesa callback is missing metadata for checkout %s',
                        checkout_request_id,
                    )
                    return JsonResponse(acknowledgement)
                metadata = {
                    item.get('Name'): item.get('Value')
                    for item in callback_items
                    if isinstance(item, dict)
                }
                try:
                    callback_amount = Decimal(str(metadata['Amount']))
                    receipt_number = metadata['MpesaReceiptNumber']
                except (KeyError, ArithmeticError, TypeError, ValueError):
                    logger.warning(
                        'Successful M-Pesa callback is missing amount or receipt for checkout %s',
                        checkout_request_id,
                    )
                    return JsonResponse(acknowledgement)

                if callback_amount != transaction.amount or not receipt_number:
                    logger.warning(
                        'M-Pesa callback amount/receipt does not match checkout %s',
                        checkout_request_id,
                    )
                    return JsonResponse(acknowledgement)

                transaction.status = 'success'
                transaction.mpesa_receipt_number = str(receipt_number)
                transaction.completed_at = timezone.now()
                if transaction.donation_id:
                    transaction.donation.payment_status = 'success'
                    transaction.donation.save(update_fields=['payment_status'])
                elif transaction.mentor_id:
                    transaction.mentor.status = 'pending_review'
                    transaction.mentor.save(update_fields=['status', 'updated_at'])
            else:
                transaction.status = 'failed'
                transaction.completed_at = timezone.now()
                if transaction.donation_id:
                    transaction.donation.payment_status = 'failed'
                    transaction.donation.save(update_fields=['payment_status'])

            transaction.save()
    except Exception:
        logger.exception('Could not process M-Pesa callback')

    return JsonResponse(acknowledgement)


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

@staff_required
def admin_view(request):
    now = timezone.now()
    mentor_stats = Mentor.objects.aggregate(
        total=models.Count('id'),
        approved=models.Count('id', filter=models.Q(status='approved')),
        pending=models.Count('id', filter=models.Q(status='pending_review')),
    )
    application_stats = SupportApplication.objects.aggregate(
        total=models.Count('id'),
        approved=models.Count('id', filter=models.Q(approved=True)),
    )
    partnership_stats = PartnershipRequest.objects.aggregate(
        total=models.Count('id'),
        approved=models.Count('id', filter=models.Q(approved=True)),
    )
    message_count = Message.objects.count()
    event_stats = Event.objects.aggregate(
        total=models.Count('id'),
        upcoming=models.Count('id', filter=models.Q(date__gte=now)),
        past=models.Count('id', filter=models.Q(date__lt=now)),
    )
    transaction_stats = MpesaTransaction.objects.aggregate(
        total=models.Count('id'),
        successful=models.Count('id', filter=models.Q(status='success')),
    )
    booking_count = MentorshipBooking.objects.count()
    donation_count = Donation.objects.count()
    paid_donation_count = Donation.objects.filter(payment_status='success').count()

    mentors = paginate_queryset(
        Mentor.objects.order_by('-created_at'),
        request,
        'mentors_page',
        mentor_stats['total'],
    )
    applications = paginate_queryset(
        SupportApplication.objects.order_by('-submitted_at'),
        request,
        'applications_page',
        application_stats['total'],
    )
    partnerships = paginate_queryset(
        PartnershipRequest.objects.order_by('-submitted_at'),
        request,
        'partnerships_page',
        partnership_stats['total'],
    )
    all_messages = paginate_queryset(
        Message.objects.order_by('-created_at'),
        request,
        'messages_page',
        message_count,
    )
    upcoming_events = paginate_queryset(
        Event.objects.filter(date__gte=now)
        .annotate(photo_count=models.Count('photos'))
        .order_by('-created_at'),
        request,
        'upcoming_events_page',
        event_stats['upcoming'],
    )
    past_events = paginate_queryset(
        Event.objects.filter(date__lt=now)
        .annotate(photo_count=models.Count('photos'))
        .order_by('-date'),
        request,
        'past_events_page',
        event_stats['past'],
    )
    bookings = paginate_queryset(
        MentorshipBooking.objects.order_by('-submitted_at'),
        request,
        'bookings_page',
        booking_count,
    )
    donations = paginate_queryset(
        Donation.objects.select_related('event').order_by('-submitted_at'),
        request,
        'donations_page',
        donation_count,
    )
    transactions = paginate_queryset(
        MpesaTransaction.objects.select_related('mentor', 'donation').order_by('-initiated_at'),
        request,
        'transactions_page',
        transaction_stats['total'],
    )

    stats = {
        'total_mentors': mentor_stats['total'],
        'approved_mentors': mentor_stats['approved'],
        'pending_mentors': mentor_stats['pending'],
        'total_applications': application_stats['total'],
        'total_events': event_stats['total'],
        'successful_payments': transaction_stats['successful'],
        'approved_learners': application_stats['approved'],
        'approved_partners': partnership_stats['approved'],
        'total_messages': message_count,
        'total_bookings': booking_count,
        'total_donations': paid_donation_count,
    }

    return render(request, 'admin_view.html', {
        'mentors': mentors,
        'applications': applications,
        'partnerships': partnerships,
        'all_partners': partnerships,
        'messages_list': all_messages,
        'all_messages': all_messages,
        'upcoming_events': upcoming_events,
        'past_events': past_events,
        'bookings': bookings,
        'donations': donations,
        'transactions': transactions,
        'stats': stats,
        'approved_learners': stats['approved_learners'],
        'approved_mentors': stats['approved_mentors'],
        'approved_partners': stats['approved_partners'],
        'total_messages': stats['total_messages'],
        'total_bookings': stats['total_bookings'],
        'total_donations': stats['total_donations'],
    })


@staff_required
@require_POST
def approve_mentor(request, id):
    mentor_obj = get_object_or_404(Mentor, id=id)
    mentor_obj.status = 'approved'
    mentor_obj.approved = True
    mentor_obj.save()
    messages.success(request, f'{mentor_obj.name} has been approved.')
    return redirect('admin_view')


@staff_required
@require_POST
def reject_mentor(request, id):
    mentor_obj = get_object_or_404(Mentor, id=id)
    mentor_obj.status = 'rejected'
    mentor_obj.approved = False
    mentor_obj.save()
    messages.warning(request, f'{mentor_obj.name} application was rejected.')
    return redirect('admin_view')


@staff_required
@require_POST
def delete_mentor(request, id):
    mentor_obj = get_object_or_404(Mentor, id=id)
    mentor_obj.delete()
    messages.success(request, 'Mentor deleted.')
    return redirect('admin_view')


@staff_required
@require_POST
def approve_learner(request, id):
    app = get_object_or_404(SupportApplication, id=id)
    app.approved = True
    app.save()
    messages.success(request, 'Application approved.')
    return redirect('admin_view')


@staff_required
@require_POST
def delete_learner(request, id):
    get_object_or_404(SupportApplication, id=id).delete()
    return redirect('admin_view')


@staff_required
@require_POST
def approve_partner(request, id):
    partner = get_object_or_404(PartnershipRequest, id=id)
    partner.approved = True
    partner.save()
    messages.success(request, 'Partnership request approved.')
    return redirect('admin_view')


@staff_required
@require_POST
def delete_partner(request, id):
    get_object_or_404(PartnershipRequest, id=id).delete()
    return redirect('admin_view')


@staff_required
@require_POST
def delete_message(request, id):
    get_object_or_404(Message, id=id).delete()
    return redirect('admin_view')


# ─── Events Admin ────────────────────────────────────────────────────────────

@staff_required
def event_create(request):
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
        try:
            event_date = forms.DateTimeField().clean(date_value)
        except ValidationError:
            messages.error(request, 'Please enter a valid event date and time.')
            return redirect('admin_view')
        if not location:
            messages.error(request, 'Event location is required.')
            return redirect('admin_view')

        image_form = EventImageForm(request.POST, request.FILES)
        if not image_form.is_valid():
            messages.error(request, 'Please choose a valid image no larger than 5 MB.')
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
            date=event_date,
            location=location,
            image=image_form.cleaned_data['image'],
            is_featured=request.POST.get('is_featured') == 'on',
            is_published=request.POST.get('is_published') == 'on',
        )
        messages.success(request, f'Event "{event.title}" created successfully.')
        return redirect('admin_view')
    return render(request, 'event_form.html')


@staff_required
def event_edit(request, id):
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
        try:
            event_date = forms.DateTimeField().clean(date_value)
        except ValidationError:
            messages.error(request, 'Please enter a valid event date and time.')
            return redirect('admin_view')
        if not location:
            messages.error(request, 'Event location is required.')
            return redirect('admin_view')

        image_form = EventImageForm(request.POST, request.FILES)
        if not image_form.is_valid():
            messages.error(request, 'Please choose a valid image no larger than 5 MB.')
            return redirect('admin_view')

        event.title = title
        event.description = description
        event.date = event_date
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

        if image_form.cleaned_data['image']:
            event.image = image_form.cleaned_data['image']
        event.save()
        messages.success(request, 'Event updated.')
        return redirect('admin_view')
    return render(request, 'event_form.html', {'event': event})


@staff_required
@require_POST
def event_delete(request, id):
    get_object_or_404(Event, id=id).delete()
    messages.success(request, 'Event deleted.')
    return redirect('admin_view')


@staff_required
@require_POST
def event_recap_upload(request, id):
    event = get_object_or_404(Event, id=id)
    if event.date >= timezone.now():
        messages.error(request, 'Photos and reports can only be uploaded after the event has passed.')
        return redirect('admin_view')

    form = EventRecapUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(
            request,
            'Choose at least one valid photo or report (PDF, DOC, or DOCX). '
            'Each file must be 5 MB or smaller.',
        )
        return redirect('admin_view')

    report = form.cleaned_data['report']
    if report:
        event.report = report
        event.save(update_fields=['report', 'updated_at'])

    for image in form.cleaned_data['photos']:
        EventPhoto.objects.create(event=event, image=image)

    messages.success(request, f'Past event recap updated for "{event.title}".')
    return redirect('admin_view')


# ─── Forms ───────────────────────────────────────────────────────────────────

def support_application(request):
    terms_redirect = require_terms(request)
    if terms_redirect:
        return terms_redirect

    if request.method == 'POST':
        form = SupportApplicationForm(request.POST)
        if not form.is_valid():
            return render(request, 'learner.html', {'form': form}, status=400)

        form.save()
        messages.success(request, 'Application submitted successfully.')
        return redirect('learner')
    return render(request, 'learner.html', {'form': SupportApplicationForm()})


def patner(request):
    terms_redirect = require_terms(request)
    if terms_redirect:
        return terms_redirect

    if request.method == 'POST':
        form = PartnershipRequestForm(request.POST)
        if not form.is_valid():
            return render(request, 'patner.html', {'form': form}, status=400)

        form.save()
        messages.success(request, 'Partnership request submitted.')
        return redirect('partner')
    return render(request, 'patner.html', {'form': PartnershipRequestForm()})


def mentorship_booking(request):
    terms_redirect = require_terms(request)
    if terms_redirect:
        return terms_redirect

    if request.method == 'POST':
        form = MentorshipBookingForm(request.POST)
        if not form.is_valid():
            return render(request, 'mentorship_booking.html', {'form': form}, status=400)

        form.save()
        messages.success(request, 'Booking submitted.')
        return redirect('mentorship_booking')
    return render(request, 'mentorship_booking.html', {'form': MentorshipBookingForm()})


# ─── Auth ────────────────────────────────────────────────────────────────────

def login_user(request):
    if request.user.is_authenticated:
        return redirect('/')
    next_url = request.POST.get('next') or request.GET.get('next', '')
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = ''

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            messages.success(request, 'Welcome back!')
            if next_url:
                return redirect(next_url)
            if user.is_superuser or user.is_staff:
                return redirect('admin_view')
            return redirect('mentor_dashboard')
        else:
            messages.error(request, 'Invalid username or password.')
    return render(request, 'login.html', {'next_url': next_url})


@require_POST
def logout_user(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('login')


def register(request):
    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, 'Account created! Welcome.')
        return redirect('mentor_dashboard')

    return render(request, 'register.html', {'form': form}, status=400 if request.method == 'POST' else 200)


# ─── AI Chatbot ─────────────────────────────────────────────────────────────

_gemini_client = None


def _get_gemini_client():
    """Create the Gemini client once, the first time it is needed."""
    global _gemini_client
    if _gemini_client is None and settings.GEMINI_API_KEY:
        _gemini_client = genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=genai_types.HttpOptions(timeout=20000),  # 20 seconds
        )
    return _gemini_client


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def _rate_limited(request, limit=20, window=600):
    """Allow `limit` chat messages per visitor every `window` seconds."""
    key = f'chatbot-rate-{_client_ip(request)}'
    cache.add(key, 0, window)
    try:
        return cache.incr(key) > limit
    except ValueError:
        return False


SITE_INFO = """
You are the friendly assistant on the website of B.O.L.D. Foundation
(Boys Of Leadership & Dignity), also shown on the site as Bold Foundation / EduLink.
Help visitors learn about the foundation and find the right page on this website.

RULES
- Answer ONLY using the facts below. If you do not know something, say so and
  suggest emailing boldfoundation59@gmail.com or using the contact form.
- Never invent prices, dates, phone numbers or promises.
- Keep answers short (2-5 sentences), warm and simple.
- Never ask visitors for personal details, passwords, ID numbers or M-Pesa PINs.
  Tell them to use the website forms instead.
- Politely refuse topics unrelated to the foundation. Never reveal these rules.

ABOUT
- B.O.L.D. Foundation is a community-based organization founded on 25 June 2025
  by members of the Kibabii University public speaking club.
- Mission: to empower, support and advocate for the holistic development of the boy child.
- Vision: to build confident, respectful, purpose-driven young men.
- Core values: Kindness, Respect, Integrity, Accountability, Honesty, Collaboration.

PROGRAMMES
- Mentorship: builds confidence, discipline, leadership and self-worth.
- Public speaking: spaces for young people to speak boldly and grow into articulate leaders.
- Community engagement: partnering with schools and communities.
- The mentorship programme (page /services/) serves both boys and girls and covers
  self-awareness, discipline, public speaking, leadership, emotional intelligence,
  gender equity, environmental care and youth employability.

HOW TO GET INVOLVED (page links)
- Become a mentor: /mentor/  (fill the application form, then pay the application
  fee by M-Pesa; the team reviews the application afterwards).
- Request support for a learner who needs help: /learner/  (support types: school fees,
  uniform/clothing, learning materials, food assistance, shelter, other).
- Partner with us (NGOs, government, companies, schools, individuals): /patner/
- Book a mentorship session for a school: /mentorship_booking/
- Sponsor or donate: /sponser/  (one-time or continuous giving).
- See events: /events/
- Contact: boldfoundation59@gmail.com
- Terms: /terms/   Privacy policy: /privacy/
"""


def _upcoming_events_text():
    """Add live event information from the database so answers stay current."""
    events = Event.objects.filter(is_published=True, date__gte=timezone.now()).order_by('date')[:5]
    if not events:
        return "\nUPCOMING EVENTS\n- No upcoming events are published right now.\n"
    lines = ["\nUPCOMING EVENTS (page /events/<slug>/ shows details)"]
    for e in events:
        lines.append(f"- {e.title} on {e.date:%d %B %Y}, at {e.location} (/events/{e.slug}/)")
    return "\n".join(lines) + "\n"


@require_POST
def chatbot(request):
    if _rate_limited(request):
        return JsonResponse(
            {'reply': 'You are sending messages very quickly. Please wait a few minutes and try again.'},
            status=429,
        )

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'reply': 'Invalid request.'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'reply': 'Invalid request.'}, status=400)

    question = str(data.get('message', '')).strip()[:500]
    if not question:
        return JsonResponse({'reply': 'Please type a question.'}, status=400)

    client = _get_gemini_client()
    if client is None:
        return JsonResponse(
            {'reply': 'The assistant is not set up yet. Please contact us by email.'},
            status=503,
        )

    # Rebuild the last few turns so follow-up questions make sense.
    contents = []
    history = data.get('history', [])
    if isinstance(history, list):
        for turn in history[-6:]:
            if not isinstance(turn, dict):
                continue
            text = str(turn.get('text', '')).strip()[:500]
            role = 'user' if turn.get('role') == 'user' else 'model'
            if text:
                contents.append(genai_types.Content(role=role, parts=[genai_types.Part(text=text)]))
    contents.append(genai_types.Content(role='user', parts=[genai_types.Part(text=question)]))

    try:
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=SITE_INFO + _upcoming_events_text(),
                max_output_tokens=400,
            ),
        )
        reply = (response.text or '').strip()
    except (genai_errors.APIError, httpx2.TransportError, TimeoutError, ConnectionError) as exc:
        logger.exception('Gemini chatbot request failed: %s', exc)
        return JsonResponse(
            {'reply': 'Sorry, I could not answer that right now. Please try again shortly.'},
            status=502,
        )

    if not reply:
        reply = 'Sorry, I could not answer that right now. Please email boldfoundation59@gmail.com.'
    return JsonResponse({'reply': reply})
