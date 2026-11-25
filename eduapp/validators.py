import re

from django.core.exceptions import ValidationError


class StrongPasswordValidator:
    CHARACTER_PATTERNS = (
        r'[a-z]',
        r'[A-Z]',
        r'[0-9]',
        r'[^A-Za-z0-9]',
    )

    def validate(self, password, user=None):
        character_types = sum(
            bool(re.search(pattern, password))
            for pattern in self.CHARACTER_PATTERNS
        )
        if character_types < 3:
            raise ValidationError(
                'Use at least three of these four character types: lowercase '
                'letters, uppercase letters, numbers, and symbols.',
                code='insufficient_character_types',
            )

    def get_help_text(self):
        return (
            'Use at least three of these four character types: lowercase '
            'letters, uppercase letters, numbers, and symbols.'
        )
