from datetime import date
from decimal import Decimal
from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.db import connection, IntegrityError, transaction
from .models import Course, Employee, Training, TrainingAreaChange, TrainingAreaIssue
from .training_areas import AREAS, AREA_CHOICES, FILTER_CHOICES, normalize_area, TECHNICAL, MANAGEMENT, SAFETY, IT
from .area_reclassification import apply
from .analytics import statistics, distribution
from .dashboard import DashboardFilters, executive_data
from .forms import TrainingForm
from .planning import CourseForm
from .integrated_import import prepare_training


class TrainingAreasTests(TestCase):
    def setUp(self):
        self.person=Employee.objects.create(name='Teste de classificação',number='999001')

    def training(self, **overrides):
        values=dict(employee=self.person,title='Curso de ensaio',area=TECHNICAL,start=date(2026,1,1),end=date(2026,1,1),days=1,hours_per_day=2,attendance=1,attendance_confirmed=True,grade=Decimal('16'),status='Concluído')
        values.update(overrides);return Training.objects.create(**values)

    def test_known_variants_and_unknown_values(self):
        for alias,target in [('tecnica',TECHNICAL),('T.I. / Sistemas',IT),('Soft-skills',MANAGEMENT),('COMUNICAÇÃO',MANAGEMENT),('Seguranca',SAFETY),('Tecnologias da Informação',IT)]:
            self.assertEqual(normalize_area(alias),target)
        for value in ['Sona Fati','Todas as áreas','Gestão','Categoria inventada']:
            with self.assertRaises(ValueError):normalize_area(value)

    def test_model_writes_and_bulk_update_reject_unknowns(self):
        with self.assertRaises(ValidationError):Course.objects.create(title='Inválido',area='Sona Fati')
        course=Course.objects.create(title='Válido',area='TI / Sistemas')
        self.assertEqual(course.area,IT)
        with self.assertRaises(ValidationError):Course.objects.filter(pk=course.pk).update(area='Todas as áreas')
        with self.assertRaises(ValidationError):Course.objects.bulk_create([Course(title='Inválido em lote',area='Outra')])
        course.area='Outra'
        with self.assertRaises(ValidationError):Course.objects.bulk_update([course],['area'])

    def test_all_selectors_share_the_same_four_areas(self):
        self.assertEqual(tuple(DashboardFilters().fields['area'].choices),FILTER_CHOICES)
        for form in [TrainingForm(),CourseForm()]:
            self.assertEqual([v for v,label in form.fields['area'].choices if v],list(AREAS))
            self.assertTrue(form.fields['area'].required)

    def test_course_category_propagates_without_changing_results(self):
        course=Course.objects.create(title='Manutenção',area=TECHNICAL)
        t=self.training(course=course)
        before=(t.days,t.hours_per_day,t.attendance,t.grade,t.certified,t.version)
        course.area=SAFETY;course.save();t.refresh_from_db()
        self.assertEqual(t.area,SAFETY)
        self.assertEqual(before,(t.days,t.hours_per_day,t.attendance,t.grade,t.certified,t.version))
        with self.assertRaises(ValidationError):self.training(course=course,area=MANAGEMENT)

    def test_classification_uses_objective_and_preserves_totals_idempotently(self):
        course=Course.objects.create(title='Operação e Manutenção Segura do Cloro - Manuseamento e Segurança',area=TECHNICAL)
        t=self.training(course=course)
        before=statistics([t]);apply(apps);t.refresh_from_db();course.refresh_from_db()
        self.assertEqual((t.area,course.area),(SAFETY,SAFETY))
        self.assertEqual(statistics([t])['hours'],before['hours'])
        self.assertEqual(t.grade,Decimal('16'))
        count=TrainingAreaChange.objects.count();apply(apps);self.assertEqual(TrainingAreaChange.objects.count(),count)

    def test_ambiguous_objectives_remain_unclassified(self):
        course=Course.objects.create(title='Gestão de manutenção assistida pelo computador',area='')
        self.training(course=course,area='')
        apply(apps);course.refresh_from_db()
        self.assertEqual(course.area,'')
        self.assertTrue(TrainingAreaIssue.objects.filter(model_name='Course',object_id=str(course.pk),resolved_at=None).exists())
        course.area=IT;course.save()
        self.assertFalse(TrainingAreaIssue.objects.filter(model_name='Course',object_id=str(course.pk),resolved_at=None).exists())
        self.assertEqual(Training.objects.get(course=course).area,IT)

    def test_graphs_use_four_areas_and_keep_unclassified_hours_in_totals(self):
        known=self.training();unknown=self.training(title='Objectivo por comprovar',area='',hours_per_day=4)
        stats=statistics([known,unknown]);segments=distribution([known,unknown])
        self.assertEqual(stats['hours'],6);self.assertEqual(stats['unclassified_hours'],4)
        self.assertEqual([s['label'] for s in segments],[TECHNICAL])
        self.assertAlmostEqual(segments[0]['percent'],100/3)
        result=executive_data([known,unknown],[self.person])
        self.assertEqual([b['label'] for b in result['area_bars']],[TECHNICAL])
        self.assertEqual(result['stats']['hours'],6)

    def test_missing_attendance_is_not_counted_as_a_real_presence(self):
        t=self.training(attendance=None,days=1,hours_per_day=2,attendance_confirmed=True)
        result=executive_data([t],[self.person])
        self.assertEqual(result['stats']['registered_people'],1)
        self.assertEqual(result['directions_table'][0]['present'],0)
        self.assertIsNone(t.completed_hours)
        self.assertIsNone(t.attendance_percent)
        self.assertIsNone(t.absences)
        from .indicators import analyse_indicators
        self.assertFalse(analyse_indicators([t])['reconciliation'][0]['attendance'])

    def test_import_normalizes_known_values_and_reports_invalid_values(self):
        record={'sheet':'PARTICIPACOES','row':2,'legacy_key':'area-test','values':{'number':self.person.number,'title':'Excel','area':'TI / Sistemas','status':'Concluído','attendance':1,'days':1,'hours_per_day':Decimal(2)}}
        self.assertEqual(prepare_training(record,self.person,{'issues':[]})['area'],IT)
        record['values']['area']='Sona Fati'
        with self.assertRaisesMessage(ValueError,'Área de formação não reconhecida'):prepare_training(record,self.person,{'issues':[]})
        record['values']['area']=''
        with self.assertRaisesMessage(ValueError,'(vazia)'):prepare_training(record,self.person,{'issues':[]})

    def test_database_domain_guard_rejects_unvalidated_sql(self):
        course=Course.objects.create(title='Protecção de domínio',area=TECHNICAL)
        with self.assertRaises(IntegrityError),transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('UPDATE training_course SET area=%s WHERE id=%s',['Outra categoria',course.pk])
        course.refresh_from_db();self.assertEqual(course.area,TECHNICAL)

    def test_catalogue_import_retains_raw_evidence_and_normalizes_category(self):
        from .catalogue_import import make_plan
        row=dict(id='TEST-AREA',title='Excel de ensaio',code='AR01',area='T.I. / Sistemas',days=None,hours=None,origin='Fonte de ensaio',designation_state='Catálogo do plano')
        plan=make_plan([row],[],{'alias':'default'})
        values=plan['operations'][0]['values']
        self.assertEqual(values['area'],IT)
        self.assertEqual(values['source_metadata']['area'],'T.I. / Sistemas')
        row['area']='Categoria sem prova'
        plan=make_plan([row],[],{'alias':'default'})
        self.assertTrue(any('Área de formação não reconhecida' in c['reason'] for c in plan['conflicts']))

    def test_pending_list_requires_staff(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        user=get_user_model().objects.create_user('classificador',password='test')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('training_area_issues')).status_code,403)
        user.is_staff=True;user.save()
        Course.objects.create(title='Objectivo ainda não documentado',area='')
        response=self.client.get(reverse('training_area_issues'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Objectivo ainda não documentado')

    def test_course_link_mismatch_is_a_form_error(self):
        course=Course.objects.create(title='Catálogo técnico',area=TECHNICAL)
        form=TrainingForm(data={'course':course.pk,'title':course.title,'area':MANAGEMENT},instance=Training(employee=self.person))
        self.assertFalse(form.is_valid())
        self.assertIn('area',form.errors)

    def test_catalogue_http_decision_updates_all_linked_records(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        user=get_user_model().objects.create_user('gestor-area',password='test',is_staff=True)
        self.client.force_login(user)
        course=Course.objects.create(title='Curso para decisão',area=TECHNICAL)
        training=self.training(course=course)
        data={'code':'','title':course.title,'area':SAFETY,'duration_reason':'Correcção sintética','duration_reference':'SYN-AREA','contents':'','default_days':1,'default_hours':2,'validation_status':course.validation_status,'active':'on','revision':course.revision}
        response=self.client.post(reverse('course_edit',args=[course.pk]),data)
        self.assertEqual(response.status_code,302)
        training.refresh_from_db();self.assertEqual(training.area,SAFETY)
        self.assertEqual(training.grade,Decimal('16'))
        self.assertTrue(TrainingAreaChange.objects.filter(model_name='Course',object_id=str(course.pk),previous_area=TECHNICAL,new_area=SAFETY).exists())

    def test_groups_and_uuid_drafts_follow_category_and_can_be_recovered(self):
        from datetime import time
        from django.contrib.auth import get_user_model
        from .models import TrainingGroup,PlanningDraft
        from .scheduling_guard import reservation_transaction
        from .area_reclassification import reverse
        course=Course.objects.create(title='Combate ao incêndio',area=TECHNICAL)
        with reservation_transaction():
            group=TrainingGroup.objects.create(course=course,number=1,title=course.title,area=TECHNICAL,start=date(2026,1,1),end=date(2026,1,1),days=1,hours_per_day=2,start_time=time(8),end_time=time(10))
        owner=get_user_model().objects.create_user('rascunho')
        draft=PlanningDraft.objects.create(owner=owner,course=course,title=course.title,area=TECHNICAL)
        apply(apps);group.refresh_from_db();draft.refresh_from_db()
        self.assertEqual((group.area,draft.area),(SAFETY,SAFETY))
        reverse(apps);group.refresh_from_db();draft.refresh_from_db();course.refresh_from_db()
        self.assertEqual((group.area,draft.area,course.area),(TECHNICAL,TECHNICAL,TECHNICAL))
