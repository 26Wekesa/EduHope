from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    widget = MultipleImageInput

    def clean(self, data, initial=None):
        if not data:
            return []

        if not isinstance(data, (list, tuple)):
            data = [data]

        return [forms.ImageField.clean(self, image, initial) for image in data]


class EventRecapUploadForm(forms.Form):
    photos = MultipleImageField(required=False)
    report = forms.FileField(
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'doc', 'docx'])],
    )

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get('photos') and not cleaned_data.get('report'):
            raise ValidationError('Select at least one photo or report to upload.')
        return cleaned_data
