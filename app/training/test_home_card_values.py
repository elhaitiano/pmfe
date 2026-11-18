from datetime import date
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Employee, Training


@override_settings(PMF_VALIDATION_ENABLED=False, PMF_HISTORICAL_ENABLED=False)
class CollaboratorCardValuesTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('qa_card_values',password='test-only')
        self.client.force_login(self.user)

    def people(self, count=3):
        Employee.objects.bulk_create([Employee(number=f'QA-{n}',name=f'Pessoa de ensaio {n}',direction='Unidade de ensaio') for n in range(count)])

    def history(self, employee, state='operational'):
        return Training.objects.create(employee=employee,title='Histórico de ensaio',start=date(2026,1,2),end=date(2026,1,2),days=1,hours_per_day=7,interface_state=state)

    def values(self, **params):
        response=self.client.get(reverse('home'),params);self.assertEqual(response.status_code,200)
        return response,tuple(response.context[key] for key in ['employee_count','trained_count','untrained_count','coverage'])

    def test_1510_official_workers_without_any_history(self):
        self.people(1510);response,values=self.values()
        self.assertEqual(values,(1510,0,1510,0))
        self.assertContains(response,'id="gfTotalEmployees"')
        self.assertContains(response,'id="gfTrainedEmployees">0</b>',html=False)
        self.assertContains(response,'id="gfCoverage">0,0%</b>',html=False)

    def test_no_workers_is_explicit_zero_not_missing(self):
        self.assertEqual(self.values()[1],(0,0,0,0))

    def test_multiple_histories_count_the_person_once_without_attachments(self):
        self.people();e=Employee.objects.first();self.history(e);self.history(e)
        values=self.values()[1];self.assertEqual(values[:3],(3,1,2));self.assertAlmostEqual(values[3],100/3)

    def test_global_denominator_does_not_follow_search_or_status_filter(self):
        self.people();self.history(Employee.objects.first())
        base=self.values()[1]
        for params in [{'q':'no-match'}, {'status':'trained'}, {'status':'untrained'}, {'direction':'Unidade inexistente'}]:
            self.assertEqual(self.values(**params)[1],base)

    def test_preserved_legacy_records_are_not_counted_as_current_history(self):
        self.people();self.history(Employee.objects.first(),'legacy_preserved')
        self.assertEqual(self.values()[1],(3,0,3,0))

    def test_inactive_legacy_employee_is_not_in_official_denominator(self):
        self.people();Employee.all_objects.create(number='OLD-QA',name='Cadastro antigo',registry_active=False)
        self.assertEqual(self.values()[1],(3,0,3,0))

    def test_full_coverage_is_100_and_no_negative_without_history(self):
        self.people(1);self.history(Employee.objects.first())
        self.assertEqual(self.values()[1],(1,1,0,100))

    def test_login_is_still_required(self):
        self.client.logout();self.assertEqual(self.client.get(reverse('home')).status_code,302)
