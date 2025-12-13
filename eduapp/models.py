from django.db import models

# Create your models here.
class Message(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    subject = models.CharField(max_length=200)
    message = models.TextField()

    def __str__(self):
        return f"Message from {self.name} - {self.subject}"
    

class Mentor(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.IntegerField(max_length=10)
    message = models.TextField()
    organization = models.CharField(max_length=200)
    years = models.IntegerField()
    message = models.TextField()
    subject = models.CharField(max_length=200)
    approved = models.BooleanField(default=False)

    def __str__(self):
        return f"Mentor from {self.name} - {self.email}"


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
        return f"Partnership Request from {self.org_name}"


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
        return f"Mentorship Booking for {self.school_name}"


class Donation(models.Model):
    donor_name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    donation_type = models.CharField(max_length=50, choices=[
        ('one_time', 'One-Time Donation'),
        ('monthly', 'Monthly Donation'),
        ('annual', 'Annual Donation'),
    ])
    message = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Donation from {self.donor_name} - ${self.amount}"