import json
import tempfile
from datetime import timedelta
from io import BytesIO
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from .models import Donation, Event, EventPhoto, Mentor, MpesaTransaction
from .mpesa import initiate_stk_push, query_stk_status
from .views import events_list


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

    def test_events_are_split_into_upcoming_and_past(self):
        response = self.client.get('/events/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['upcoming_events']), 1)
        self.assertEqual(len(response.context['past_events']), 1)
        self.assertIn('Upcoming Events', response.content.decode())
        self.assertContains(response, 'View Details')

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
        report = SimpleUploadedFile('report.pdf', b'%PDF-1.4 recap', content_type='application/pdf')

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

    def test_event_support_records_the_event(self):
        event = Event.objects.get(slug='upcoming-community-talk')
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

        self.assertRedirects(response, reverse('event_detail', args=[event.slug]))
        self.assertEqual(Donation.objects.get().event, event)

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

        image = SimpleUploadedFile(
            'event-cover.png',
            b'fake-image-bytes',
            content_type='image/png',
        )

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


class MpesaApiTests(SimpleTestCase):
    @override_settings(
        MPESA_ENVIRONMENT='sandbox',
        MPESA_CONSUMER_KEY='test-key',
        MPESA_CONSUMER_SECRET='test-secret',
        MPESA_SHORTCODE='174379',
        MPESA_PASSKEY='test-passkey',
        MPESA_TRANSACTION_TYPE='CustomerPayBillOnline',
        MPESA_CALLBACK_URL='https://staging.eduhope.org/mpesa/callback/',
    )
    @patch('eduapp.mpesa.requests.post')
    @patch('eduapp.mpesa.requests.get')
    def test_stk_push_fetches_oauth_token_and_uses_sandbox(self, mock_get, mock_post):
        mock_get.return_value.json.return_value = {'access_token': 'test-token'}
        mock_post.return_value.json.return_value = {'ResponseCode': '0'}

        result = initiate_stk_push('254708374149', 100, 'MentorRef')

        self.assertEqual(result['ResponseCode'], '0')
        self.assertIn('sandbox.safaricom.co.ke/oauth/v1/generate', mock_get.call_args.args[0])
        self.assertIn('sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest', mock_post.call_args.args[0])
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_get.call_args.kwargs['auth'], ('test-key', 'test-secret'))
        self.assertEqual(mock_post.call_args.kwargs['headers']['Authorization'], 'Bearer test-token')
        self.assertEqual(mock_post.call_args.kwargs['json']['CallBackURL'], 'https://staging.eduhope.org/mpesa/callback/')
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
        mock_get.return_value.json.return_value = {'access_token': 'test-token'}
        mock_post.return_value.json.return_value = {'ResponseCode': '0'}

        result = query_stk_status('checkout-id')

        self.assertEqual(result['ResponseCode'], '0')
        self.assertIn('sandbox.safaricom.co.ke/oauth/v1/generate', mock_get.call_args.args[0])
        self.assertIn('sandbox.safaricom.co.ke/mpesa/stkpushquery/v1/query', mock_post.call_args.args[0])
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_post.call_args.kwargs['headers']['Authorization'], 'Bearer test-token')
        self.assertEqual(mock_post.call_args.kwargs['json']['CheckoutRequestID'], 'checkout-id')


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

    def _callback(self, result_code, items=None):
        return self.client.post(
            reverse('mpesa_callback'),
            data=json.dumps({
                'Body': {
                    'stkCallback': {
                        'CheckoutRequestID': self.transaction.checkout_request_id,
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

        self.assertEqual(response.status_code, 400)
        self.transaction.refresh_from_db()
        self.mentor.refresh_from_db()
        self.assertEqual(self.transaction.status, 'pending')
        self.assertEqual(self.mentor.status, 'payment_pending')

    def test_payment_polling_does_not_mark_pending_transaction_successful(self):
        session = self.client.session
        session['checkout_request_id'] = self.transaction.checkout_request_id
        session.save()

        response = self.client.get(reverse('check_payment_status'))

        self.assertEqual(response.json()['status'], 'pending')
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.status, 'pending')
