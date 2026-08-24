from datetime import timedelta
from django.test import RequestFactory, TestCase
from django.utils import timezone

from .models import Event
from .views import events_list


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
        request = self.factory.get('/events/')
        response = events_list(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context_data['upcoming_events']), 1)
        self.assertEqual(len(response.context_data['past_events']), 1)
        self.assertIn('Upcoming Events', response.render().content.decode())
