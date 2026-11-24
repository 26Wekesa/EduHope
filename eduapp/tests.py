import base64
import json
import re
import tempfile
import zipfile
from datetime import date, timedelta
from io import BytesIO
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
import httpx2
from requests.exceptions import Timeout
from unittest.mock import patch

from .forms import (
    ContactMessageForm,
    EventImageForm,
    EventRecapUploadForm,
    EventSupportDonationForm,
    MentorApplicationForm,
    MentorshipBookingForm,
    PartnershipRequestForm,
    RegistrationForm,
    SupportApplicationForm,
    MAX_EVENT_UPLOAD_SIZE,
)
from .models import (
    Donation, Event, EventPhoto, Mentor, Message, MpesaTransaction,
    PartnershipRequest, SupportApplication,
)
from .mpesa import initiate_stk_push, query_stk_status


def minimal_valid_pdf():
    content = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>',
    ]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f'{number} 0 obj\n'.encode())
        content.extend(body + b'\nendobj\n')

    xref_offset = len(content)
    content.extend(b'xref\n0 4\n0000000000 65535 f \n')
    for offset in offsets[1:]:
        content.extend(f'{offset:010d} 00000 n \n'.encode())
    content.extend(
        b'trailer\n<< /Size 4 /Root 1 0 R >>\n'
        + f'startxref\n{xref_offset}\n'.encode()
        + b'%%EOF\n'
    )
    return bytes(content)


class LegalPolicyPageTests(TestCase):
    def test_all_seven_policy_pages_render_as_review_drafts(self):
        pages = (
            ('privacy', 'Privacy Policy'),
            ('child_safeguarding', 'Child Safeguarding Policy'),
            ('code_of_conduct', 'Code of Conduct'),
            ('donations_refunds', 'Donations and Refund Policy'),
            ('cookie_policy', 'Cookie Policy'),
            ('complaints_grievance', 'Complaints and Grievance Policy'),
            ('data_retention', 'Data Retention Policy'),
        )

        for route, title in pages:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, title)
                self.assertContains(response, 'WORKING DRAFT')
                self.assertContains(response, 'noindex,nofollow')
                self.assertContains(response, 'Constitution of Kenya, 2010')
                self.assertContains(response, 'UN Convention on the Rights of the Child')

    def test_footer_exposes_policy_pages_and_cookie_controls(self):
        response = self.client.get(reverse('index'))

        for route in (
            'privacy',
            'child_safeguarding',
            'code_of_conduct',
            'donations_refunds',
            'cookie_policy',
            'complaints_grievance',
            'data_retention',
        ):
            with self.subTest(route=route):
                self.assertContains(response, reverse(route))

        self.assertContains(response, 'Accept all cookies')
        self.assertContains(response, 'Reject non-essential')
        self.assertContains(response, 'Manage preferences')
        self.assertContains(response, 'data-cookie-settings')
        self.assertContains(response, 'chatPrivacyNotice')
        self.assertContains(response, '+254 793 299 818')

    def test_policies_disclose_unresolved_child_data_and_safeguarding_controls(self):
        response = self.client.get(reverse('privacy'))
        self.assertContains(response, 'distinct verified guardian-consent step')
        self.assertContains(response, 'Oregon, United States')
        self.assertContains(response, 'not a durable record of')
        self.assertContains(response, 'Foundation Secretary monitors the general')
        self.assertContains(response, 'does not enforce that sequence')

        response = self.client.get(reverse('child_safeguarding'))
        self.assertContains(response, 'not yet a verified')
        self.assertContains(response, 'can receive and store learner details')

        response = self.client.get(reverse('cookie_policy'))
        self.assertContains(response, 'optional tracking service')
        self.assertContains(response, 'id="cookie-settings"')

        response = self.client.get(reverse('complaints_grievance'))
        self.assertContains(response, 'seven calendar days')
        self.assertContains(response, '+254 793 299 818')

        response = self.client.get(reverse('donations_refunds'))
        self.assertContains(response, 'international')
        self.assertContains(response, 'within seven calendar days')

        response = self.client.get(reverse('contact'))
        self.assertContains(response, 'within seven calendar days')
        self.assertContains(response, '+254 793 299 818')

    def test_draft_policies_are_not_published_in_the_sitemap(self):
        response = self.client.get(reverse('sitemap'))

        for route in (
            'privacy',
            'child_safeguarding',
            'code_of_conduct',
            'donations_refunds',
            'cookie_policy',
            'complaints_grievance',
            'data_retention',
        ):
            with self.subTest(route=route):
                self.assertNotContains(response, reverse(route))


class HealthCheckTests(SimpleTestCase):
    @patch('eduapp.views.connection.ensure_connection')
    def test_health_check_reports_ready_when_database_is_available(self, ensure_connection):
        response = self.client.get(reverse('health_check'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        ensure_connection.assert_called_once_with()

    @patch('eduapp.views.logger.exception')
    @patch('eduapp.views.connection.ensure_connection', side_effect=OperationalError)
    def test_health_check_reports_unavailable_database(self, ensure_connection, log_exception):
        response = self.client.get(reverse('health_check'))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unhealthy'})
        log_exception.assert_called_once()


class ChatbotTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_chatbot_requires_post(self):
        response = self.client.get(reverse('chatbot'))

        self.assertEqual(response.status_code, 405)

    def test_chatbot_rejects_empty_message(self):
        response = self.client.post(
            reverse('chatbot'),
            data=json.dumps({'message': '  '}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['reply'], 'Please type a question.')

    def test_chatbot_rejects_non_object_json(self):
        response = self.client.post(
            reverse('chatbot'),
            data=json.dumps(['not', 'an', 'object']),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['reply'], 'Invalid request.')

    @override_settings(GEMINI_API_KEY='')
    @patch('eduapp.views._get_gemini_client', return_value=None)
    def test_chatbot_reports_missing_configuration(self, get_client):
        response = self.client.post(
            reverse('chatbot'),
            data=json.dumps({'message': 'What programmes do you offer?'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn('not set up', response.json()['reply'])
        get_client.assert_called_once_with()

    @patch('eduapp.views._get_gemini_client')
    def test_chatbot_returns_gemini_reply(self, get_client):
        client = get_client.return_value
        client.models.generate_content.return_value = SimpleNamespace(text='We offer mentorship.')

        response = self.client.post(
            reverse('chatbot'),
            data=json.dumps({
                'message': 'What programmes do you offer?',
                'history': [{'role': 'user', 'text': 'Hello'}],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['reply'], 'We offer mentorship.')
        client.models.generate_content.assert_called_once()

    @patch('eduapp.views._get_gemini_client')
    def test_chatbot_reports_provider_timeout(self, get_client):
        get_client.return_value.models.generate_content.side_effect = httpx2.TimeoutException(
            'Timed out'
        )

        with self.assertLogs('eduapp.views', level='ERROR'):
            response = self.client.post(
                reverse('chatbot'),
                data=json.dumps({'message': 'Tell me about mentorship'}),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn('try again shortly', response.json()['reply'])


class PublicApplicationFormTests(TestCase):
    def test_learner_textareas_do_not_render_css_as_text(self):
        content = render_to_string('learner.html', {'form': SupportApplicationForm()})

        situation = re.search(
            r'<textarea name="situation_description"[^>]*>(.*?)</textarea>',
            content,
            re.DOTALL,
        )
        remarks = re.search(
            r'<textarea name="informant_remarks"[^>]*>(.*?)</textarea>',
            content,
            re.DOTALL,
        )
        self.assertIsNotNone(situation)
        self.assertIsNotNone(remarks)
        self.assertNotIn('style=', situation.group(1))
        self.assertNotIn('border-radius:', remarks.group(1))

    def test_phone_numbers_are_validated_and_normalized_to_254(self):
        form = MentorApplicationForm(data={
            'name': 'Amina Otieno',
            'email': 'amina@example.com',
            'phone': '+254 712 345 678',
            'organization': '',
            'years': '60',
            'subject': 'Mentoring',
            'message': 'I would like to mentor learners.',
            'honeypot': '',
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['phone'], '254712345678')

    def test_invalid_phone_and_out_of_range_experience_are_rejected(self):
        form = MentorApplicationForm(data={
            'name': 'Amina Otieno',
            'email': 'amina@example.com',
            'phone': '12345',
            'organization': '',
            'years': '61',
            'subject': 'Mentoring',
            'message': 'I would like to mentor learners.',
            'honeypot': '',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)
        self.assertIn('years', form.errors)

    def test_honeypot_rejects_automated_public_form_submissions(self):
        form = ContactMessageForm(data={
            'name': 'Automated sender',
            'email': 'bot@example.com',
            'subject': 'Spam',
            'message': 'Spam content',
            'honeypot': 'filled by a bot',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('honeypot', form.errors)

    def test_support_application_requires_a_valid_non_future_date(self):
        data = {
            'learner_name': 'Learner Name',
            'dob': (date.today() + timedelta(days=1)).isoformat(),
            'grade': 'Grade 5',
            'school': 'Example School',
            'support_type': 'school_fees',
            'situation_description': 'Needs support.',
            'guardian_name': 'Guardian Name',
            'relationship': 'Parent',
            'guardian_contact': '0712345678',
            'address': 'Nairobi',
            'informant_name': 'Informant Name',
            'informant_phone': '254712345678',
            'informant_relationship': 'Teacher',
            'informant_remarks': '',
            'honeypot': '',
        }
        form = SupportApplicationForm(data=data)

        self.assertFalse(form.is_valid())
        self.assertIn('dob', form.errors)

    def test_donation_amount_must_be_positive_and_phone_valid(self):
        for amount in ('0', '1.50'):
            with self.subTest(amount=amount):
                form = EventSupportDonationForm(data={
                    'donor_name': 'Amina Otieno',
                    'email': 'amina@example.com',
                    'phone': '0712345678',
                    'amount': amount,
                    'donation_type': 'one_time',
                    'message': '',
                    'honeypot': '',
                })

                self.assertFalse(form.is_valid())
                self.assertIn('amount', form.errors)

    def test_partnership_form_validates_email_and_normalizes_phone(self):
        form = PartnershipRequestForm(data={
            'org_name': 'Community Group',
            'partner_type': 'ngo',
            'email': 'team@example.org',
            'phone': '0712345678',
            'website': 'https://example.org',
            'partnership_area': 'projects',
            'proposal_description': 'A joint learner program.',
            'reason': 'We share the same goals.',
            'honeypot': '',
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['phone'], '254712345678')

    def test_booking_rejects_out_of_range_learner_and_session_counts(self):
        form = MentorshipBookingForm(data={
            'school_name': 'Example School',
            'contact_person': 'Amina Otieno',
            'email': 'amina@example.com',
            'phone': '0712345678',
            'number_of_learners': '0',
            'preferred_date': date.today().isoformat(),
            'preferred_time': '09:00',
            'duration_hours': '9',
            'topics': 'Leadership',
            'additional_notes': '',
            'honeypot': '',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('number_of_learners', form.errors)
        self.assertIn('duration_hours', form.errors)

    def test_registration_applies_password_validators_and_email_validation(self):
        form = RegistrationForm(data={
            'username': 'new-user',
            'email': 'invalid',
            'password1': 'password',
            'password2': 'password',
            'honeypot': '',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
        self.assertIn('password2', form.errors)


class LoginRedirectTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='login-user',
            password='StrongPass123!',
        )

    def test_login_redirects_to_safe_next_parameter(self):
        response = self.client.post(reverse('login'), {
            'username': 'login-user',
            'password': 'StrongPass123!',
            'next': reverse('events'),
        })

        self.assertRedirects(response, reverse('events'))

    def test_login_ignores_external_next_parameter(self):
        response = self.client.post(reverse('login'), {
            'username': 'login-user',
            'password': 'StrongPass123!',
            'next': 'https://malicious.example/',
        })

        self.assertRedirects(response, reverse('mentor_dashboard'))


class PublicApplicationFlowTests(TestCase):
    def setUp(self):
        self.client.post(reverse('terms'), {'accept_terms': 'on'})
        self.mentor_data = {
            'name': 'Amina Otieno',
            'email': 'amina@example.com',
            'phone': '0712345678',
            'organization': 'Community Center',
            'years': '5',
            'subject': 'Mentoring',
            'message': 'I would like to mentor learners.',
            'honeypot': '',
        }

    def test_valid_mentor_application_is_saved_and_advances_to_payment(self):
        response = self.client.post(reverse('mentor'), self.mentor_data)

        mentor = Mentor.objects.get(email='amina@example.com')
        self.assertRedirects(
            response,
            reverse('payment_initiate', args=[mentor.pk]),
        )
        self.assertEqual(mentor.phone, '254712345678')
        self.assertEqual(mentor.status, 'payment_pending')

    @patch('eduapp.views.initiate_stk_push')
    def test_payment_initiation_persists_only_daraja_accepted_checkout(self, initiate):
        mentor = Mentor.objects.create(
            name='Amina Otieno',
            email='amina@example.com',
            phone='254712345678',
            organization='Community Center',
            years=5,
            subject='Mentoring',
            message='I would like to mentor learners.',
        )
        initiate.return_value = {
            'ResponseCode': '0',
            'CheckoutRequestID': 'checkout-confirmed',
            'MerchantRequestID': 'merchant-confirmed',
        }
        session = self.client.session
        session['pending_mentor_id'] = mentor.pk
        session.save()

        response = self.client.post(
            reverse('payment_initiate', args=[mentor.pk]),
            {'phone': '+254 712 345 678', 'amount': '100'},
        )

        transaction = MpesaTransaction.objects.get(mentor=mentor)
        self.assertRedirects(response, reverse('payment_status'))
        self.assertEqual(transaction.phone_number, '254712345678')
        initiate.assert_called_once_with(
            phone_number='254712345678',
            amount=100,
            account_ref='Amina Otieno',
            description='Mentor Application',
        )

        repeated_response = self.client.post(
            reverse('payment_initiate', args=[mentor.pk]),
            {'phone': '+254 712 345 678', 'amount': '100'},
        )
        self.assertRedirects(repeated_response, reverse('payment_status'))
        initiate.assert_called_once()

    @patch('eduapp.views.initiate_stk_push')
    def test_payment_initiation_rejects_invalid_phone_before_daraja(self, initiate):
        mentor = Mentor.objects.create(
            name='Amina Otieno',
            email='amina@example.com',
            phone='254712345678',
            organization='Community Center',
            years=5,
            subject='Mentoring',
            message='I would like to mentor learners.',
        )
        session = self.client.session
        session['pending_mentor_id'] = mentor.pk
        session.save()

        response = self.client.post(
            reverse('payment_initiate', args=[mentor.pk]),
            {'phone': 'not-a-phone', 'amount': '100'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'valid Kenyan mobile number', status_code=400)
        initiate.assert_not_called()
        self.assertFalse(MpesaTransaction.objects.exists())

    @patch('eduapp.views.initiate_stk_push')
    def test_payment_initiation_rejects_unowned_mentor_id(self, initiate):
        mentor = Mentor.objects.create(
            name='Amina Otieno',
            email='amina@example.com',
            phone='254712345678',
            organization='Community Center',
            years=5,
            subject='Mentoring',
            message='I would like to mentor learners.',
        )

        response = self.client.post(
            reverse('payment_initiate', args=[mentor.pk]),
            {'phone': '0712345678', 'amount': '100'},
        )

        self.assertEqual(response.status_code, 404)
        initiate.assert_not_called()
        self.assertFalse(MpesaTransaction.objects.exists())

    def test_invalid_mentor_application_returns_errors_without_saving(self):
        invalid_data = {**self.mentor_data, 'phone': 'invalid', 'years': '100'}

        response = self.client.post(reverse('mentor'), invalid_data)

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'valid Kenyan mobile number', status_code=400)
        self.assertFalse(Mentor.objects.exists())


class EventsPageTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        Event.objects.create(
            title='Upcoming Community Talk',
            slug='upcoming-community-talk',
            description='Future event',
            location='Nairobi',
            date=timezone.now() + timedelta(days=7),
            is_published=True,
        )
        Event.objects.create(
            title='Past Mentorship Day',
            slug='past-mentorship-day',
            description='Past event',
            location='Kisumu',
            date=timezone.now() - timedelta(days=7),
            is_published=True,
        )
        Event.objects.create(
            title='Unpublished Community Event',
            slug='unpublished-community-event',
            description='Draft event',
            location='Nairobi',
            date=timezone.now() + timedelta(days=14),
            is_published=False,
        )

    def test_events_are_split_into_upcoming_and_past(self):
        response = self.client.get('/events/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['upcoming_events']), 1)
        self.assertEqual(len(response.context['past_events']), 1)
        self.assertIn('Upcoming Events', response.content.decode())
        self.assertContains(response, 'View Details')

    def test_public_event_lists_are_paginated(self):
        for number in range(6):
            Event.objects.create(
                title=f'Past Community Event {number}',
                slug=f'past-community-event-{number}',
                description='Past event',
                location='Nairobi',
                date=timezone.now() - timedelta(days=number + 10),
                is_published=True,
            )

        first_page = self.client.get(reverse('events'))
        second_page = self.client.get(reverse('events'), {'past_page': '2'})

        self.assertEqual(len(first_page.context['past_events']), 6)
        self.assertTrue(first_page.context['past_events'].has_next())
        self.assertEqual(len(second_page.context['past_events']), 1)
        self.assertContains(first_page, '?past_page=2#past-events')

    def test_sitemap_lists_public_pages_and_published_events(self):
        response = self.client.get(reverse('sitemap'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('events'))
        self.assertContains(response, reverse('about'))
        self.assertContains(response, reverse('programmes'))
        self.assertContains(response, reverse('impact'))
        self.assertContains(response, reverse('get_involved'))
        self.assertContains(response, reverse('contact'))
        self.assertContains(response, reverse('event_detail', args=['past-mentorship-day']))
        self.assertNotContains(response, 'unpublished-community-event')
        self.assertNotContains(response, reverse('admin_view'))

    def test_robots_txt_points_to_sitemap_and_excludes_private_routes(self):
        response = self.client.get(reverse('robots_txt'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Sitemap: http://testserver{reverse("sitemap")}')
        self.assertContains(response, 'Disallow: /admin_view/')
        self.assertContains(response, 'Disallow: /dashboard/')

    def test_public_page_has_search_metadata_and_canonical_url(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'name="description"')
        self.assertContains(response, 'rel="canonical" href="http://testserver/"')
        self.assertContains(response, 'property="og:title"')
        self.assertContains(response, 'name="twitter:card"')
        schema = re.search(
            r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
            response.content.decode(),
            flags=re.DOTALL,
        )
        self.assertEqual(json.loads(schema.group(1))['@type'], 'NGO')

    def test_loading_feedback_is_present_without_remote_fonts_or_unused_scripts(self):
        for path in (reverse('index'), reverse('login')):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertContains(response, 'id="pageProgress"')
                self.assertContains(response, 'page-feedback.')
                self.assertNotContains(response, 'fonts.googleapis.com')
                self.assertNotContains(response, 'assets/vendor/swiper/swiper-bundle.min.js')

    def test_primary_navigation_uses_accessible_brand_colored_icons(self):
        response = self.client.get(reverse('index'))

        for icon in (
            'bi-house-door',
            'bi-info-circle',
            'bi-people',
            'bi-bar-chart',
            'bi-calendar-event',
            'bi-heart',
            'bi-person-plus',
            'bi-envelope',
        ):
            with self.subTest(icon=icon):
                self.assertContains(response, f'class="bi {icon}"')
                self.assertContains(response, 'aria-hidden="true"')
        self.assertContains(response, 'navbar-brand__logo')
        self.assertContains(response, '--icon-accent: #00c2e0')

    def test_standalone_pages_include_navigation_progress(self):
        for path in (reverse('terms'), reverse('privacy')):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertContains(response, 'id="pageProgress"')
                self.assertContains(response, 'page-feedback.')

    def test_public_pages_use_gzip_when_the_client_supports_it(self):
        response = self.client.get(reverse('index'), HTTP_ACCEPT_ENCODING='gzip')

        self.assertEqual(response.get('Content-Encoding'), 'gzip')
        self.assertIn('Accept-Encoding', response.get('Vary', ''))

    def test_localhost_origin_is_allowed_for_csrf_protected_forms(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse('terms'))
        csrf_token = client.cookies['csrftoken'].value

        response = client.post(
            reverse('terms'),
            {'accept_terms': 'on', 'csrfmiddlewaretoken': csrf_token},
            HTTP_ORIGIN='http://localhost:8000',
        )

        self.assertEqual(response.status_code, 302)

    def test_application_login_is_not_indexed_and_partner_has_clean_route(self):
        response = self.client.get(reverse('login'))

        self.assertContains(response, 'name="robots" content="noindex,follow"')
        self.assertContains(response, f'rel="canonical" href="http://testserver{reverse("login")}"')
        self.assertEqual(reverse('partner'), '/partner/')

    def test_upcoming_event_has_event_structured_data(self):
        response = self.client.get(reverse('event_detail', args=['upcoming-community-talk']))
        page = response.content.decode()
        schema = re.search(
            r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
            page,
            flags=re.DOTALL,
        )

        self.assertIsNotNone(schema)
        self.assertEqual(json.loads(schema.group(1))['@type'], 'Event')

    def test_storytelling_pages_render_the_existing_mission_and_programmes(self):
        response = self.client.get(reverse('about'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Boys Of Leadership &amp; Dignity')
        self.assertContains(response, 'September 2025')
        self.assertContains(response, 'Makanga')
        self.assertContains(response, 'Kibabii University')
        self.assertContains(response, 'Beyond the Timetable')
        self.assertContains(response, reverse('programmes'))

        response = self.client.get(reverse('programmes'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Mentorship')
        self.assertContains(response, 'Public speaking')
        self.assertContains(response, 'Climate and community outreach')
        self.assertContains(response, 'mental wellness')
        self.assertContains(response, 'market clean-ups')

    def test_impact_page_reports_activities_without_invented_outcome_metrics(self):
        response = self.client.get(reverse('impact'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Masinde Muliro University')
        self.assertContains(response, 'Kibabii University')
        self.assertContains(response, 'Mentorship Club')
        self.assertContains(response, '11 October 2026')
        self.assertContains(response, 'Tree planting')
        self.assertContains(response, 'does not')

    def test_get_involved_and_contact_pages_are_reachable(self):
        response = self.client.get(reverse('get_involved'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('mentor'))
        self.assertContains(response, reverse('partner'))
        self.assertContains(response, reverse('events'))

        response = self.client.get(reverse('contact'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Contact Bold Foundation')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_contact_form_accepts_a_message(self):
        response = self.client.post(reverse('contact'), {
            'name': 'Amina Otieno',
            'email': 'amina@example.com',
            'subject': 'Programme information',
            'message': 'Please share more about your programmes.',
            'honeypot': '',
        })

        self.assertRedirects(response, reverse('index'))
        self.assertTrue(Message.objects.filter(email='amina@example.com').exists())

    def test_staff_admin_dashboard_renders_workspace_navigation(self):
        self._create_staff_user()
        client = Client()
        self.assertTrue(client.login(username='adminuser', password='StrongPass123!'))

        response = client.get(reverse('admin_view'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Operations dashboard')
        self.assertContains(response, 'Activity &amp; requests')
        self.assertContains(response, 'Workspace summary')
        self.assertContains(response, 'data-bs-target="#events"')

    def test_admin_dashboard_counts_only_paid_donations_and_shows_donation_payments(self):
        self._create_staff_user()
        client = Client()
        self.assertTrue(client.login(username='adminuser', password='StrongPass123!'))
        paid_donation = Donation.objects.create(
            donor_name='Paid Donor',
            email='paid@example.com',
            amount=500,
            donation_type='one_time',
            payment_status='success',
        )
        Donation.objects.create(
            donor_name='Pending Donor',
            email='pending@example.com',
            amount=300,
            donation_type='one_time',
        )
        MpesaTransaction.objects.create(
            donation=paid_donation,
            phone_number='254712345678',
            amount=500,
            checkout_request_id='admin-donation-checkout',
            merchant_request_id='admin-donation-merchant',
            status='success',
        )

        response = client.get(reverse('admin_view'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stats']['total_donations'], 1)
        self.assertContains(response, 'Donation: Paid Donor')

    def test_admin_dashboard_paginates_large_message_lists(self):
        self._create_staff_user()
        client = Client()
        self.assertTrue(client.login(username='adminuser', password='StrongPass123!'))
        Message.objects.bulk_create([
            Message(
                name=f'Contact {number}',
                email=f'contact{number}@example.com',
                subject='Question',
                message='A message',
            )
            for number in range(21)
        ])

        first_page = client.get(reverse('admin_view'))
        second_page = client.get(reverse('admin_view'), {'messages_page': '2'})

        self.assertEqual(len(first_page.context['all_messages']), 20)
        self.assertEqual(first_page.context['all_messages'].paginator.count, 21)
        self.assertEqual(len(second_page.context['all_messages']), 1)
        self.assertContains(first_page, '?messages_page=2#messages')

    def test_past_event_detail_shows_recap_area(self):
        response = self.client.get(reverse('event_detail', args=['past-mentorship-day']))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_past_event'])
        self.assertContains(response, 'Past Event Details')
        self.assertContains(response, 'The event report has not been uploaded yet.')
        self.assertNotContains(response, 'Support this event')

    def test_staff_can_upload_past_event_photos_and_report(self):
        self._create_staff_user()
        client = Client()
        self.assertTrue(client.login(username='adminuser', password='StrongPass123!'))
        admin_response = client.get(reverse('admin_view'))
        self.assertContains(admin_response, 'Past-event recap')
        self.assertContains(admin_response, 'Save recap')

        from PIL import Image

        image_buffer = BytesIO()
        Image.new('RGB', (1, 1), color='blue').save(image_buffer, format='PNG')
        photo = SimpleUploadedFile('recap.png', image_buffer.getvalue(), content_type='image/png')
        report = SimpleUploadedFile(
            'report.pdf',
            minimal_valid_pdf(),
            content_type='application/pdf',
        )
        recap_form = EventRecapUploadForm(
            files={'photos': [photo], 'report': report},
        )
        self.assertTrue(recap_form.is_valid(), recap_form.errors)

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                response = client.post(
                    reverse('event_recap_upload', args=[Event.objects.get(slug='past-mentorship-day').id]),
                    {'photos': [photo], 'report': report},
                )

                self.assertRedirects(response, reverse('admin_view'), fetch_redirect_response=False)
                event = Event.objects.get(slug='past-mentorship-day')
                self.assertEqual(EventPhoto.objects.filter(event=event).count(), 1)
                self.assertTrue(event.report.name.endswith('report.pdf'))

                detail_response = client.get(reverse('event_detail', args=[event.slug]))
                self.assertContains(detail_response, 'Download event report')
                self.assertContains(detail_response, 'Event photos')

    def test_staff_cannot_upload_recap_before_event_has_passed(self):
        self._create_staff_user()
        client = Client()
        self.assertTrue(client.login(username='adminuser', password='StrongPass123!'))
        event = Event.objects.get(slug='upcoming-community-talk')

        response = client.post(reverse('event_recap_upload', args=[event.id]), {})

        self.assertRedirects(response, reverse('admin_view'), fetch_redirect_response=False)
        self.assertFalse(EventPhoto.objects.filter(event=event).exists())
        self.assertFalse(event.report)

    def test_event_actions_open_details_and_support_form(self):
        event = Event.objects.get(slug='upcoming-community-talk')

        response = self.client.get(reverse('events'))

        self.assertContains(response, reverse('event_detail', args=[event.slug]))
        self.assertContains(response, f'{reverse("event_detail", args=[event.slug])}#support-form')

    def test_homepage_event_buttons_use_working_routes(self):
        event = Event.objects.get(slug='upcoming-community-talk')

        response = self.client.get(reverse('index'))

        self.assertContains(response, reverse('events'))
        self.assertContains(response, reverse('event_detail', args=[event.slug]))

    @patch('eduapp.views.initiate_stk_push')
    def test_event_support_records_donation_as_pending_until_payment_confirmation(self, initiate):
        event = Event.objects.get(slug='upcoming-community-talk')
        initiate.return_value = {
            'ResponseCode': '0',
            'CheckoutRequestID': 'donation-checkout',
            'MerchantRequestID': 'donation-merchant',
        }
        self.client.post(reverse('terms'), {'accept_terms': 'on'})

        response = self.client.post(
            reverse('event_support', args=[event.slug]),
            {
                'donor_name': 'Amina Otieno',
                'email': 'amina@example.com',
                'phone': '0712345678',
                'amount': '500',
                'donation_type': 'one_time',
                'message': 'Keep going.',
            },
        )

        self.assertRedirects(response, reverse('payment_status'))
        donation = Donation.objects.get()
        self.assertEqual(donation.event, event)
        self.assertEqual(donation.message, 'Keep going.')
        self.assertEqual(donation.payment_status, 'pending')
        transaction = MpesaTransaction.objects.get(donation=donation)
        self.assertEqual(transaction.amount, donation.amount)
        initiate.assert_called_once_with(
            phone_number='254712345678',
            amount=500,
            account_ref=f'EVT{event.pk}DON',
            description='Event Donation',
        )

    @patch('eduapp.views.initiate_stk_push')
    def test_event_support_does_not_count_declined_payment_as_donation(self, initiate):
        event = Event.objects.get(slug='upcoming-community-talk')
        initiate.return_value = {'ResponseCode': '1', 'errorMessage': 'Payment unavailable'}
        self.client.post(reverse('terms'), {'accept_terms': 'on'})

        response = self.client.post(
            reverse('event_support', args=[event.slug]),
            {
                'donor_name': 'Amina Otieno',
                'email': 'amina@example.com',
                'phone': '0712345678',
                'amount': '500',
                'donation_type': 'one_time',
                'message': '',
            },
        )

        self.assertRedirects(response, reverse('event_detail', args=[event.slug]))
        donation = Donation.objects.get()
        self.assertEqual(donation.payment_status, 'failed')
        self.assertEqual(Donation.objects.filter(payment_status='success').count(), 0)
        self.assertFalse(MpesaTransaction.objects.exists())

    def test_service_requires_terms_acceptance(self):
        response = self.client.get(reverse('learner'))

        self.assertRedirects(
            response,
            f'{reverse("terms")}?next={reverse("learner")}',
        )
        self.assertFalse(Donation.objects.exists())

        response = self.client.post(
            reverse('terms'),
            {'accept_terms': 'on', 'next': reverse('learner')},
            follow=True,
        )

        self.assertRedirects(response, reverse('learner'))
        self.assertContains(response, 'Register a Learner')

    def test_event_upload_requires_media_settings_and_staff_access(self):
        self.assertEqual(settings.MEDIA_URL, '/media/')
        self.assertTrue(str(settings.MEDIA_ROOT).endswith('media'))

        self._create_staff_user()
        client = Client()

        self.assertTrue(client.login(username='adminuser', password='StrongPass123!'))

        from PIL import Image

        image_buffer = BytesIO()
        Image.new('RGB', (1, 1), color='blue').save(image_buffer, format='PNG')
        image = SimpleUploadedFile(
            'event-cover.png',
            image_buffer.getvalue(),
            content_type='image/png',
        )

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                response = client.post(
                    reverse('event_create'),
                    {
                        'title': 'Spring Community Workshop',
                        'slug': 'spring-community-workshop',
                        'description': 'A community workshop for youth leaders.',
                        'date': (timezone.now() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M'),
                        'location': 'Nairobi',
                        'is_featured': 'on',
                        'is_published': 'on',
                        'image': image,
                    },
                    follow=False,
                )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Event.objects.filter(title='Spring Community Workshop').exists())

    def test_event_create_rejects_malformed_image(self):
        self._create_staff_user()
        client = Client()
        self.assertTrue(client.login(username='adminuser', password='StrongPass123!'))

        response = client.post(
            reverse('event_create'),
            {
                'title': 'Invalid Image Event',
                'description': 'Test event',
                'date': (timezone.now() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M'),
                'location': 'Nairobi',
                'image': SimpleUploadedFile('fake.png', b'not an image'),
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Event.objects.filter(title='Invalid Image Event').exists())

    def test_event_create_assigns_unique_slug_when_title_is_repeated(self):
        self._create_staff_user()
        client = Client()
        self.assertTrue(client.login(username='adminuser', password='StrongPass123!'))
        Event.objects.create(
            title='Repeated Community Event',
            description='First event.',
            date=timezone.now() + timedelta(days=2),
            location='Nairobi',
        )

        response = client.post(reverse('event_create'), {
            'title': 'Repeated Community Event',
            'description': 'Another event.',
            'date': (timezone.now() + timedelta(days=4)).strftime('%Y-%m-%dT%H:%M'),
            'location': 'Nairobi',
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Event.objects.filter(slug='repeated-community-event-2').exists())

    def test_event_create_rejects_invalid_date(self):
        self._create_staff_user()
        client = Client()
        self.assertTrue(client.login(username='adminuser', password='StrongPass123!'))

        response = client.post(reverse('event_create'), {
            'title': 'Invalid Date Event',
            'description': 'Should not be created.',
            'date': 'not-a-date',
            'location': 'Nairobi',
        })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Event.objects.filter(title='Invalid Date Event').exists())

    def _create_staff_user(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username='adminuser',
            email='admin@example.com',
            password='StrongPass123!',
        )
        user.is_staff = True
        user.save()
        return user


class AdminActionSecurityTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='securityadmin',
            password='StrongPass123!',
            is_staff=True,
        )
        self.mentor = Mentor.objects.create(
            name='Test Mentor',
            email='mentor@example.com',
            phone='254700000000',
            organization='Example Organization',
            years=2,
            subject='Mentorship',
            message='Test application',
            status='pending_review',
        )
        self.learner = SupportApplication.objects.create(
            learner_name='Test Learner',
            dob='2010-01-01',
            grade='Grade 8',
            school='Test School',
            support_type='Mentorship',
            situation_description='Test application',
            guardian_name='Test Guardian',
            relationship='Parent',
            guardian_contact='254700000001',
            address='Nairobi',
            informant_name='Test Informant',
            informant_phone='254700000002',
            informant_relationship='Teacher',
        )
        self.partner = PartnershipRequest.objects.create(
            org_name='Test Partner',
            partner_type='Community',
            email='partner@example.com',
            phone='254700000003',
            partnership_area='Mentorship',
            proposal_description='Test proposal',
            reason='Test reason',
        )
        self.message = Message.objects.create(
            name='Test Contact',
            email='contact@example.com',
            subject='Question',
            message='Test message',
        )
        self.event = Event.objects.create(
            title='Test Event',
            slug='test-event-security',
            description='Test event',
            location='Nairobi',
            date=timezone.now() + timedelta(days=1),
        )

    def _action_urls(self):
        return [
            reverse('approve_mentor', args=[self.mentor.pk]),
            reverse('reject_mentor', args=[self.mentor.pk]),
            reverse('delete_mentor', args=[self.mentor.pk]),
            reverse('approve_learner', args=[self.learner.pk]),
            reverse('delete_learner', args=[self.learner.pk]),
            reverse('approve_partner', args=[self.partner.pk]),
            reverse('delete_partner', args=[self.partner.pk]),
            reverse('delete_message', args=[self.message.pk]),
            reverse('event_delete', args=[self.event.pk]),
        ]

    def _assert_records_unchanged(self):
        self.mentor.refresh_from_db()
        self.learner.refresh_from_db()
        self.partner.refresh_from_db()
        self.assertEqual(self.mentor.status, 'pending_review')
        self.assertFalse(self.mentor.approved)
        self.assertFalse(self.learner.approved)
        self.assertFalse(self.partner.approved)
        self.assertTrue(Message.objects.filter(pk=self.message.pk).exists())
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_anonymous_requests_cannot_run_admin_actions(self):
        for action_url in self._action_urls():
            with self.subTest(action_url=action_url):
                self.assertEqual(self.client.get(action_url).status_code, 302)
                self.assertEqual(self.client.post(action_url).status_code, 302)

        for view_url in (
            reverse('admin_view'),
            reverse('event_create'),
            reverse('event_edit', args=[self.event.pk]),
        ):
            with self.subTest(view_url=view_url):
                self.assertEqual(self.client.get(view_url).status_code, 302)
                self.assertEqual(self.client.post(view_url).status_code, 302)

        self._assert_records_unchanged()

    def test_staff_get_requests_cannot_run_admin_actions(self):
        self.client.force_login(self.staff)

        for action_url in self._action_urls():
            with self.subTest(action_url=action_url):
                self.assertEqual(self.client.get(action_url).status_code, 405)

        self._assert_records_unchanged()

    def test_non_staff_post_requests_cannot_run_admin_actions(self):
        non_staff = get_user_model().objects.create_user(
            username='securityuser',
            password='StrongPass123!',
        )
        self.client.force_login(non_staff)

        for action_url in self._action_urls():
            with self.subTest(action_url=action_url):
                self.assertEqual(self.client.post(action_url).status_code, 302)

        for view_url in (
            reverse('admin_view'),
            reverse('event_create'),
            reverse('event_edit', args=[self.event.pk]),
        ):
            with self.subTest(view_url=view_url):
                self.assertEqual(self.client.get(view_url).status_code, 302)

        self._assert_records_unchanged()

    def test_staff_can_run_admin_actions_with_post(self):
        self.client.force_login(self.staff)

        response = self.client.post(reverse('approve_mentor', args=[self.mentor.pk]))
        self.assertEqual(response.status_code, 302)
        self.mentor.refresh_from_db()
        self.assertEqual(self.mentor.status, 'approved')
        self.assertTrue(self.mentor.approved)

        response = self.client.post(reverse('delete_message', args=[self.message.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Message.objects.filter(pk=self.message.pk).exists())

        response = self.client.post(reverse('event_delete', args=[self.event.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_staff_post_actions_require_a_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)

        response = client.post(reverse('delete_message', args=[self.message.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Message.objects.filter(pk=self.message.pk).exists())

    def test_dashboard_admin_actions_render_as_csrf_post_forms(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('admin_view'))

        rendered_action_urls = [
            reverse('approve_mentor', args=[self.mentor.pk]),
            reverse('delete_mentor', args=[self.mentor.pk]),
            reverse('approve_learner', args=[self.learner.pk]),
            reverse('delete_learner', args=[self.learner.pk]),
            reverse('approve_partner', args=[self.partner.pk]),
            reverse('delete_partner', args=[self.partner.pk]),
            reverse('delete_message', args=[self.message.pk]),
            reverse('event_delete', args=[self.event.pk]),
        ]
        for action_url in rendered_action_urls:
            with self.subTest(action_url=action_url):
                self.assertContains(response, f'action="{action_url}"')
        self.assertContains(response, 'method="post"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_staff_can_open_event_editor_but_changes_are_post_only(self):
        self.client.force_login(self.staff)

        self.assertEqual(self.client.get(reverse('event_create')).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('event_edit', args=[self.event.pk])).status_code,
            200,
        )
        self.assertEqual(self.client.get(reverse('event_delete', args=[self.event.pk])).status_code, 405)

    def test_logout_is_post_only_and_uses_csrf_protected_forms(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        response = self.client.get(reverse('admin_view'))
        self.assertContains(response, f'action="{reverse("logout")}"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

        self.assertEqual(self.client.post(reverse('logout')).status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)


class EventUploadValidationTests(SimpleTestCase):
    def test_rejects_malformed_reports_and_images(self):
        invalid_reports = [
            SimpleUploadedFile('fake.pdf', b'not a PDF'),
            SimpleUploadedFile('broken.pdf', b'%PDF-1.4\n%%EOF\n'),
            SimpleUploadedFile('fake.doc', b'not a DOC'),
            SimpleUploadedFile('fake.docx', b'not a DOCX'),
        ]

        for report in invalid_reports:
            with self.subTest(name=report.name):
                form = EventRecapUploadForm(files={'report': report})
                self.assertFalse(form.is_valid())

        image_form = EventImageForm(files={
            'image': SimpleUploadedFile('fake.png', b'not an image'),
        })
        self.assertFalse(image_form.is_valid())

    def test_accepts_basic_valid_report_signatures(self):
        pdf = SimpleUploadedFile('report.pdf', minimal_valid_pdf())
        pdf_form = EventRecapUploadForm(files={'report': pdf})
        self.assertTrue(pdf_form.is_valid(), pdf_form.errors)

        docx_content = BytesIO()
        with zipfile.ZipFile(docx_content, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                '[Content_Types].xml',
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
            )
            archive.writestr(
                'word/document.xml',
                '<document xmlns="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
            )
        docx = SimpleUploadedFile('report.docx', docx_content.getvalue())
        docx_form = EventRecapUploadForm(files={'report': docx})
        self.assertTrue(docx_form.is_valid(), docx_form.errors)

    def test_event_uploads_cannot_exceed_five_megabytes(self):
        oversized = SimpleUploadedFile(
            'large.pdf',
            b'x' * (MAX_EVENT_UPLOAD_SIZE + 1),
        )
        report_form = EventRecapUploadForm(files={'report': oversized})
        self.assertFalse(report_form.is_valid())

        oversized_image = SimpleUploadedFile(
            'large.png',
            b'x' * (MAX_EVENT_UPLOAD_SIZE + 1),
        )
        image_form = EventImageForm(files={'image': oversized_image})
        self.assertFalse(image_form.is_valid())

        oversized_photo = SimpleUploadedFile(
            'large.png',
            b'x' * (MAX_EVENT_UPLOAD_SIZE + 1),
        )
        photo_form = EventRecapUploadForm(files={'photos': [oversized_photo]})
        self.assertFalse(photo_form.is_valid())

    def test_docx_with_excessive_uncompressed_xml_is_rejected(self):
        docx_content = BytesIO()
        with zipfile.ZipFile(docx_content, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                '[Content_Types].xml',
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
            )
            archive.writestr(
                'word/document.xml',
                b'x' * (5 * 1024 * 1024 + 1),
            )

        form = EventRecapUploadForm(files={
            'report': SimpleUploadedFile('oversized.docx', docx_content.getvalue()),
        })

        self.assertFalse(form.is_valid())


class MpesaApiTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @override_settings(
        MPESA_ENVIRONMENT='sandbox',
        MPESA_CONSUMER_KEY='test-key',
        MPESA_CONSUMER_SECRET='test-secret',
        MPESA_SHORTCODE='174379',
        MPESA_PASSKEY='test-passkey',
        MPESA_TRANSACTION_TYPE='CustomerPayBillOnline',
        MPESA_CALLBACK_URL='https://staging.eduhope.org/mpesa/callback/',
        MPESA_CALLBACK_SECRET='test-callback-secret',
    )
    @patch('eduapp.mpesa.requests.post')
    @patch('eduapp.mpesa.requests.get')
    def test_stk_push_fetches_oauth_token_and_uses_sandbox(self, mock_get, mock_post):
        mock_get.return_value.json.return_value = {
            'access_token': 'test-token',
            'expires_in': '3599',
        }
        mock_post.return_value.json.return_value = {'ResponseCode': '0'}

        result = initiate_stk_push('254708374149', 100, 'MentorRef')

        self.assertEqual(result['ResponseCode'], '0')
        self.assertIn('sandbox.safaricom.co.ke/oauth/v1/generate', mock_get.call_args.args[0])
        self.assertIn('sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest', mock_post.call_args.args[0])
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_get.call_args.kwargs['auth'], ('test-key', 'test-secret'))
        self.assertEqual(mock_get.call_args.kwargs['timeout'], 15)
        self.assertEqual(mock_post.call_args.kwargs['headers']['Authorization'], 'Bearer test-token')
        self.assertEqual(mock_post.call_args.kwargs['timeout'], 15)
        self.assertEqual(
            mock_post.call_args.kwargs['json']['CallBackURL'],
            'https://staging.eduhope.org/mpesa/callback/?token=test-callback-secret',
        )
        self.assertEqual(mock_post.call_args.kwargs['json']['TransactionType'], 'CustomerPayBillOnline')

    @override_settings(
        MPESA_ENVIRONMENT='sandbox',
        MPESA_CONSUMER_KEY='test-key',
        MPESA_CONSUMER_SECRET='test-secret',
        MPESA_SHORTCODE='174379',
        MPESA_PASSKEY='test-passkey',
    )
    @patch('eduapp.mpesa.requests.post')
    @patch('eduapp.mpesa.requests.get')
    def test_status_query_fetches_oauth_token(self, mock_get, mock_post):
        mock_get.return_value.json.return_value = {
            'access_token': 'test-token',
            'expires_in': '3599',
        }
        mock_post.return_value.json.return_value = {
            'ResponseCode': '0',
            'CheckoutRequestID': 'checkout-id',
            'ResultCode': '0',
            'ResultDesc': 'Confirmed',
        }

        result = query_stk_status('checkout-id')

        self.assertEqual(result['ResponseCode'], '0')
        self.assertIn('sandbox.safaricom.co.ke/oauth/v1/generate', mock_get.call_args.args[0])
        self.assertIn('sandbox.safaricom.co.ke/mpesa/stkpushquery/v1/query', mock_post.call_args.args[0])
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_post.call_args.kwargs['headers']['Authorization'], 'Bearer test-token')
        self.assertEqual(mock_post.call_args.kwargs['json']['CheckoutRequestID'], 'checkout-id')
        query_payload = mock_post.call_args.kwargs['json']
        self.assertEqual(query_payload['BusinessShortCode'], '174379')
        self.assertTrue(query_payload['Timestamp'].isdecimal())
        self.assertEqual(
            query_payload['Password'],
            base64.b64encode(
                f"174379test-passkey{query_payload['Timestamp']}".encode()
            ).decode(),
        )
        self.assertEqual(mock_post.call_args.kwargs['timeout'], 15)

    @override_settings(
        MPESA_ENVIRONMENT='sandbox',
        MPESA_CONSUMER_KEY='test-key',
        MPESA_CONSUMER_SECRET='test-secret',
        MPESA_SHORTCODE='174379',
        MPESA_PASSKEY='test-passkey',
    )
    @patch('eduapp.mpesa.requests.post')
    @patch('eduapp.mpesa.requests.get')
    def test_oauth_token_is_reused_until_expiry(self, mock_get, mock_post):
        mock_get.return_value.json.return_value = {
            'access_token': 'test-token',
            'expires_in': '3599',
        }
        mock_post.return_value.json.return_value = {'ResponseCode': '0'}

        query_stk_status('checkout-one')
        query_stk_status('checkout-two')

        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_post.call_count, 2)

    @override_settings(
        MPESA_ENVIRONMENT='sandbox',
        MPESA_CONSUMER_KEY='test-key',
        MPESA_CONSUMER_SECRET='test-secret',
        MPESA_SHORTCODE='174379',
        MPESA_PASSKEY='test-passkey',
    )
    @patch('eduapp.mpesa.requests.post')
    @patch('eduapp.mpesa.requests.get')
    def test_oauth_http_errors_abort_stk_queries(self, mock_get, mock_post):
        mock_get.return_value.raise_for_status.side_effect = Timeout('OAuth timed out')

        with self.assertRaises(Timeout):
            query_stk_status('checkout-id')
        mock_post.assert_not_called()


    @override_settings(
        MPESA_ENVIRONMENT='sandbox',
        MPESA_CONSUMER_KEY='test-key',
        MPESA_CONSUMER_SECRET='test-secret',
        MPESA_SHORTCODE='174379',
        MPESA_PASSKEY='test-passkey',
        MPESA_CALLBACK_URL='https://your-public-domain.example/mpesa/callback/',
    )
    @patch('eduapp.mpesa.requests.post')
    @patch('eduapp.mpesa.requests.get')
    def test_stk_push_rejects_example_callback_url(self, mock_get, mock_post):
        result = initiate_stk_push('254708374149', 100, 'MentorRef')

        self.assertEqual(result['ResponseCode'], '1')
        mock_get.assert_not_called()
        mock_post.assert_not_called()


@override_settings(MPESA_CALLBACK_SECRET='test-callback-secret')
class MpesaCallbackTests(TestCase):
    def setUp(self):
        self.mentor = Mentor.objects.create(
            name='Test Mentor',
            email='mentor@example.com',
            phone='254708374149',
            organization='Example Organization',
            years=2,
            subject='Mentorship',
            message='Test application',
        )
        self.transaction = MpesaTransaction.objects.create(
            mentor=self.mentor,
            phone_number='254708374149',
            amount=100,
            checkout_request_id='ws_CO_test_checkout',
            merchant_request_id='test_merchant_request',
        )

    def _callback(
        self,
        result_code,
        items=None,
        token='test-callback-secret',
        transaction=None,
    ):
        transaction = transaction or self.transaction
        callback_url = reverse('mpesa_callback')
        if token is not None:
            callback_url = f'{callback_url}?token={token}'
        return self.client.post(
            callback_url,
            data=json.dumps({
                'Body': {
                    'stkCallback': {
                        'CheckoutRequestID': transaction.checkout_request_id,
                        'ResultCode': result_code,
                        'ResultDesc': 'Completed' if result_code == 0 else 'Cancelled',
                        'CallbackMetadata': {'Item': items or []},
                    },
                },
            }),
            content_type='application/json',
        )

    def test_success_callback_requires_matching_amount_and_receipt(self):
        response = self._callback(0, [
            {'Name': 'Amount', 'Value': 100},
            {'Name': 'MpesaReceiptNumber', 'Value': 'QAB123XYZ'},
        ])

        self.assertEqual(response.status_code, 200)
        self.transaction.refresh_from_db()
        self.mentor.refresh_from_db()
        self.assertEqual(self.transaction.status, 'success')
        self.assertEqual(self.transaction.mpesa_receipt_number, 'QAB123XYZ')
        self.assertEqual(self.mentor.status, 'pending_review')

    def test_success_callback_with_wrong_amount_does_not_approve_payment(self):
        response = self._callback(0, [
            {'Name': 'Amount', 'Value': 1},
            {'Name': 'MpesaReceiptNumber', 'Value': 'QAB123XYZ'},
        ])

        self.assertEqual(response.status_code, 200)
        self.transaction.refresh_from_db()
        self.mentor.refresh_from_db()
        self.assertEqual(self.transaction.status, 'pending')
        self.assertEqual(self.mentor.status, 'payment_pending')

    def test_success_callback_marks_donation_paid_only_with_matching_amount_and_receipt(self):
        donation = Donation.objects.create(
            donor_name='Amina Otieno',
            email='amina@example.com',
            phone='254708374149',
            amount=500,
            donation_type='one_time',
        )
        donation_transaction = MpesaTransaction.objects.create(
            donation=donation,
            phone_number='254708374149',
            amount=500,
            checkout_request_id='donation-checkout',
            merchant_request_id='donation-merchant',
        )

        self._callback(0, [
            {'Name': 'Amount', 'Value': 500},
            {'Name': 'MpesaReceiptNumber', 'Value': 'DON123XYZ'},
        ], transaction=donation_transaction)

        donation.refresh_from_db()
        donation_transaction.refresh_from_db()
        self.assertEqual(donation.payment_status, 'success')
        self.assertEqual(donation_transaction.status, 'success')
        self.assertEqual(donation_transaction.mpesa_receipt_number, 'DON123XYZ')

    def test_success_callback_with_wrong_donation_amount_leaves_it_unpaid(self):
        donation = Donation.objects.create(
            donor_name='Amina Otieno',
            email='amina@example.com',
            phone='254708374149',
            amount=500,
            donation_type='one_time',
        )
        donation_transaction = MpesaTransaction.objects.create(
            donation=donation,
            phone_number='254708374149',
            amount=500,
            checkout_request_id='donation-checkout',
            merchant_request_id='donation-merchant',
        )

        self._callback(0, [
            {'Name': 'Amount', 'Value': 1},
            {'Name': 'MpesaReceiptNumber', 'Value': 'DON123XYZ'},
        ], transaction=donation_transaction)

        donation.refresh_from_db()
        donation_transaction.refresh_from_db()
        self.assertEqual(donation.payment_status, 'pending')
        self.assertEqual(donation_transaction.status, 'pending')

    def test_callback_failure_is_recorded_without_approving_mentor(self):
        response = self._callback(1032)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ResultCode'], 0)
        self.transaction.refresh_from_db()
        self.mentor.refresh_from_db()
        self.assertEqual(self.transaction.status, 'failed')
        self.assertEqual(self.transaction.result_code, 1032)
        self.assertEqual(self.mentor.status, 'payment_pending')

    def test_repeated_success_callback_is_idempotent(self):
        items = [
            {'Name': 'Amount', 'Value': 100},
            {'Name': 'MpesaReceiptNumber', 'Value': 'QAB123XYZ'},
        ]
        self._callback(0, items)

        with patch.object(Mentor, 'save', autospec=True) as mentor_save:
            response = self._callback(0, items)

        self.assertEqual(response.status_code, 200)
        mentor_save.assert_not_called()
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.status, 'success')

    def test_callback_requires_secret_token(self):
        response = self._callback(0, token=None)

        self.assertEqual(response.status_code, 403)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.status, 'pending')

    def test_malformed_callback_is_logged_and_acknowledged(self):
        with self.assertLogs('eduapp.views', level='WARNING'):
            response = self.client.post(
                f"{reverse('mpesa_callback')}?token=test-callback-secret",
                data='{}',
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ResultCode'], 0)

    @patch('eduapp.views.query_stk_status')
    def test_payment_polling_does_not_mark_unconfirmed_transaction_successful(self, query_status):
        query_status.return_value = {
            'ResponseCode': '0',
            'ResultCode': '1032',
            'CheckoutRequestID': self.transaction.checkout_request_id,
            'ResultDesc': 'Request cancelled by user.',
        }
        session = self.client.session
        session['checkout_request_id'] = self.transaction.checkout_request_id
        session.save()

        response = self.client.get(reverse('check_payment_status'))

        self.assertEqual(response.json()['status'], 'pending')
        query_status.assert_called_once_with(self.transaction.checkout_request_id)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.status, 'pending')
        self.mentor.refresh_from_db()
        self.assertEqual(self.mentor.status, 'payment_pending')

    @patch('eduapp.views.query_stk_status')
    def test_payment_polling_marks_only_matching_daraja_success(self, query_status):
        query_status.return_value = {
            'ResponseCode': '0',
            'ResultCode': '0',
            'CheckoutRequestID': self.transaction.checkout_request_id,
            'ResultDesc': 'The service request is processed successfully.',
        }
        session = self.client.session
        session['checkout_request_id'] = self.transaction.checkout_request_id
        session.save()

        response = self.client.get(reverse('check_payment_status'))

        self.assertEqual(response.json()['status'], 'success')
        self.transaction.refresh_from_db()
        self.mentor.refresh_from_db()
        self.assertEqual(self.transaction.status, 'success')
        self.assertEqual(self.transaction.result_code, 0)
        self.assertEqual(self.mentor.status, 'pending_review')

    @patch('eduapp.views.query_stk_status')
    def test_payment_polling_does_not_mark_donation_paid_without_callback_receipt(self, query_status):
        donation = Donation.objects.create(
            donor_name='Amina Otieno',
            email='amina@example.com',
            phone='254708374149',
            amount=500,
            donation_type='one_time',
        )
        donation_transaction = MpesaTransaction.objects.create(
            donation=donation,
            phone_number='254708374149',
            amount=500,
            checkout_request_id='donation-checkout',
            merchant_request_id='donation-merchant',
        )
        query_status.return_value = {
            'ResponseCode': '0',
            'ResultCode': '0',
            'CheckoutRequestID': donation_transaction.checkout_request_id,
            'ResultDesc': 'Payment succeeded.',
        }
        session = self.client.session
        session['checkout_request_id'] = donation_transaction.checkout_request_id
        session.save()

        response = self.client.get(reverse('check_payment_status'))

        self.assertEqual(response.json()['status'], 'pending')
        donation.refresh_from_db()
        donation_transaction.refresh_from_db()
        self.assertEqual(donation.payment_status, 'pending')
        self.assertEqual(donation_transaction.status, 'pending')

    @patch('eduapp.views.query_stk_status')
    def test_payment_polling_rejects_success_for_another_checkout(self, query_status):
        query_status.return_value = {
            'ResponseCode': '0',
            'ResultCode': '0',
            'CheckoutRequestID': 'different-checkout',
        }
        session = self.client.session
        session['checkout_request_id'] = self.transaction.checkout_request_id
        session.save()

        response = self.client.get(reverse('check_payment_status'))

        self.assertEqual(response.json()['status'], 'pending')
        self.transaction.refresh_from_db()
        self.mentor.refresh_from_db()
        self.assertEqual(self.transaction.status, 'pending')
        self.assertEqual(self.mentor.status, 'payment_pending')

    @patch('eduapp.views.query_stk_status', side_effect=Timeout('Daraja timed out'))
    def test_payment_polling_keeps_pending_when_daraja_is_unavailable(self, query_status):
        session = self.client.session
        session['checkout_request_id'] = self.transaction.checkout_request_id
        session.save()

        response = self.client.get(reverse('check_payment_status'))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['status'], 'pending')
        self.transaction.refresh_from_db()
        self.mentor.refresh_from_db()
        self.assertEqual(self.transaction.status, 'pending')
        self.assertEqual(self.mentor.status, 'payment_pending')
