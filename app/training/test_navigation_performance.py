from datetime import date
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import connection
from django.urls import reverse
from .models import Training,Employee,PrivateDocument
from .query_helpers import receipt_aware,dashboard_summary
from .analytics import statistics


class NavigationPerformanceTests(TestCase):
    def setUp(self):
        self.employee=Employee.objects.create(name='Ensaio navegação',number='99001')
        self.other=Employee.objects.create(name='Outro formando',number='99002')
        self.user=get_user_model().objects.create_user('gestor-navegacao',password='test',is_staff=True)
        self.client.force_login(self.user)

    def training(self,**changes):
        values=dict(employee=self.employee,title='Ensaio',area='Técnica',start=date(2026,1,1),end=date(2026,1,1),days=1,hours_per_day=7,attendance=1,attendance_confirmed=True,certified=True,certificate_delivery_date=date(2026,1,1),status='Concluído',grade=16)
        values.update(changes);return Training.objects.create(**values)

    def receipt(self,training,employee):
        return PrivateDocument.objects.create(training=training,employee=employee,kind='participant_receipt',original_name='ensaio.pdf',reference='ENSAIO',sha256='0'*64,size=4,content=b'test',actor='test')

    def test_receipts_are_constant_query_and_respect_owner_and_active_state(self):
        rows=[self.training(title=str(n)) for n in range(24)]
        self.receipt(rows[0],self.employee)
        self.receipt(rows[1],self.other)
        doc=self.receipt(rows[2],self.employee);doc.active=False;doc.save()
        with self.assertNumQueries(3):stats=statistics(receipt_aware(Training.objects.all()))
        self.assertEqual(stats['certificates_receipt_associated'],1)
        self.assertEqual(stats['total'],24)

    def test_dashboard_compact_data_preserves_all_indicators_and_original_evidence(self):
        self.training(source_data={'evidence':'original não alterado'})
        self.training(source_data={'_EPAL_CONSOLIDATED_FACTS':{'attended_hours':None,'total_hours':'7.00','absent_count':None,'attendance_percent':None,'result':'Bom','edition':'edition-1'},'_original_assessment':{'kind':'percentage','normalized_ratio':'0.8','raw':'80/100'}})
        full=statistics(receipt_aware(Training.objects.all()))
        connection.features.supports_json_field  # Initialize the SQLite capability probe outside the count.
        with self.assertNumQueries(3):
            compact=list(dashboard_summary(receipt_aware(Training.objects.all())))
            result=statistics(compact)
        self.assertEqual(result,full)
        manual=next(r for r in compact if r.title=='Ensaio' and r.completed_hours==7)
        self.assertIn('source_data',manual.get_deferred_fields())
        self.assertEqual(manual.source_data['evidence'],'original não alterado')

    def test_home_does_not_recalculate_unused_global_statistics(self):
        self.training()
        with patch('training.views.statistics',side_effect=AssertionError('global statistics should stay lazy')):
            response=self.client.get(reverse('home'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['employee_count'],2)

    def test_internal_pages_keep_icon_and_never_include_the_entry_canvas(self):
        response=self.client.get(reverse('home'))
        self.assertContains(response,'rel="icon"')
        self.assertNotContains(response,'id="epalEntry"')
        self.assertNotContains(response,'entry-water.js')
        self.assertNotContains(response,'4800')
        self.assertContains(response,'color-scheme')
        login=self.client.get(reverse('login'))
        self.assertContains(login,'id="epalEntry"')
