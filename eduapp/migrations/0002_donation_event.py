from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('eduapp', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='donation',
            name='event',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='donations', to='eduapp.event'),
        ),
    ]
