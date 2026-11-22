import re
import xml.etree.ElementTree as ElementTree
import zipfile
import zlib
from datetime import date
from decimal import Decimal

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

from .models import (
    Donation,
    Mentor,
    MentorshipBooking,
    Message,
    PartnershipRequest,
    SupportApplication,
)


MAX_EVENT_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_DOCX_XML_SIZE = 5 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 1000
OLE_COMPOUND_FILE_SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
KENYAN_MOBILE_PATTERN = re.compile(r'^254[17]\d{8}$')


class KenyanPhoneField(forms.CharField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('max_length', 16)
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        if not value:
            return value

        normalized = re.sub(r'[\s()-]', '', value)
        if normalized.startswith('+'):
            normalized = normalized[1:]
        if normalized.startswith('0'):
            normalized = f'254{normalized[1:]}'
        elif normalized.startswith(('7', '1')):
            normalized = f'254{normalized}'
        if not KENYAN_MOBILE_PATTERN.fullmatch(normalized):
            raise ValidationError('Enter a valid Kenyan mobile number, such as 0712345678.')
        return normalized


class HoneypotModelForm(forms.ModelForm):
    honeypot = forms.CharField(
        required=False,
        label='Leave this field empty',
        widget=forms.TextInput(attrs={
            'class': 'form-honeypot',
            'autocomplete': 'off',
            'tabindex': '-1',
            'aria-hidden': 'true',
        }),
    )

    def clean_honeypot(self):
        if self.cleaned_data.get('honeypot'):
            raise ValidationError('Unable to accept this submission.')
        return ''


class MentorApplicationForm(HoneypotModelForm):
    phone = KenyanPhoneField()
    years = forms.IntegerField(min_value=0, max_value=60)
    organization = forms.CharField(required=False, max_length=200)

    class Meta:
        model = Mentor
        fields = (
            'name', 'email', 'phone', 'organization', 'years', 'subject', 'message',
        )


class SupportApplicationForm(HoneypotModelForm):
    dob = forms.DateField(
        required=True,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    guardian_contact = KenyanPhoneField()
    informant_phone = KenyanPhoneField()
    support_type = forms.ChoiceField(choices=[
        ('school_fees', 'School Fees'),
        ('uniform_clothing', 'Uniform / Clothing'),
        ('learning_materials', 'Learning Materials'),
        ('food_assistance', 'Food Assistance'),
        ('shelter', 'Shelter Support'),
        ('other', 'Other'),
    ])

    class Meta:
        model = SupportApplication
        fields = (
            'learner_name', 'dob', 'grade', 'school', 'support_type',
            'situation_description', 'guardian_name', 'relationship',
            'guardian_contact', 'address', 'informant_name', 'informant_phone',
            'informant_relationship', 'informant_remarks',
        )

    def clean_dob(self):
        dob = self.cleaned_data['dob']
        if dob > date.today():
            raise ValidationError('Date of birth cannot be in the future.')
        return dob


class PartnershipRequestForm(HoneypotModelForm):
    phone = KenyanPhoneField()
    partner_type = forms.ChoiceField(choices=[
        ('ngo', 'NGO / Non-profit'),
        ('government', 'Government Institution'),
        ('company', 'Company / Corporate'),
        ('school', 'School / Training Centre'),
        ('individual', 'Individual'),
    ])
    partnership_area = forms.ChoiceField(choices=[
        ('sponsorship', 'Student Sponsorship'),
        ('donations', 'Donations (Clothes, Food, Materials)'),
        ('projects', 'Joint Projects / Programs'),
        ('training', 'Training & Capacity Building'),
        ('volunteer', 'Volunteer Support'),
        ('infrastructure', 'Infrastructure & Facilities Support'),
        ('other', 'Other'),
    ])

    class Meta:
        model = PartnershipRequest
        fields = (
            'org_name', 'partner_type', 'email', 'phone', 'website',
            'partnership_area', 'proposal_description', 'reason',
        )


class MentorshipBookingForm(HoneypotModelForm):
    phone = KenyanPhoneField()
    number_of_learners = forms.IntegerField(min_value=1, max_value=5000)
    duration_hours = forms.IntegerField(min_value=1, max_value=8)

    class Meta:
        model = MentorshipBooking
        fields = (
            'school_name', 'contact_person', 'email', 'phone',
            'number_of_learners', 'preferred_date', 'preferred_time',
            'duration_hours', 'topics', 'additional_notes',
        )


class ContactMessageForm(HoneypotModelForm):
    class Meta:
        model = Message
        fields = ('name', 'email', 'subject', 'message')


class EventSupportDonationForm(HoneypotModelForm):
    phone = KenyanPhoneField()
    amount = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_digits=10,
        decimal_places=2,
    )

    class Meta:
        model = Donation
        fields = ('donor_name', 'email', 'phone', 'amount', 'donation_type', 'message')


class PaymentInitiationForm(forms.Form):
    phone = KenyanPhoneField()
    amount = forms.IntegerField(required=False, min_value=1)
    honeypot = forms.CharField(
        required=False,
        label='Leave this field empty',
        widget=forms.TextInput(attrs={
            'class': 'form-honeypot',
            'autocomplete': 'off',
            'tabindex': '-1',
            'aria-hidden': 'true',
        }),
    )

    def clean_honeypot(self):
        if self.cleaned_data.get('honeypot'):
            raise ValidationError('Unable to accept this submission.')
        return ''


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    honeypot = forms.CharField(
        required=False,
        label='Leave this field empty',
        widget=forms.TextInput(attrs={
            'class': 'form-honeypot',
            'autocomplete': 'off',
            'tabindex': '-1',
            'aria-hidden': 'true',
        }),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def clean_honeypot(self):
        if self.cleaned_data.get('honeypot'):
            raise ValidationError('Unable to accept this submission.')
        return ''

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


def validate_event_upload_size(upload):
    if upload.size > MAX_EVENT_UPLOAD_SIZE:
        raise ValidationError('Each event upload must be 5 MB or smaller.')


def is_valid_pdf(upload):
    upload.seek(0)
    header = upload.read(1024)
    upload.seek(max(0, upload.size - 1024))
    trailer = upload.read(1024)
    match = re.search(rb'startxref\s+(\d+)\s+%%EOF', trailer)
    if b'%PDF-' not in header or not match:
        return False

    xref_offset = int(match.group(1))
    if xref_offset >= upload.size:
        return False

    upload.seek(xref_offset)
    xref_data = upload.read(1024).lstrip()
    if xref_data.startswith(b'xref'):
        return True
    return bool(
        re.match(rb'\d+\s+\d+\s+obj\b', xref_data)
        and b'/Type /XRef' in xref_data
    )


def validate_event_report_content(upload):
    extension = upload.name.rsplit('.', 1)[-1].lower()
    valid = False

    try:
        upload.seek(0)
        if extension == 'pdf':
            valid = is_valid_pdf(upload)
        elif extension == 'doc':
            header = upload.read(512)
            valid = (
                len(header) == 512
                and header.startswith(OLE_COMPOUND_FILE_SIGNATURE)
                and header[28:30] == b'\xfe\xff'
            )
        elif extension == 'docx':
            with zipfile.ZipFile(upload) as archive:
                archive_entries = archive.infolist()
                entries = {entry.filename: entry for entry in archive_entries}
                content_types = entries.get('[Content_Types].xml')
                document = entries.get('word/document.xml')
                valid = bool(
                    content_types
                    and document
                    and len(archive_entries) <= 1000
                    and sum(entry.file_size for entry in archive_entries)
                    <= MAX_DOCX_UNCOMPRESSED_SIZE
                    and all(
                        not entry.flag_bits & 0x1
                        and entry.file_size
                        <= max(entry.compress_size, 1) * MAX_DOCX_COMPRESSION_RATIO
                        for entry in archive_entries
                    )
                    and content_types.file_size <= 1024 * 1024
                    and document.file_size <= MAX_DOCX_XML_SIZE
                )
                if valid:
                    content_types_xml = ElementTree.fromstring(
                        archive.read(content_types)
                    )
                    document_xml = ElementTree.fromstring(archive.read(document))
                    valid = (
                        content_types_xml.tag
                        == '{http://schemas.openxmlformats.org/package/2006/content-types}Types'
                        and document_xml.tag
                        == '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document'
                    )
    except (
        OSError,
        RuntimeError,
        ValueError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
        zlib.error,
    ):
        valid = False
    finally:
        upload.seek(0)

    if not valid:
        raise ValidationError('The report file is corrupt or does not match its extension.')


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class EventImageField(forms.ImageField):
    def to_python(self, data):
        if data:
            validate_event_upload_size(data)
        return super().to_python(data)


class MultipleImageField(EventImageField):
    widget = MultipleImageInput

    def clean(self, data, initial=None):
        if not data:
            return []

        if not isinstance(data, (list, tuple)):
            data = [data]

        return [forms.ImageField.clean(self, image, initial) for image in data]


class EventImageForm(forms.Form):
    image = EventImageField(required=False)


class EventRecapUploadForm(forms.Form):
    photos = MultipleImageField(required=False)
    report = forms.FileField(
        required=False,
        validators=[
            FileExtensionValidator(allowed_extensions=['pdf', 'doc', 'docx']),
            validate_event_upload_size,
            validate_event_report_content,
        ],
    )

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get('photos') and not cleaned_data.get('report'):
            raise ValidationError('Select at least one photo or report to upload.')
        return cleaned_data
