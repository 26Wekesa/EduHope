import re
import xml.etree.ElementTree as ElementTree
import zipfile
import zlib

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator


MAX_EVENT_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_DOCX_XML_SIZE = 5 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 1000
OLE_COMPOUND_FILE_SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'


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
