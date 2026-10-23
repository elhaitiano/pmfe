"""Numeric text entry without platform-dependent browser number spinners."""
from datetime import date
from django import forms


class DecimalTextInput(forms.TextInput):
    def value_from_datadict(self, data, files, name):
        value = super().value_from_datadict(data, files, name)
        return value.replace(',', '.') if isinstance(value, str) else value


def numeric_text_widgets(fields):
    for field in fields.values():
        if field.disabled or isinstance(field.widget, forms.HiddenInput):
            continue
        attrs = dict(field.widget.attrs)
        if isinstance(field, forms.DecimalField):
            attrs['inputmode'] = 'decimal'
            field.widget = DecimalTextInput(attrs=attrs)
        elif isinstance(field, forms.IntegerField):
            attrs['inputmode'] = 'numeric'
            field.widget = forms.TextInput(attrs=attrs)


class DayMonthYearInput(forms.DateInput):
    """Stable display format, including existing ISO query-string values."""

    def __init__(self, attrs=None):
        attributes = {
            'placeholder': 'dd/mm/aaaa',
            'maxlength': '10',
            'pattern': '[0-9]{2}/[0-9]{2}/[0-9]{4}',
        }
        attributes.update(attrs or {})
        attributes.pop('type', None)
        super().__init__(attrs=attributes, format='%d/%m/%Y')

    def format_value(self, value):
        if isinstance(value, str):
            try:
                value = date.fromisoformat(value)
            except ValueError:
                pass
        return super().format_value(value)


def date_widgets(fields):
    for field in fields.values():
        if isinstance(field, forms.DateField) and not isinstance(field, forms.DateTimeField):
            field.input_formats = ['%d/%m/%Y', '%Y-%m-%d']
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget = DayMonthYearInput(attrs=field.widget.attrs)
