from django.contrib import admin
from .models import (
    Message, Mentor, MpesaTransaction, Event,
    SupportApplication, PartnershipRequest,
    MentorshipBooking, Donation
)


@admin.register(Mentor)
class MentorAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'organization', 'status', 'created_at')
    list_filter = ('status', 'approved')
    search_fields = ('name', 'email', 'organization')
    readonly_fields = ('created_at', 'updated_at')
    actions = ['approve_mentors', 'reject_mentors']

    def approve_mentors(self, request, queryset):
        queryset.update(status='approved', approved=True)
    approve_mentors.short_description = 'Approve selected mentors'

    def reject_mentors(self, request, queryset):
        queryset.update(status='rejected', approved=False)
    reject_mentors.short_description = 'Reject selected mentors'


@admin.register(MpesaTransaction)
class MpesaTransactionAdmin(admin.ModelAdmin):
    list_display = ('checkout_request_id', 'mentor', 'amount', 'status', 'initiated_at')
    list_filter = ('status',)
    readonly_fields = ('initiated_at', 'completed_at')


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'date', 'location', 'is_featured', 'is_published')
    list_filter = ('is_featured', 'is_published')
    search_fields = ('title', 'location')
    prepopulated_fields = {'slug': ('title',)}


@admin.register(SupportApplication)
class SupportApplicationAdmin(admin.ModelAdmin):
    list_display = ('learner_name', 'school', 'support_type', 'approved', 'submitted_at')
    list_filter = ('approved', 'support_type')


@admin.register(PartnershipRequest)
class PartnershipRequestAdmin(admin.ModelAdmin):
    list_display = ('org_name', 'partner_type', 'email', 'approved', 'submitted_at')
    list_filter = ('approved',)


admin.site.register(Message)
admin.site.register(MentorshipBooking)
admin.site.register(Donation)