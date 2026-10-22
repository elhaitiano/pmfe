"""Numeric text entry without platform-dependent browser number spinners."""
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
