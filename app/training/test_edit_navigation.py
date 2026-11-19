from datetime import date
from urllib.parse import urlencode,urlsplit,parse_qs
from django.test import TestCase,RequestFactory,override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse,resolve
from .models import Employee,Training
from .forms import TrainingForm
from .edit_navigation import safe_return,origin,context

@override_settings(PMF_VALIDATION_ENABLED=False,PMF_HISTORICAL_ENABLED=False)
class EditNavigationTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('qa_nav',password='test-only',is_staff=True)
        self.client.force_login(self.user)
        self.employee=Employee.objects.create(number='QA-N1',name='Pessoa fictícia')
        self.training=Training.objects.create(employee=self.employee,title='Formação fictícia',area='Segurança',start=date(2026,1,2),end=date(2026,1,3),days=2,hours_per_day=7,attendance=1,attendance_confirmed=True,status='Por confirmar')
        self.url=reverse('training_edit',args=[self.training.pk]);self.group='/planeamento/grupos/321/?q=cloro&page=2#formandos-registados'
    def data(self,**changes):
        form=TrainingForm(instance=self.training,initial={'revision':self.training.version});data={}
        for name in form.fields:
            value=form[name].value()
            if isinstance(value,bool):
                if value:data[name]='on'
            else:data[name]=value.isoformat() if isinstance(value,date) else '' if value is None else str(value)
        data.update(return_to=self.group,observations='Correcção fictícia',**changes);return data
    def test_safe_return_preserves_query_and_anchor(self):
        request=RequestFactory().get(self.url)
        self.assertEqual(safe_return(request,self.group),self.group)
    def test_external_logout_edit_and_malformed_targets_rejected(self):
        request=RequestFactory().get(self.url)
        for url in ['https://evil.example/','//evil.example/','/sair/','/entrar/',self.url,'/admin/','/\\evil','javascript:alert(1)','/planeamento/\n']:
            with self.subTest(url=url):self.assertEqual(safe_return(request,url),'')
    def test_nested_profile_referer_recovers_group(self):
        ref='http://testserver/funcionarios/1/?'+urlencode({'return_to':self.group})
        request=RequestFactory().get(self.url,HTTP_REFERER=ref)
        self.assertEqual(origin(request),self.group)
    def test_filtered_list_sets_own_origin_without_javascript(self):
        request=RequestFactory().get('/avaliacao/?q=cloro&page=3');request.resolver_match=resolve('/avaliacao/')
        self.assertEqual(context(request)['origin'],'/avaliacao/?q=cloro&page=3')
    def test_edit_get_has_cancel_and_both_save_choices(self):
        response=self.client.get(self.url,{'return_to':self.group})
        self.assertEqual(response.status_code,200);self.assertContains(response,'name="return_to"');self.assertContains(response,'Guardar e voltar ao grupo')
    def test_valid_save_shows_choice_and_commits_once(self):
        response=self.client.post(self.url,self.data())
        self.assertEqual(response.status_code,302);self.assertTrue(response.url.startswith('/navegacao/guardado/'))
        self.training.refresh_from_db();self.assertEqual(self.training.observations,'Correcção fictícia');self.assertEqual(self.training.version,2)
        saved=self.client.get(response.url);self.assertContains(saved,'Guardado com sucesso');self.assertEqual(saved.context['saved_navigation']['target'],self.group)
        self.client.get(response.url);self.training.refresh_from_db();self.assertEqual(self.training.version,2)
    def test_save_and_return_preserves_filters_and_success_message(self):
        response=self.client.post(self.url,self.data(after_save='return'))
        self.assertEqual(response.url,self.group)
    def test_invalid_form_stays_and_retains_origin(self):
        response=self.client.post(self.url,self.data(attendance='99'))
        self.assertEqual(response.status_code,200);self.assertContains(response,'name="return_to"');self.training.refresh_from_db();self.assertEqual(self.training.version,1)
    def test_stale_revision_does_not_show_success(self):
        Training.objects.filter(pk=self.training.pk).update(version=3)
        response=self.client.post(self.url,self.data())
        self.assertEqual(response.status_code,200);self.assertContains(response,'noutra janela');self.training.refresh_from_db();self.assertEqual(self.training.version,3)
    def test_no_origin_preserves_previous_destination(self):
        data=self.data();data.pop('return_to')
        response=self.client.post(self.url,data);self.assertEqual(response.url,reverse('employee_detail',args=[self.employee.pk]))
    def test_invalid_explicit_origin_cannot_use_referer_as_bypass(self):
        request=RequestFactory().post(self.url,{'return_to':'https://evil.example'},HTTP_REFERER='http://testserver'+self.group)
        self.assertEqual(origin(request),'')
    def test_saved_choice_is_bound_to_user_and_cannot_be_forged(self):
        response=self.client.post(self.url,self.data());url=response.url
        other=get_user_model().objects.create_user('qa_other',password='test-only',is_staff=True);self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code,400)
        self.assertEqual(self.client.get('/navegacao/guardado/?token=forged').status_code,400)
    def test_two_tabs_keep_independent_destinations(self):
        first=self.client.get(self.url,{'return_to':self.group});second=self.client.get(self.url,{'return_to':'/avaliacao/?q=outro&page=4'})
        self.assertEqual(first.context['edit_navigation']['origin'],self.group)
        self.assertEqual(second.context['edit_navigation']['origin'],'/avaliacao/?q=outro&page=4')
    def test_non_staff_still_cannot_edit(self):
        self.user.is_staff=False;self.user.save(update_fields=['is_staff'])
        self.assertEqual(self.client.post(self.url,self.data()).status_code,403)
