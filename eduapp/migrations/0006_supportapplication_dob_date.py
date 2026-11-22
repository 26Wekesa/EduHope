from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eduapp', '0005_event_evt_pub_date_idx'),
    ]

    operations = [
        migrations.RenameField(
            model_name='supportapplication',
            old_name='dob',
            new_name='legacy_dob',
        ),
        migrations.AlterField(
            model_name='supportapplication',
            name='legacy_dob',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name='supportapplication',
            name='dob',
            field=models.DateField(blank=True, null=True),
        ),
    ]
