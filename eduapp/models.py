from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


class Message(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    subject = models.CharField(max_length=200)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Message from {self.name}"


class Mentor(models.Model):
    STATUS_CHOICES = [
        ('payment_pending', 'Payment Pending'),
        ('pending_review', 'Pending Review'),
        ('under_review', 'Under Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('interview_scheduled', 'Interview Scheduled'),
    ]

    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=15)
    organization = models.CharField(max_length=200)
    years = models.IntegerField()
    subject = models.CharField(max_length=200)
    message = models.TextField()
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='payment_pending')
    approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} — {self.get_status_display()}"


class MpesaTransaction(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    mentor = models.ForeignKey(Mentor, on_delete=models.CASCADE, related_name='transactions')
    phone_number = models.CharField(max_length=15)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    checkout_request_id = models.CharField(max_length=100, unique=True)
    merchant_request_id = models.CharField(max_length=100)
    mpesa_receipt_number = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    result_code = models.IntegerField(null=True, blank=True)
    result_description = models.TextField(blank=True, null=True)
    initiated_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Txn {self.checkout_request_id} — {self.status}"


class Event(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField()
    image = models.ImageField(upload_to='events/', blank=True, null=True)
    date = models.DateTimeField()
    location = models.CharField(max_length=300)
    is_featured = models.BooleanField(default=False)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date']

    def save(self, *args, **kwargs):
        self.slug = slugify(self.slug or self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class SupportApplication(models.Model):
    learner_name = models.CharField(max_length=200)
    dob = models.CharField(max_length=50)
    grade = models.CharField(max_length=50)
    school = models.CharField(max_length=200)
    support_type = models.CharField(max_length=50)
    situation_description = models.TextField()
    guardian_name = models.CharField(max_length=200)
    relationship = models.CharField(max_length=100)
    guardian_contact = models.CharField(max_length=50)
    address = models.CharField(max_length=300)
    informant_name = models.CharField(max_length=200)
    informant_phone = models.CharField(max_length=50)
    informant_relationship = models.CharField(max_length=100)
    informant_remarks = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    approved = models.BooleanField(default=False)

    def __str__(self):
        return f"Support Application for {self.learner_name}"


class PartnershipRequest(models.Model):
    org_name = models.CharField(max_length=200)
    partner_type = models.CharField(max_length=50)
    email = models.EmailField()
    phone = models.CharField(max_length=50)
    website = models.URLField(blank=True, null=True)
    partnership_area = models.CharField(max_length=100)
    proposal_description = models.TextField()
    reason = models.TextField()
    submitted_at = models.DateTimeField(auto_now_add=True)
    approved = models.BooleanField(default=False)

    def __str__(self):
        return f"Partnership from {self.org_name}"


class MentorshipBooking(models.Model):
    school_name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=50)
    number_of_learners = models.IntegerField()
    preferred_date = models.DateField()
    preferred_time = models.TimeField()
    duration_hours = models.IntegerField()
    topics = models.TextField()
    additional_notes = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Booking for {self.school_name}"

# &mpesatransactions
class Donation(models.Model):
    event = models.ForeignKey(Event, on_delete=models.SET_NULL, null=True, blank=True, related_name='donations')
    donor_name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    donation_type = models.CharField(max_length=50, choices=[
        ('one_time', 'One-Time'),
        ('monthly', 'Monthly'),
        ('annual', 'Annual'),
    ])
    message = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Donation from {self.donor_name} — KES {self.amount}"