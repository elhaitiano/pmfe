from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from .models import Course
from .planning import CourseForm


class CourseCodeTests(TestCase):
    def test_normalisation_duplicates_and_legacy_courses(self):
        Course.objects.create(title='Antiga A')
        Course.objects.create(title='Antiga B')
        course = Course.objects.create(title='Curso', code=' capt ')
        self.assertEqual(course.code, 'CAPT')
        form = CourseForm(data={'title': 'Outro', 'code': 'capt', 'default_days': 1, 'default_hours': 7})
        self.assertFalse(form.is_valid())
        self.assertIn('code', form.errors)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Course.objects.create(title='Duplicado', code='CAPT')

    def test_rename_preserves_code_aliases_and_search(self):
        self.client.force_login(get_user_model().objects.create_user('editor', is_staff=True))
        course = Course.objects.create(title='Nome anterior', code='CAPT')
        response = self.client.post(reverse('course_edit', args=[course.pk]), {
            'code': 'TENTATIVA', 'title': 'Nome corrigido', 'default_days': 1,
            'default_hours': 7, 'active': 'on', 'revision': course.revision})
        self.assertEqual(response.status_code, 302)
        course.refresh_from_db()
        self.assertEqual(course.code, 'CAPT')
        self.assertIn('Nome anterior', course.aliases)
        self.assertEqual(course.title, 'Nome corrigido')
        response = self.client.get(reverse('planning'), {'q': 'capt'})
        self.assertContains(response, 'Nome corrigido')
        self.assertContains(response, 'CAPT')

    def test_assign_code_to_legacy_course(self):
        course = Course.objects.create(title='Histórico')
        form = CourseForm(instance=course, data={'title': course.title, 'code': ' lgde ',
            'default_days': 1, 'default_hours': 7})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().code, 'LGDE')
