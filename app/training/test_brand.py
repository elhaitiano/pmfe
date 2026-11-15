from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Employee
from .brand import SERIES

def contrast(a,b):
    def light(h):
        channels=[int(h[i:i+2],16)/255 for i in (1,3,5)]
        channels=[x/12.92 if x<=.04045 else ((x+.055)/1.055)**2.4 for x in channels]
        return sum(x*w for x,w in zip(channels,(.2126,.7152,.0722)))
    lo,hi=sorted((light(a),light(b)))
    return (hi+.05)/(lo+.05)

class BrandIdentityTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('brand-review',password='test')
        self.client.force_login(self.user)
    def test_accessible_text_pairs_and_chart_palette(self):
        for foreground,background in [('#ffffff','#164777'),('#ffffff','#35509a'),('#ffffff','#2e6eae'),('#ffffff','#0e7c86'),('#164777','#f4f8fb'),('#5b6b7f','#ffffff'),('#000000','#27bdbe'),('#000000','#1ea0ca')]:
            self.assertGreaterEqual(contrast(foreground,background),4.5)
        self.assertLess(contrast('#164777','#27bdbe'),4.5)
        self.assertEqual(SERIES[:3],['#35509a','#1ea0ca','#27bdbe'])
    def test_new_mark_and_theme_are_used_without_canvas_distortion(self):
        response=self.client.get(reverse('dashboard'))
        self.assertContains(response,'epal-theme.css')
        self.assertContains(response,'epal-identidade.png')
        self.assertNotContains(response,'epalWaterCanvas')
        self.assertNotContains(response,'entry-water.js')
        self.assertNotContains(response,'class="dark"')
        e=Employee.objects.create(number='BRAND',name='Synthetic')
        for route in ('employee_data','employee_print'):
            response=self.client.get(reverse(route,args=[e.pk]))
            self.assertContains(response,'epal-identidade.png')
            self.assertContains(response,'epal-theme.css')
        self.client.logout()
        self.assertContains(self.client.get(reverse('login')),'epal-identidade.png')
    def test_asset_and_styles_are_packaged(self):
        from django.contrib.staticfiles import finders
        self.assertIsNotNone(finders.find('pmf/epal-identidade.png'))
        self.assertIsNotNone(finders.find('pmf/epal-theme.css'))
