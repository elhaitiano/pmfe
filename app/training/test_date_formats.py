from datetime import date, datetime
from django import forms
from django.template import Context, Template
from django.test import SimpleTestCase
from .widgets import date_widgets


class DateFormatTests(SimpleTestCase):
    def test_date_entry_roundtrip_and_validation(self):
        class Example(forms.Form):
            day = forms.DateField(required=False)

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                date_widgets(self.fields)

        for value in ('03/04/2028', '2028-04-03'):
            form = Example({'day': value})
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data['day'], date(2028, 4, 3))
            self.assertIn('value="03/04/2028"', str(form['day']))
        self.assertTrue(Example({'day': ''}).is_valid())
        self.assertTrue(Example({'day': '29/02/2028'}).is_valid())
        for value in ('31/02/2028', '29/02/2027', '04/25/2028'):
            self.assertFalse(Example({'day': value}).is_valid())
        self.assertIn('value="03/04/2028"', str(Example(initial={'day': date(2028, 4, 3)})['day']))

    def test_hidden_and_datetime_widgets_preserved(self):
        fields = {
            'hidden': forms.DateField(widget=forms.HiddenInput()),
            'timestamp': forms.DateTimeField(),
            'disabled': forms.DateField(disabled=True),
        }
        timestamp_widget = fields['timestamp'].widget
        date_widgets(fields)
        self.assertIsInstance(fields['hidden'].widget, forms.HiddenInput)
        self.assertIs(fields['timestamp'].widget, timestamp_widget)
        self.assertEqual(fields['disabled'].widget.format_value(date(2028, 4, 3)), '03/04/2028')

    def test_default_template_dates(self):
        rendered = Template('{{ day }}|{{ timestamp }}').render(Context({
            'day': date(2028, 4, 3), 'timestamp': datetime(2028, 4, 3, 9, 15),
        }))
        self.assertEqual(rendered, '03/04/2028|03/04/2028 09:15')
