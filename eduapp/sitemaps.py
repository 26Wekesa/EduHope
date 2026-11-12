from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Event


class StaticPageSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.7

    def items(self):
        return ['index', 'events', 'services', 'terms', 'privacy']

    def location(self, item):
        return reverse(item)


class EventSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.6

    def items(self):
        return Event.objects.filter(is_published=True).order_by('date')

    def lastmod(self, event):
        return event.updated_at

    def location(self, event):
        return reverse('event_detail', kwargs={'slug': event.slug})
