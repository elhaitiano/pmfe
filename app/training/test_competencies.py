from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from .models import (Employee, Training, Course, Competency, RoleProfile, RoleCompetency,
    EvaluationCycle, CycleRater, CycleCompetency, CompetencyResponse, DevelopmentAction,
    PrivateResponseRevision, AuditEvent)
from .competency_services import open_cycle, finish_cycle, cycle_results, RevisionConflict


class CompetencyTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.owner = get_user_model().objects.create_user('responsavel', is_staff=True)
        self.other = get_user_model().objects.create_user('outro-admin', is_staff=True)
        self.reader = get_user_model().objects.create_user('leitor')
        self.client.force_login(self.owner)
        self.employee = Employee.objects.create(number='C360', name='Pessoa fictícia', function='Texto histórico')
        self.role = RoleProfile.objects.create(title='Função de referência')
        self.skill = Competency.objects.create(name='Competência de teste', dimension='Técnica')
        self.rule = RoleCompetency.objects.create(role=self.role, competency=self.skill, expected=5,
            weight=3, validated=True, validated_by='RH fictício', validated_on=self.today)

    def cycle(self, peers=3, coverage=100):
        cycle = EvaluationCycle.objects.create(employee=self.employee, role=self.role, owner=self.owner,
            moment='Acompanhamento', start=self.today, end=self.today+timedelta(days=7), minimum_coverage=coverage)
        for kind, count in [('self',1),('manager',1),('peer',peers),('trainer',1)]:
            for n in range(count):
                user = get_user_model().objects.create_user(f'{cycle.pk}-{kind}-{n}')
                CycleRater.objects.create(cycle=cycle,user=user,kind=kind,authorized=True)
        return cycle

    def open(self, cycle):
        open_cycle(cycle, cycle.revision)
        return cycle

    def submit(self, rater, score=3, comment='Segredo individual', action='submit'):
        self.client.force_login(rater.user)
        data = {'revision':rater.revision, 'reason':'Revisão privada', 'action':action}
        for item in rater.cycle.items.all():
            data[f'item_{item.pk}'] = score
            data[f'item_{item.pk}_comment'] = comment
        response = self.client.post(reverse('cycle_respond',args=[rater.code]),data)
        rater.refresh_from_db()
        return response

    def closed(self, cycle, score=3):
        self.open(cycle)
        for rater in cycle.raters.select_related('user','cycle'):
            self.assertEqual(self.submit(rater,score).status_code,302)
        cycle.refresh_from_db()
        finish_cycle(cycle,cycle.revision)
        self.client.force_login(self.owner)
        return cycle

    def test_configuration_validation_and_identity_links(self):
        cycle = self.cycle()
        cycle.weight_self = 11
        with self.assertRaises(ValidationError): cycle.full_clean()
        cycle.weight_self = 10
        cycle.minimum_peers = 2
        with self.assertRaises(ValidationError): cycle.full_clean()
        cycle.minimum_peers = 3
        cycle.end = cycle.start-timedelta(days=1)
        with self.assertRaises(ValidationError): cycle.full_clean()
        another = Employee.objects.create(number='OTHER', name='Outra pessoa')
        training = Training.objects.create(employee=another,title='Outro curso',start=self.today,end=self.today,
            days=1,hours_per_day=7,attendance=0)
        cycle.end = cycle.start
        cycle.training = training
        with self.assertRaises(ValidationError): cycle.full_clean()

    def test_open_requires_validated_rules_types_and_minimum_peers(self):
        cycle = self.cycle(peers=2)
        with self.assertRaises(ValidationError): self.open(cycle)
        cycle.refresh_from_db()
        self.assertEqual(cycle.state,'draft')
        self.assertFalse(cycle.items.exists())
        self.assertEqual(cycle.revision,1)
        peer=get_user_model().objects.create_user('extra-peer')
        CycleRater.objects.create(cycle=cycle,user=peer,kind='peer',authorized=True)
        self.rule.validated=False; self.rule.save()
        with self.assertRaises(ValidationError): self.open(cycle)
        self.rule.validated=True; self.rule.save()
        cycle.refresh_from_db()
        self.open(cycle)
        self.assertEqual(cycle.state,'open')
        self.assertEqual(cycle.config['employee_function'],'Texto histórico')
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.function,'Texto histórico')

    def test_missing_type_does_not_renormalize_and_inactive_account_rejected(self):
        cycle = self.cycle()
        trainer=cycle.raters.get(kind='trainer'); trainer.authorized=False; trainer.save()
        with self.assertRaises(ValidationError): self.open(cycle)
        cycle.refresh_from_db()
        cycle.weight_trainer=0; cycle.weight_manager=65; cycle.save()
        self.open(cycle)
        self.assertEqual(cycle.config['weights']['trainer'],'0.00')
        second=self.cycle(); r=second.raters.first(); r.user.is_active=False; r.user.save()
        with self.assertRaises(ValidationError): self.open(second)

    def test_snapshots_remain_after_catalogue_changes(self):
        cycle=self.open(self.cycle())
        self.skill.name='Novo nome'; self.skill.save()
        self.rule.expected=1; self.rule.weight=8; self.rule.save()
        item=cycle.items.get()
        self.assertEqual(item.name,'Competência de teste')
        self.assertEqual(item.expected,5)
        self.assertEqual(item.weight,3)
        with self.assertRaises(ValidationError): item.save()
        with self.assertRaises(ValidationError): cycle.save()
        with self.assertRaises(ValidationError): cycle.raters.first().save()
        self.assertEqual(self.client.get(reverse('cycle_edit',args=[cycle.pk])).status_code,409)

    def test_weighted_scores_gaps_and_relative_competency_weights(self):
        second=Competency.objects.create(name='Outra competência')
        RoleCompetency.objects.create(role=self.role,competency=second,expected=5,weight=1,
            validated=True,validated_by='RH',validated_on=self.today)
        cycle=self.open(self.cycle())
        for rater in cycle.raters.select_related('user','cycle'):
            self.client.force_login(rater.user)
            values={'self':2,'manager':3,'peer':4,'trainer':5}
            data={'revision':rater.revision,'reason':'Primeira resposta','action':'submit'}
            for item in cycle.items.all(): data[f'item_{item.pk}']=values[rater.kind] if item.competency_id==self.skill.pk else 5
            self.assertEqual(self.client.post(reverse('cycle_respond',args=[rater.code]),data).status_code,302)
        self.assertFalse(cycle_results(cycle)['available'])
        cycle.refresh_from_db(); finish_cycle(cycle,cycle.revision)
        result=cycle_results(cycle)
        self.assertTrue(result['available'])
        self.assertEqual(result['items'][0]['score'],Decimal('3.65'))
        self.assertEqual(result['items'][0]['gap'],Decimal('1.35'))
        self.assertEqual(result['overall'],Decimal('3.9875'))

    def test_insufficient_peer_threshold_and_coverage_withhold_scores(self):
        for peers,coverage,submitted,expected in [(3,50,2,False),(4,100,3,False),(4,75,3,True)]:
            with self.subTest(peers=peers,coverage=coverage):
                cycle=self.open(self.cycle(peers=peers,coverage=coverage))
                for kind in ['self','manager','trainer']:
                    self.submit(cycle.raters.get(kind=kind),4)
                for rater in cycle.raters.filter(kind='peer')[:submitted]: self.submit(rater,4)
                cycle.refresh_from_db(); finish_cycle(cycle,cycle.revision)
                result=cycle_results(cycle)
                self.assertEqual(result['available'],expected)
                if not expected:
                    self.assertIsNone(result['overall'])
                    self.assertIsNone(result['items'][0]['score'])
                    self.assertIsNone(result['items'][0]['gap'])

    def test_private_responses_never_visible_to_manager_other_staff_or_peer(self):
        cycle=self.open(self.cycle())
        rater=cycle.raters.filter(kind='peer').first()
        self.assertEqual(self.submit(rater,2).status_code,302)
        own=self.client.get(reverse('cycle_respond',args=[rater.code]))
        self.assertContains(own,'Segredo individual')
        self.assertEqual(own['Cache-Control'],'private, no-store')
        self.assertFalse(AuditEvent.objects.filter(detail__contains='Segredo individual').exists())
        self.assertTrue(PrivateResponseRevision.objects.filter(rater=rater,payload__after__0__comment='Segredo individual').exists())
        for user in [self.owner,self.other,self.reader,cycle.raters.get(kind='self').user]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('cycle_respond',args=[rater.code])).status_code,404)
            self.assertEqual(self.client.post(reverse('cycle_respond',args=[rater.code]),{}).status_code,404)
        self.client.force_login(self.owner)
        detail=self.client.get(reverse('cycle_detail',args=[cycle.pk]))
        self.assertNotContains(detail,'Segredo individual')
        self.assertContains(detail,'após o fecho')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('cycle_detail',args=[cycle.pk])).status_code,404)
        self.assertNotContains(self.client.get(reverse('indicator_index')),f'Ciclo #{cycle.pk}')

    def test_drafts_corrections_stale_save_submission_and_date_limits(self):
        cycle=self.open(self.cycle())
        rater=cycle.raters.first()
        self.assertEqual(self.submit(rater,2,action='save').status_code,302)
        self.assertIsNone(rater.submitted_at)
        self.assertEqual(self.submit(rater,4,action='save').status_code,302)
        self.assertEqual(rater.private_history.count(),2)
        self.assertEqual(rater.responses.get().score,4)
        old={'revision':1,'reason':'Antiga','action':'save'}
        self.assertEqual(self.client.post(reverse('cycle_respond',args=[rater.code]),old).status_code,409)
        self.assertEqual(self.submit(rater,4).status_code,302)
        self.assertEqual(self.submit(rater,1).status_code,409)
        self.assertEqual(rater.responses.get().score,4)
        with self.assertRaises(ValidationError): rater.responses.get().save()
        future=self.cycle(); future.start=self.today+timedelta(days=1); future.save(); self.open(future)
        self.assertEqual(self.submit(future.raters.first()).status_code,409)

    def test_missing_invalid_scores_and_foreign_competency(self):
        cycle=self.open(self.cycle()); rater=cycle.raters.first()
        self.client.force_login(rater.user)
        url=reverse('cycle_respond',args=[rater.code]); item=cycle.items.get()
        for score in ['',0,6]:
            data={'revision':1,'reason':'Teste','action':'submit',f'item_{item.pk}':score}
            response=self.client.post(url,data)
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.context['form'].errors)
        self.assertFalse(rater.responses.exists())
        another=self.open(self.cycle())
        with self.assertRaises(ValidationError): CompetencyResponse(rater=rater,item=another.items.get(),score=3).full_clean()

    def test_cancel_and_close_reject_obsolete_transitions(self):
        cycle=self.open(self.cycle()); revision=cycle.revision
        self.submit(cycle.raters.first(),3)
        with self.assertRaises(RevisionConflict): finish_cycle(cycle,revision)
        cycle.refresh_from_db(); finish_cycle(cycle,cycle.revision,cancel=True)
        self.assertFalse(cycle_results(cycle)['available'])
        self.assertEqual(self.submit(cycle.raters.last()).status_code,409)
        with self.assertRaises(ValidationError): finish_cycle(cycle,cycle.revision)

    def test_audit_failure_rolls_back_answers_and_opening(self):
        cycle=self.cycle()
        with patch('training.competencies.record',side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse('cycle_detail',args=[cycle.pk]),
                    {'revision':cycle.revision,'reason':'Abrir','confirm':'on','action':'open'})
        cycle.refresh_from_db(); self.assertEqual(cycle.state,'draft'); self.assertFalse(cycle.items.exists())
        self.open(cycle); rater=cycle.raters.first()
        with patch('training.competencies.record',side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError): self.submit(rater)
        self.assertFalse(rater.responses.exists())
        self.assertFalse(rater.private_history.exists())
        rater.refresh_from_db(); self.assertIsNone(rater.submitted_at)

    def test_catalogue_cycle_and_authorization_forms(self):
        response=self.client.post(reverse('competency_create'),{'name':'Nova competência','dimension':'D',
            'description':'Descrição','active':'on','revision':1,'reason':'Criar catálogo'})
        self.assertEqual(response.status_code,302)
        role_data={'title':'Nova função','active':'on','revision':1,'reason':'Definir regras',
            'rules-TOTAL_FORMS':1,'rules-INITIAL_FORMS':0,'rules-0-competency':self.skill.pk,
            'rules-0-expected':4,'rules-0-weight':2,'rules-0-validated':'on',
            'rules-0-validated_by':'RH','rules-0-validated_on':self.today.isoformat()}
        response=self.client.post(reverse('role_create'),role_data)
        self.assertEqual(response.status_code,302)
        role=RoleProfile.objects.get(title='Nova função')
        self.assertEqual(role.rules.get().expected,4)
        data={'employee':self.employee.pk,'role':role.pk,'moment':'Acompanhamento','start':self.today,
            'end':self.today+timedelta(days=3),'weight_self':100,'weight_manager':0,'weight_peer':0,
            'weight_trainer':0,'minimum_coverage':100,'minimum_peers':3,'revision':1,'reason':'Preparação'}
        response=self.client.post(reverse('cycle_create'),data)
        self.assertEqual(response.status_code,302)
        cycle=EvaluationCycle.objects.get()
        raters={'revision':cycle.revision,'raters-TOTAL_FORMS':1,'raters-INITIAL_FORMS':0,
            'raters-0-user':self.reader.pk,'raters-0-kind':'self','raters-0-authorized':'on'}
        response=self.client.post(reverse('cycle_raters',args=[cycle.pk]),raters)
        self.assertEqual(response.status_code,302)
        cycle.refresh_from_db()
        response=self.client.post(reverse('cycle_detail',args=[cycle.pk]),
            {'revision':cycle.revision,'reason':'Identidade confirmada','confirm':'on','action':'open'})
        self.assertEqual(response.status_code,302)
        cycle.refresh_from_db(); self.assertEqual(cycle.state,'open')

    def test_catalogue_uniqueness_and_rh_validation(self):
        data={'name':self.skill.name.upper(),'revision':1,'reason':'Duplicado','active':'on'}
        self.assertEqual(self.client.post(reverse('competency_create'),data).status_code,200)
        self.assertEqual(Competency.objects.count(),1)
        rule=RoleCompetency(role=self.role,competency=self.skill,validated=True,expected=3,weight=1)
        with self.assertRaises(ValidationError): rule.full_clean()
        with self.assertRaises(IntegrityError),transaction.atomic():
            CycleRater.objects.bulk_create([])
            Competency.objects.create(name=self.skill.name.upper())

    def test_development_requires_closed_gap_and_review_and_preserves_scores(self):
        cycle=self.open(self.cycle()); item=cycle.items.get()
        self.client.force_login(self.owner)
        data={'revision':1,'reason':'Planear','action':'Exercícios acompanhados','responsible':'Chefia',
            'due':self.today+timedelta(days=10),'state':'Planeada'}
        url=reverse('development_create',args=[item.pk])
        response=self.client.post(url,data)
        self.assertTrue(response.context['form'].errors)
        for rater in cycle.raters.all(): self.submit(rater,3)
        cycle.refresh_from_db(); finish_cycle(cycle,cycle.revision)
        self.client.force_login(self.owner)
        course=Course.objects.create(title='Formação recomendada')
        data['course']=course.pk
        self.assertEqual(self.client.post(url,data).status_code,302)
        action=DevelopmentAction.objects.get()
        self.assertEqual(action.item_id,item.pk)
        edit=reverse('development_edit',args=[item.pk,action.pk])
        data.update(revision=action.revision,state='Concluída')
        response=self.client.post(edit,data)
        self.assertTrue(response.context['form'].errors)
        data.update(review_date=self.today,review='Evidência de realização')
        self.assertEqual(self.client.post(edit,data).status_code,302)
        self.assertEqual(self.client.post(edit,data).status_code,409)
        self.assertEqual(cycle_results(cycle)['overall'],3)
        self.assertContains(self.client.get(reverse('development_index')),'Evidência de realização')
        from .planning import course_deletion_allowed
        self.assertFalse(course_deletion_allowed(course))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(edit).status_code,404)

    def test_permissions_csrf_and_page_rendering(self):
        cycle=self.cycle()
        staff_urls=[reverse('competency_index'),reverse('competency_catalog'),reverse('competency_create'),
            reverse('role_create'),reverse('cycle_create'),reverse('cycle_detail',args=[cycle.pk]),
            reverse('cycle_edit',args=[cycle.pk]),reverse('cycle_raters',args=[cycle.pk]),
            reverse('development_index'),reverse('indicator_index')]
        for url in staff_urls: self.assertEqual(self.client.get(url).status_code,200,url)
        csrf=Client(enforce_csrf_checks=True); csrf.force_login(self.owner)
        for url in staff_urls: self.assertEqual(csrf.post(url,{}).status_code,403,url)
        self.client.force_login(self.reader)
        for url in staff_urls: self.assertEqual(self.client.get(url).status_code,403,url)
        self.assertEqual(self.client.get(reverse('my_ratings')).status_code,200)
        self.client.logout()
        for url in staff_urls: self.assertEqual(self.client.get(url).status_code,302,url)

    def test_response_csrf_no_weight_change_after_open_and_frozen_results_pages(self):
        cycle=self.closed(self.cycle(),score=3)
        response=self.client.get(reverse('cycle_detail',args=[cycle.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Planear desenvolvimento')
        self.assertNotContains(response,'Segredo individual')
        response=self.client.get(reverse('indicator_index'))
        self.assertEqual(response.context['competency_count'],1)
        self.assertEqual(response.context['competency_average'],3)
        self.assertNotContains(response,'Segredo individual')
        rater=cycle.raters.first()
        csrf=Client(enforce_csrf_checks=True); csrf.force_login(rater.user)
        self.assertEqual(csrf.post(reverse('cycle_respond',args=[rater.code]),{}).status_code,403)

    def test_evaluator_command_no_privileges_password_validation_and_no_secret_in_audit(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO
        secret='Test-only-Evaluator-7392-long!'
        with patch('training.management.commands.create_evaluator.getpass',side_effect=[secret,secret]):
            call_command('create_evaluator','new-reviewer',stdout=StringIO())
        user=get_user_model().objects.get(username='new-reviewer')
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password(secret))
        self.assertFalse(AuditEvent.objects.filter(detail__contains=secret).exists())
        with patch('training.management.commands.create_evaluator.getpass',side_effect=['short','short']):
            with self.assertRaises(CommandError): call_command('create_evaluator','rejected',stdout=StringIO())
        self.assertFalse(get_user_model().objects.filter(username='rejected').exists())
