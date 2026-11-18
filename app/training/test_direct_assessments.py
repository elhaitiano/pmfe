from datetime import date, time
from decimal import Decimal as D
from io import BytesIO
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from .models import Course, Employee, Enrollment, Training, AssessmentScale, DirectAssessment, AuditEvent
from .scheduling import save_group
from .planning import group_deletion_allowed
from .direct_assessment_metrics import normalise, mention, metrics
from .direct_assessments import scope_rows


@override_settings(PMF_SIMPLIFIED_PLANNING_ENABLED=True, PMF_HISTORICAL_ENABLED=False)
class DirectAssessmentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('assessment-qa', 'qa@example.invalid', 'test-only')
        self.client.force_login(self.user)
        self.course = Course.objects.create(title='Curso fictício de avaliação', validation_status='pending')
        self.group = save_group(self.course.pk, dict(dates=[date(2026, 11, 20)], start=date(2026, 11, 20), end=date(2026, 11, 20),
            trainer='Formador fictício', provider='EPAL', location='Local de ensaio', capacity=30, hours_per_day=D(7), break_hours=D(1), start_time=time(8), end_time=time(16)), actor='qa')
        self.e = Enrollment.objects.create(group=self.group, employee=Employee.objects.create(number='QA-1', name='Pessoa de ensaio'), confirmation='Confirmado')
        self.url = reverse('direct_assessments') + f'?group={self.group.pk}'

    def payload(self, initial='10', final='16', satisfaction='5', **extra):
        response = self.client.get(self.url)
        data = {'token': response.context['token'], 'initial_max': '20', 'final_max': '20',
            f'{self.e.pk}-initial_points': initial, f'{self.e.pk}-final_points': final, f'{self.e.pk}-satisfaction': satisfaction}
        data.update(extra); return data

    def test_zero_missing_negative_and_normalisation(self):
        self.assertEqual(normalise(D(0), 60), 0); self.assertIsNone(normalise(None, 60))
        self.assertEqual(normalise(D(30), 60), 10)
        m = metrics([dict(initial=D(15), final=D(10), satisfaction=None), dict(initial=None, final=D(20), satisfaction=5)])
        self.assertEqual(m['evolution'], -5); self.assertEqual(m['paired_final'], 10)
        self.assertEqual(m['final'], 15); self.assertEqual(m['responses'], 1); self.assertEqual(m['satisfaction'], 5)

    def test_mentions_use_unrounded_thresholds(self):
        for value, expected in [('9.999', 'Insuficiente'), ('10', 'Suficiente'), ('14', 'Bom'), ('16', 'Muito Bom'), ('18', 'Excelente')]:
            self.assertEqual(mention(D(value)), expected)
        self.assertEqual(mention(None), 'Sem avaliação')

    def test_weighted_people_not_group_means(self):
        rows = [dict(initial=D(0), final=D(v), satisfaction=None) for v in (10, 10, 10, 20)]
        self.assertEqual(metrics(rows)['final'], D('12.5'))

    def test_empty_summary_unknown_not_zero(self):
        self.assertIsNone(metrics([])['evolution']); self.assertIsNone(metrics([])['satisfaction'])

    def test_save_without_attendance_or_attachment(self):
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        self.assertEqual(DirectAssessment.objects.get().final_points, 16)
        self.assertEqual(Training.objects.count(), 0)
        self.assertTrue(AuditEvent.objects.filter(action='DIRECT_ASSESSMENTS_SAVED').exists())
        self.assertFalse(group_deletion_allowed(self.group))

    def test_repeated_submission_does_not_duplicate(self):
        data = self.payload(); self.client.post(self.url, data); self.client.post(self.url, data)
        self.assertEqual(DirectAssessment.objects.count(), 1)

    def test_stale_page_cannot_overwrite(self):
        data = self.payload(); self.client.post(self.url, data)
        data[f'{self.e.pk}-final_points'] = '20'; data['reason'] = 'Correction'
        self.client.post(self.url, data)
        self.assertEqual(DirectAssessment.objects.get().final_points, 16)

    def test_correction_requires_reason_and_audits_previous(self):
        self.client.post(self.url, self.payload())
        self.client.post(self.url, self.payload(final='18'))
        self.assertEqual(DirectAssessment.objects.get().final_points, 16)
        self.assertEqual(self.client.post(self.url, self.payload(final='18', reason='Correcção de digitação')).status_code, 302)
        self.assertEqual(DirectAssessment.objects.get().final_points, 18)
        audit = AuditEvent.objects.filter(action='DIRECT_ASSESSMENTS_SAVED').first()
        self.assertIn('before', audit.detail); self.assertIn('16.00', audit.detail)

    def test_clear_existing_value_requires_reason(self):
        self.client.post(self.url, self.payload())
        self.client.post(self.url, self.payload(final=''))
        self.assertEqual(DirectAssessment.objects.get().final_points, 16)

    def test_scale_cannot_reinterpret_existing_scores(self):
        self.client.post(self.url, self.payload())
        self.client.post(self.url, self.payload(initial_max='100', reason='Tentativa de alteração'))
        self.assertEqual(AssessmentScale.objects.get().initial_max, 20)

    def test_invalid_score_and_satisfaction_atomic(self):
        for fields in [dict(final='21'), dict(initial='-1'), dict(satisfaction='6'), dict(final='NaN'), dict(final='1.001')]:
            self.client.post(self.url, self.payload(**fields)); self.assertFalse(DirectAssessment.objects.exists())
        self.assertFalse(AssessmentScale.objects.exists())

    def test_zero_is_valid(self):
        self.client.post(self.url, self.payload(initial='0', final='0', satisfaction=''))
        row = scope_rows(Enrollment.objects.select_related('group__assessment_scale', 'direct_assessment', 'employee'))[0]
        self.assertEqual(row['evolution'], 0); self.assertEqual(row['mention'], 'Insuficiente')

    def test_blank_new_record_not_created(self):
        self.client.post(self.url, self.payload(initial='', final='', satisfaction=''))
        self.assertFalse(DirectAssessment.objects.exists())

    def test_invalid_signature_and_cross_user_rejected(self):
        data = self.payload(token='invalid'); self.client.post(self.url, data)
        self.assertFalse(DirectAssessment.objects.exists())
        data = self.payload()
        other = get_user_model().objects.create_superuser('other-qa', '', 'test-only'); self.client.force_login(other)
        self.client.post(self.url, data); self.assertFalse(DirectAssessment.objects.exists())

    def test_roster_change_invalidates_token(self):
        data = self.payload()
        Enrollment.objects.create(group=self.group, employee=Employee.objects.create(number='QA-2', name='Outra pessoa'))
        self.client.post(self.url, data); self.assertFalse(DirectAssessment.objects.exists())

    def test_reader_and_nonstaff_permissions(self):
        u = get_user_model().objects.create_user('reader-qa', password='test-only', is_staff=True)
        self.client.force_login(u); self.assertEqual(self.client.get(self.url).status_code, 403)
        u.user_permissions.add(*Permission.objects.filter(codename__in=['view_employee', 'view_training', 'view_traininggroup']))
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, {}).status_code, 403)
        u.is_staff = False; u.save(); self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_mismatch_course_and_bad_id(self):
        c = Course.objects.create(title='Outro curso')
        self.assertEqual(self.client.get(self.url + f'&course={c.pk}').status_code, 400)
        self.assertEqual(self.client.get(reverse('direct_assessments') + '?group=bad').status_code, 400)

    def test_excel_contains_computed_numbers_and_pdf(self):
        from openpyxl import load_workbook
        self.client.post(self.url, self.payload())
        r = self.client.get(self.url + '&export=xlsx'); book = load_workbook(BytesIO(r.content), data_only=True)
        self.assertEqual(book['Resultados']['D2'].value, 10); self.assertEqual(book['Resultados']['F2'].value, 6)
        r = self.client.get(self.url + '&export=pdf'); self.assertEqual(r.status_code, 200); self.assertTrue(r.content.startswith(b'%PDF'))

    def test_course_summary_is_not_mean_of_group_means(self):
        self.client.post(self.url, self.payload())
        other = save_group(self.course.pk, dict(dates=[date(2026, 11, 21)], start=date(2026, 11, 21), end=date(2026, 11, 21),
            trainer='Outro formador', provider='EPAL', location='Outro local', capacity=30, hours_per_day=D(6), break_hours=D(1), start_time=time(8), end_time=time(15)), actor='qa')
        AssessmentScale.objects.create(group=other, initial_max=20, final_max=20)
        for n in range(3):
            e = Enrollment.objects.create(group=other, employee=Employee.objects.create(number=f'QA-MULTI-{n}', name=f'Ensaio {n}'))
            DirectAssessment.objects.create(enrollment=e, initial_points=0, final_points=12, updated_by='qa')
        r = self.client.get(reverse('direct_assessments') + f'?course={self.course.pk}')
        self.assertEqual(r.context['summary']['evolution'], D('10.5'))
        self.assertEqual(r.context['summary']['final'], 13); self.assertFalse(r.context['editable'])

    def test_independent_initial_and_final_scales(self):
        self.client.post(self.url, self.payload(initial='30', final='60', initial_max='60', final_max='100'))
        r = self.client.get(self.url)
        self.assertEqual(r.context['summary']['initial'], 10)
        self.assertEqual(r.context['summary']['final'], 12)
        self.assertEqual(r.context['summary']['evolution'], 2)

    def test_inactive_enrollment_excluded_without_deleting_results(self):
        self.client.post(self.url, self.payload())
        self.e.state = 'Cancelada'; self.e.save()
        self.assertEqual(self.client.get(self.url).context['summary']['total'], 0)
        self.assertEqual(DirectAssessment.objects.count(), 1)

    def test_navigation_and_no_certificate_approval(self):
        r = self.client.get(self.url)
        self.assertContains(r, 'Estas menções não atribuem certificados.')
        self.assertContains(self.client.get(reverse('evaluation_index')), reverse('direct_assessments'))

    def test_cancelled_group_preserves_results_but_excludes_kpis(self):
        from .scheduling_guard import reservation_transaction
        self.client.post(self.url, self.payload())
        with reservation_transaction():
            self.group.state = 'Cancelada'; self.group.save()
        r = self.client.get(self.url)
        self.assertEqual(len(r.context['page']), 1); self.assertEqual(r.context['summary']['total'], 0)
        self.assertFalse(r.context['editable']); self.assertEqual(self.client.post(self.url, {}).status_code, 403)

    def test_audit_failure_rolls_back_scores_and_scale(self):
        from unittest.mock import patch
        data = self.payload()
        with patch('training.direct_assessments.AuditEvent.objects.create', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaises(RuntimeError): self.client.post(self.url, data)
        self.assertFalse(DirectAssessment.objects.exists()); self.assertFalse(AssessmentScale.objects.exists())

    def test_html_and_excel_formula_injection_neutralised(self):
        from openpyxl import load_workbook
        self.e.employee.name = '=HYPERLINK("https://example.invalid")'; self.e.employee.save()
        self.client.post(self.url, self.payload())
        r = self.client.get(self.url + '&export=xlsx'); cell = load_workbook(BytesIO(r.content))['Resultados']['B2']
        self.assertEqual(cell.data_type, 's'); self.assertTrue(cell.value.startswith("'="))
