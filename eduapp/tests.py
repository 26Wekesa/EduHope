import json
import re
import tempfile
import zipfile
from datetime import timedelta
from io import BytesIO
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from .forms import EventImageForm, EventRecapUploadForm, MAX_EVENT_UPLOAD_SIZE
from .models import (
    Donation, Event, EventPhoto, Mentor, Message, MpesaTransaction,
    PartnershipRequest, SupportApplication,
)
from .mpesa import initiate_stk_push, query_stk_status
from .views import events_list


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
