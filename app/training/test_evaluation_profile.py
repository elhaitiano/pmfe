from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import (Employee, Competency, RoleProfile, RoleCompetency, EvaluationCycle,
                     CycleRater, CompetencyResponse)
from .competency_services import open_cycle, finish_cycle
from .evaluation_profile import radar_geometry


class EvaluationProfileTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('profile-owner',is_staff=True)
        self.other=get_user_model().objects.create_user('profile-other',is_staff=True)
        self.reader=get_user_model().objects.create_user('profile-reader')
        self.employee=Employee.objects.create(number='RADAR',name='Pessoa teste')
        self.role=RoleProfile.objects.create(title='Função radar')
        for name in ['Técnica <script>unsafe</script>','Equipa','Comunicação']:
            competency=Competency.objects.create(name=name)
            RoleCompetency.objects.create(role=self.role,competency=competency,expected=4,weight=1,
                validated=True,validated_by='RH',validated_on=timezone.localdate())
        self.client.force_login(self.user)
        self.url=reverse('evaluation_profile',args=[self.employee.pk])

    def cycle(self,score,owner=None,close=True):
        owner=owner or self.user
        cycle=EvaluationCycle.objects.create(employee=self.employee,role=self.role,owner=owner,
            moment='Acompanhamento',start=timezone.localdate(),end=timezone.localdate(),
            weight_self=100,weight_manager=0,weight_peer=0,weight_trainer=0)
        rater=CycleRater.objects.create(cycle=cycle,user=owner,kind='self',authorized=True)
        open_cycle(cycle,cycle.revision)
        for item in cycle.items.all():
            CompetencyResponse.objects.create(rater=rater,item=item,score=score,comment='SECRET RAW COMMENT')
        CycleRater.objects.filter(pk=rater.pk).update(submitted_at=timezone.now())
        if close: finish_cycle(cycle,cycle.revision)
        return cycle

    def test_real_radar_comparison_and_escaped_names(self):
        older=self.cycle(2); current=self.cycle(4)
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['comparison']['cycle'].pk,older.pk)
        self.assertEqual(response.context['selected']['cycle'].pk,current.pk)
        self.assertEqual(response.context['items'][0]['delta'],2)
        self.assertContains(response,'profile-radar-current')
        self.assertContains(response,'profile-radar-previous')
        self.assertContains(response,'Técnica &lt;script&gt;unsafe&lt;/script&gt;')
        self.assertNotContains(response,'SECRET RAW COMMENT')
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.assertEqual(len(response.context['strengths']),3)

    def test_no_fake_values_or_disclosure_before_close(self):
        response=self.client.get(self.url)
        self.assertIsNone(response.context['radar'])
        self.assertContains(response,'nenhum valor demonstrativo')
        cycle=self.cycle(5,close=False)
        response=self.client.get(self.url,{'cycle':cycle.pk})
        self.assertIsNone(response.context['radar']['current'])
        self.assertEqual(response.context['strengths'],[])
        self.assertNotContains(response,'class="radar-area profile-radar-current"')
        self.assertNotContains(response,'SECRET RAW COMMENT')

    def test_changed_rules_never_compared(self):
        older=self.cycle(2)
        rule=self.role.rules.first();rule.expected=5;rule.save()
        newer=self.cycle(3)
        response=self.client.get(self.url)
        self.assertIsNone(response.context['comparison'])
        self.assertEqual(response.context['previous_options'],[])
        self.assertEqual(self.client.get(self.url,{'cycle':newer.pk,'previous':older.pk}).status_code,404)

    def test_owner_filters_both_selected_and_previous(self):
        private=self.cycle(2,owner=self.other)
        mine=self.cycle(4)
        response=self.client.get(self.url)
        self.assertEqual(len(response.context['history']),1)
        self.assertEqual(self.client.get(self.url,{'cycle':private.pk}).status_code,404)
        self.assertEqual(self.client.get(self.url,{'cycle':mine.pk,'previous':private.pk}).status_code,404)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)

    def test_navigation_under_evaluation_and_people_without_participations(self):
        response=self.client.get(reverse('evaluation_index'))
        self.assertContains(response,self.url)
        response=self.client.get(reverse('home'))
        self.assertNotContains(response,'>Competências e 360°')
        self.assertContains(response,'id="navAvaliacao"')
        self.assertContains(self.client.get(reverse('competency_index')),'Secções da avaliação')

    def test_geometry_uses_exact_scale_no_synthetic_axes(self):
        values=[{'name':str(i),'score':5,'expected':4} for i in range(3)]
        self.assertIsNone(radar_geometry(values[:2]))
        radar=radar_geometry(values)
        self.assertEqual(radar['axes'][0]['dy'],'65.00')
        self.assertTrue(radar['current'].startswith('220.00,65.00'))
        values[0]['score']=None
        self.assertIsNone(radar_geometry(values)['current'])
