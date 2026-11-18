from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from training.models import TrainingGroup, Enrollment, GroupSession, Course


class AttendanceNavigationTests(TestCase):
    def test_reader_can_find_excel_access_without_creating_groups(self):
        self.assertEqual(self.client.get(reverse('planning')).status_code,302)
        user=get_user_model().objects.create_user('synthetic-list-reader',password=None)
        self.client.force_login(user)
        course=Course.objects.create(title='Synthetic list navigation')
        response=self.client.get(reverse('planning'))
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'id="grupos"')
        self.assertContains(response,reverse('course_groups',args=[course.pk]))
        response=self.client.get(reverse('course_groups',args=[course.pk]))
        self.assertContains(response,'Lista de Presença — Excel')
        self.assertContains(response,'href="#grupos"')
        self.assertContains(response,'id="grupos"')
        self.assertContains(response,'Para gerar uma lista de presença, seleccione ou crie uma turma com calendário e formandos inscritos')
        for model in (TrainingGroup,Enrollment,GroupSession):self.assertEqual(model.objects.count(),0)
