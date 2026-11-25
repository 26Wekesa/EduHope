from django.db import migrations, models


def move_unpaid_mentors_to_review(apps, schema_editor):
    Mentor = apps.get_model('eduapp', 'Mentor')
    Mentor.objects.using(schema_editor.connection.alias).filter(
        status='payment_pending',
    ).update(status='pending_review')


class Migration(migrations.Migration):

    dependencies = [
        ('eduapp', '0008_donation_payment_status_mpesatransaction_donation_and_more'),
    ]

    operations = [
        migrations.RunPython(
            move_unpaid_mentors_to_review,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='mentor',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending_review', 'Pending Review'),
                    ('under_review', 'Under Review'),
                    ('approved', 'Approved'),
                    ('rejected', 'Rejected'),
                    ('interview_scheduled', 'Interview Scheduled'),
                ],
                default='pending_review',
                max_length=30,
            ),
        ),
    ]
