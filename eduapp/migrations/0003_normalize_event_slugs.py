from django.db import migrations
from django.utils.text import slugify


def normalize_event_slugs(apps, schema_editor):
    Event = apps.get_model('eduapp', 'Event')
    used_slugs = set()

    for event in Event.objects.order_by('id'):
        base_slug = slugify(event.slug or event.title) or f'event-{event.id}'
        candidate = base_slug
        counter = 2
        while candidate in used_slugs:
            candidate = f'{base_slug}-{counter}'
            counter += 1
        if event.slug != candidate:
            event.slug = candidate
            event.save(update_fields=['slug'])
        used_slugs.add(candidate)


class Migration(migrations.Migration):
    dependencies = [
        ('eduapp', '0002_donation_event'),
    ]

    operations = [
        migrations.RunPython(normalize_event_slugs, migrations.RunPython.noop),
    ]