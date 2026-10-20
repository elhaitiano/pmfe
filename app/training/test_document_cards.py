"""Presentation-only regression tests; all records are synthetic and isolated."""
from datetime import date
from html.parser import HTMLParser
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Employee, Training, PrivateDocument, TrainingDocumentState, AuditEvent
from .test_documents import pdf_bytes


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__();self.tags=[];self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag,dict(attrs)))

    def with_class(self, name):
        return [(tag,attrs) for tag,attrs in self.tags if name in attrs.get('class','').split()]


@override_settings(PMF_DOCUMENTS_ENABLED=True)
class DocumentCardTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser('cards',password='synthetic')
        self.reader=get_user_model().objects.create_user('reader')
        self.staff=get_user_model().objects.create_user('staff',is_staff=True)
        self.person=Employee.objects.create(number='CARD',name='Synthetic worker',phone='000000201',source_data={'Telefone 2':'000000202'})
        self.first=Training.objects.create(employee=self.person,title='Synthetic recent training',start=date(2025,2,1),end=date(2025,2,2),days=2,hours_per_day=7,attendance=2,grade=0,attendance_confirmed=True)
        self.second=Training.objects.create(employee=self.person,title='Synthetic older training',start=date(2025,1,1),end=date(2025,1,3),days=3,hours_per_day=6,attendance=1)
        self.url=reverse('employee_data',args=[self.person.pk]);self.client.force_login(self.admin)

    def test_native_disclosure_first_card_open_and_four_named_blocks(self):
        response=self.client.get(self.url);markup=Markup(response.content.decode())
        cards=markup.with_class('doc-training-card');self.assertEqual(len(cards),2)
        self.assertIn('open',cards[0][1]);self.assertNotIn('open',cards[1][1]);self.assertTrue(all(tag=='details' for tag,attrs in cards))
        self.assertEqual(len(markup.with_class('doc-info-block')),8)
        self.assertEqual(len(markup.with_class('doc-training-summary')),2)
        self.assertContains(response,'GESTÃO DE CERTIFICADOS E AVALIAÇÕES')
        for label in ('Participação comprovada','Nota e resultado','Emissão do certificado','Entrega dos exemplares','Aos Recursos Humanos da EPAL'):
            self.assertContains(response,label)
        self.assertContains(response,'14 H previstas');self.assertContains(response,'18 H previstas')
        self.assertContains(response,'01/02/2025');self.assertContains(response,'02/02/2025')
        self.assertContains(response,'doc-management-title')

    def test_zero_grade_unknown_and_pending_are_preserved(self):
        response=self.client.get(self.url)
        self.assertContains(response,'0 <small>/ 20</small>',html=True)
        self.assertContains(response,'Por confirmar');self.assertContains(response,'Pendente');self.assertContains(response,'Comprovada')
        self.assertContains(response,'doc-status-unknown');self.assertContains(response,'doc-status-pending')
        self.assertContains(response,'aria-hidden="true"');self.assertContains(response,'doc-sr-only')
        self.assertEqual(response.context['document_rows'][0]['grade'],0)

    def test_management_urls_and_permissions_are_unchanged(self):
        response=self.client.get(self.url)
        for training in (self.first,self.second):self.assertContains(response,reverse('document_delivery',args=[training.pk]))
        self.assertContains(response,reverse('document_upload',args=[self.person.pk]))
        for user in (self.reader,self.staff):
            self.client.force_login(user);response=self.client.get(self.url)
            self.assertContains(response,'doc-training-card')
            self.assertNotContains(response,'Registar estados');self.assertNotContains(response,reverse('document_upload',args=[self.person.pk]))
            self.assertEqual(self.client.get(reverse('document_delivery',args=[self.first.pk])).status_code,403)
        self.staff.user_permissions.add(Permission.objects.get(codename='change_trainingdocumentstate'))
        self.client.force_login(self.staff);response=self.client.get(self.url)
        self.assertContains(response,'Registar estados');self.assertEqual(self.client.get(reverse('document_delivery',args=[self.first.pk])).status_code,200)

    def test_private_archive_groups_types_and_retains_authorised_links(self):
        docs=[]
        for kind,training in [('cv',None),('certificate',self.first),('certificate',self.second)]:
            docs.append(PrivateDocument.objects.create(employee=self.person,training=training,kind=kind,content=pdf_bytes(),sha256='synthetic',size=1,actor='synthetic',reference='Synthetic source'))
        response=self.client.get(self.url);markup=Markup(response.content.decode())
        self.assertEqual(len(markup.with_class('doc-file-group')),2);self.assertEqual(len(markup.with_class('doc-file-card')),3)
        for doc in docs:
            url=reverse('document_download',args=[doc.pk]);self.assertContains(response,url);self.assertContains(response,url+'?view=1')
        self.client.force_login(self.reader);response=self.client.get(self.url)
        self.assertNotContains(response,'Consultar documentos privados')
        for doc in docs:self.assertNotContains(response,str(doc.pk))

    def test_deliveries_keep_distinct_dates_and_responsibles_and_no_writes(self):
        TrainingDocumentState.objects.create(training=self.first,issued=True,learner_delivered=True,learner_date=date(2025,2,3),learner_responsible='Synthetic learner delivery',hr_delivered=True,hr_date=date(2025,2,4),hr_responsible='Synthetic HR delivery',reference='Synthetic record',observations='Preserved evidence')
        models=(Employee,Training,TrainingDocumentState,PrivateDocument,AuditEvent)
        before={model.__name__:list(model.objects.order_by('pk').values()) for model in models}
        response=self.client.get(self.url)
        for text in ('03/02/2025','04/02/2025','Synthetic learner delivery','Synthetic HR delivery','Synthetic record','Preserved evidence'):
            self.assertContains(response,text)
        self.assertEqual(before,{model.__name__:list(model.objects.order_by('pk').values()) for model in models})

    def test_contacts_are_adjacent_and_history_hours_have_uppercase_unit(self):
        import re
        from html import unescape
        response=self.client.get(self.url);html=response.content.decode()
        start=html.index('ficha-contact-values');end=html.index('E-mail',start)
        pair=html[start:end]
        self.assertIn('000 000 201',pair);self.assertIn('000 000 202',pair);self.assertIn('Tel. 2',pair)
        history=html[html.index('ficha-history'):html.index('doc-management')]
        self.assertIn('14,0 H</td>',history)
        for first,second,expected in [
            ('000000101','000000102','Tel. 1: 000 000 101 | Tel. 2: 000 000 102'),
            ('000000101','','Tel.: 000 000 101'),
            ('','','Tel.: —'),
            ('','000000102','Tel.: 000 000 102'),
        ]:
            with self.subTest(first=first,second=second):
                self.person.phone=first
                self.person.source_data={'Telefone 2':second}
                self.person.save()
                before=Employee.objects.filter(pk=self.person.pk).values().get()
                response=self.client.get(self.url)
                fragment=re.search(r'<div class="ficha-contact-values">(.*?)</div>',response.content.decode(),re.S).group(1)
                text=' '.join(unescape(re.sub(r'<[^>]+>',' ',fragment)).split())
                self.assertEqual(text,expected)
                markup=Markup(fragment)
                self.assertEqual(len(markup.with_class('ficha-contact-item')),2 if first and second else 1)
                if not (first and second):self.assertNotIn('Tel. 2',text)
                self.assertEqual(Employee.objects.filter(pk=self.person.pk).values().get(),before)

    def test_empty_sections_and_disabled_feature_do_not_expose_actions(self):
        self.first.delete();self.second.delete()
        response=self.client.get(self.url);self.assertContains(response,'Sem participações registadas')
        self.assertContains(response,'Nenhum documento arquivado.')
        with override_settings(PMF_DOCUMENTS_ENABLED=False):
            response=self.client.get(self.url);self.assertNotContains(response,'doc-training-card');self.assertNotContains(response,'GESTÃO DE CERTIFICADOS')
