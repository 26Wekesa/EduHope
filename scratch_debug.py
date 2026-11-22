import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Edulink.settings')
import django
django.setup()
from datetime import timedelta
from django.test import Client
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

u = get_user_model().objects.create_user(username='adminuser', email='admin@example.com', password='StrongPass123!')
u.is_staff = True
u.save()
client = Client()
print('login', client.login(username='adminuser', password='StrongPass123!'))
print('route', reverse('event_create'))
img = SimpleUploadedFile('event-cover.png', b'fake-image-bytes', content_type='image/png')
resp = client.post(reverse('event_create'), {
    'title': 'Spring Community Workshop',
    'slug': 'spring-community-workshop',
    'description': 'A community workshop for youth leaders.',
    'date': (timezone.now() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M'),
    'location': 'Nairobi',
    'is_featured': 'on',
    'is_published': 'on',
    'image': img,
}, follow=False)
print('status', resp.status_code)
print('url', getattr(resp, 'url', None))
print('redirect_chain', getattr(resp, 'redirect_chain', None))
print(resp.content.decode()[:500])
