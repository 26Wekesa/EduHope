from django.contrib import admin
from eduapp.models import Message, Mentor, SupportApplication
from eduapp.models import PartnershipRequest, MentorshipBooking, Donation

# Register your models here.
admin.site.register(Message)
admin.site.register(Mentor)
admin.site.register(SupportApplication)
admin.site.register(PartnershipRequest)
admin.site.register(MentorshipBooking)
admin.site.register(Donation)

