from datetime import date
from decimal import Decimal
from django.test import TestCase,SimpleTestCase
from .models import Employee,OfficialRosterEntry,Course,Training,ConsolidatedEdition,ConsolidatedParticipation,HistoricalInterfaceLink
from .historical_interface import install
from .evidence_completion import same_grade,documented_issue_date


class OriginalScalesTests(SimpleTestCase):
    def test_issue_date_requires_agreement_and_never_uses_a_future_or_inverted_date(self):
        end=date(2026,10,29)
        def proof(v):return {'facts':{'issue_date':v}}
        self.assertEqual(documented_issue_date(end,[proof('2026-10-29'),proof(None)]),end)
        self.assertIsNone(documented_issue_date(end,[proof('2026-10-29'),proof('2026-10-30')]))
        self.assertIsNone(documented_issue_date(end,[proof('2040-07-24')]))
        self.assertIsNone(documented_issue_date(end,[proof('2026-10-28')]))
        self.assertIsNone(documented_issue_date(end,[proof(None)]))

    def test_comparing_scales_preserves_the_source_values(self):
        a={'value':'16','scale':'20'};b={'value':'80','scale':'100'}
        self.assertTrue(same_grade(a,b))
        self.assertEqual(a,{'value':'16','scale':'20'})
        self.assertFalse(same_grade(a,{'value':'81','scale':'100'}))
        self.assertTrue(same_grade({'value':'0','scale':'100'},{'value':'0','scale':'20'}))


class ScopedProjectionTests(TestCase):
    def test_pending_participations_use_canonical_code_without_counting_documents_twice(self):
        from .models import DocumentaryRecord,HistoricalDocumentaryParticipation
        from .historical_review import grouped_pending
        person=Employee.objects.create(number='40001',name='Pessoa EPAL')
        day=date(2026,10,1)
        t=Training.objects.create(employee=person,title='Curso',source_course_code='CURSO',start=day,end=day,source_data={})
        d=HistoricalDocumentaryParticipation.objects.create(employee=person,source_key='documentary',title='Curso',course_reference='CURSO',start=day,end=day)
        e=ConsolidatedEdition.objects.create(source_key='edition',title='Curso',course_reference='CURSO',start=day,end=day)
        p=ConsolidatedParticipation.objects.create(source_key='part',employee=person,edition=e,documentary_participation_ids=[d.pk])
        HistoricalInterfaceLink.objects.create(training=t,participation=p)
        DocumentaryRecord.objects.create(source_key='pending',kind='held_assertion',employee=person,state='PENDENTE',payload={
            'course_reference':'CURSO','start_declared':str(day),'end_declared':str(day)},payload_sha256='a'*64,package_sha256='b'*64)
        result=grouped_pending()
        self.assertEqual(result['known_operational_participations'],1)
        self.assertEqual(result['known_documentary_participations'],0)
        self.assertEqual(result['distinct_identified_participations'],1)

    def test_hour_conflict_is_visible_and_offers_only_the_context_decision(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from .models import DocumentaryRecord
        user=get_user_model().objects.create_user('gestor-horas',password='test',is_staff=True,is_superuser=True)
        self.client.force_login(user)
        r=DocumentaryRecord.objects.create(source_key='hours-conflict',kind='context',state='CONFLITO',payload={
            'Título literal':'Formação com horário divergente','claims':[{'value':7,'scale':'horas',
            'eligible_documentary_assertion':True,'source':{'source':'lista.pdf','document':'a'*64}}]},payload_sha256='a'*64,package_sha256='b'*64)
        response=self.client.get(reverse('historical_exceptions')+'?kind=conflicts')
        self.assertContains(response,'Formação com horário divergente')
        response=self.client.get(reverse('historical_exception_detail',args=[r.pk]))
        self.assertContains(response,'Confirmar horas/dia com evidência documental')
        self.assertNotContains(response,'Adotar valor da fonte')

    def test_refresh_is_scoped_and_keeps_historical_delivery_without_a_date(self):
        person=Employee.objects.create(number='10001',name='Pessoa EPAL')
        OfficialRosterEntry.objects.create(employee=person,current=True,source_sha256='a'*64,source_row=1)
        course=Course.objects.create(title='Formação comprovada',area='Técnica')
        edition=ConsolidatedEdition.objects.create(source_key='edition',course=course,title=course.title,
            course_reference='CURSO',start=date(2026,10,1),end=date(2026,10,1),declared_days=1,total_hours=7,hours_per_day=7)
        p=ConsolidatedParticipation.objects.create(source_key='participation',employee=person,edition=edition,
            present_count=1,absent_count=0,attendance_complete=True,attendance_percent=100,attended_hours=7,completed=True)
        t=Training.objects.create(employee=person,title=course.title,start=edition.start,end=edition.end,status='Concluído',
            days=1,hours_per_day=7,certified=True,certificate_issued=True,source_key='existing')
        HistoricalInterfaceLink.objects.create(participation=p,training=t)
        other=Training.objects.create(employee=person,title='Outra formação',start=edition.start,end=edition.end,
            status='Por confirmar',source_key='unrelated')
        version=other.version
        result=install([p.pk]);t.refresh_from_db();other.refresh_from_db()
        self.assertEqual(result['new_interface_rows'],0)
        self.assertTrue(t.certified);self.assertTrue(t.certificate_issued)
        self.assertIsNone(t.certificate_delivery_date)
        self.assertEqual(t.completed_hours,Decimal('7'))
        self.assertEqual(other.interface_state,'operational');self.assertEqual(other.version,version)
        self.assertEqual(other.title,'Outra formação')
