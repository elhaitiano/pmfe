from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase, Client
from django.urls import reverse
from .models import (Employee, Training, TrainingEvaluation, ReactionEvaluation, Exam,
                     ExamQuestion, ExamAttempt, ExamAnswer, AuditEvent)


class EvaluationTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('avaliador', is_staff=True)
        self.reader = get_user_model().objects.create_user('leitor')
        self.client.force_login(self.staff)
        self.employee = Employee.objects.create(number='TEST-EVAL', name='Pessoa fictícia')
        self.training = Training.objects.create(employee=self.employee, title='Curso fictício',
            start=date(2026, 1, 1), end=date(2026, 1, 2), days=2, hours_per_day=7,
            attendance=2, status='Concluído')

    def evidence(self, revision=1, **extra):
        return dict(revision=revision, correction_reason='Verificação documental',
            date='2026-01-03', evaluator='Avaliador fictício', source='Documento de teste',
            validation='Validado', **extra)

    def make_exam(self):
        exam = Exam.objects.create(training=self.training, title='Prova fictícia', pass_percent=60)
        q1 = ExamQuestion.objects.create(exam=exam, number=1, prompt='Primeira', expected_answer='A', points=2)
        q2 = ExamQuestion.objects.create(exam=exam, number=2, prompt='Segunda', expected_answer='B', points=3)
        return exam, q1, q2

    def start(self, exam, reason=''):
        exam.refresh_from_db()
        return self.client.post(reverse('attempt_create', args=[exam.pk]), {'revision': exam.revision, 'reason': reason})

    def mark_data(self, attempt, q1, q2, **extra):
        data = self.evidence(attempt.revision, action='finalize')
        data.update({f'q{q1.pk}_response': 'A', f'q{q1.pk}_awarded': '2', f'q{q1.pk}_comment': '',
                     f'q{q2.pk}_response': 'C', f'q{q2.pk}_awarded': '1', f'q{q2.pk}_comment': 'Parcial'})
        data.update(extra)
        return data

    def test_routes_permissions_csrf_and_navigation(self):
        exam, q1, q2 = self.make_exam()
        self.start(exam)
        attempt = exam.attempts.get()
        urls = [reverse('evaluation_index'), reverse('evaluation_detail', args=[self.training.pk]),
            reverse('evaluation_edit', args=[self.training.pk]), reverse('reaction_edit', args=[self.training.pk]),
            reverse('exam_create', args=[self.training.pk]), reverse('attempt_create', args=[exam.pk]),
            reverse('attempt_edit', args=[attempt.pk])]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get(reverse('evaluation_index')), 'Pessoa fictícia')
        centre = self.client.get(reverse('evaluation_index'))
        sidebar = centre.content.decode().split('<aside', 1)[1].split('</aside>', 1)[0]
        for route in ['indicator_index', 'development_index', 'my_ratings']:
            self.assertNotIn('href="' + reverse(route) + '"', sidebar)
            self.assertContains(centre, 'href="' + reverse(route) + '"')
            self.assertContains(self.client.get(reverse(route)), 'Secções da avaliação')
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.staff)
        for url in urls:
            self.assertEqual(csrf.post(url, {}).status_code, 403)
        self.client.force_login(self.reader)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {}).status_code, 403)
        home = self.client.get(reverse('home'))
        self.assertContains(home, 'id="navAvaliacao"')
        personal = self.client.get(reverse('my_ratings'))
        self.assertContains(personal, 'Minhas avaliações')
        self.assertNotContains(personal, 'href="' + reverse('indicator_index') + '"')
        self.assertNotContains(personal, 'href="' + reverse('development_index') + '"')
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_learning_keeps_unknown_and_separates_logistics_and_final_grade(self):
        url = reverse('evaluation_edit', args=[self.training.pk])
        data = self.evidence(logistics='4.5', grade='', result='Não avaliado', comments='Logística apenas')
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.training.refresh_from_db()
        self.assertIsNone(self.training.grade)
        self.assertEqual(self.training.result, 'Não avaliado')
        self.assertEqual(self.training.evaluation.logistics, Decimal('4.5'))
        self.assertEqual(self.client.post(url, data).status_code, 409)
        data.update(revision=self.training.version, grade='16', result='Aprovado')
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.training.refresh_from_db()
        self.assertEqual(self.training.grade, 16)
        self.assertTrue(AuditEvent.objects.filter(action='UPDATE_FINAL_RESULT', detail__contains='BEFORE').exists())
        self.assertContains(self.client.get(reverse('evaluation_detail', args=[self.training.pk])), 'Logística')

    def test_invalid_final_grade_evidence_and_unfinished_training(self):
        url = reverse('evaluation_edit', args=[self.training.pk])
        for extra in [dict(grade='21'), dict(logistics='0'), dict(source=''), dict(date='2999-01-01')]:
            data = self.evidence(grade='10', result='Aprovado', logistics='3')
            data.update(extra)
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['form'].errors)
        self.assertFalse(TrainingEvaluation.objects.exists())
        Training.objects.filter(pk=self.training.pk).update(status='Planeado')
        response = self.client.post(url, self.evidence(grade='10', result='Aprovado', logistics='3'))
        self.assertTrue(response.context['form'].errors)
        self.assertFalse(TrainingEvaluation.objects.exists())

    def test_reaction_nullable_scores_range_and_audited_correction(self):
        url = reverse('reaction_edit', args=[self.training.pk])
        data = self.evidence(organization='5', content='', trainer='3', usefulness='4', comments='Comentário')
        self.assertEqual(self.client.post(url, data).status_code, 302)
        reaction = ReactionEvaluation.objects.get()
        self.assertIsNone(reaction.content)
        self.training.refresh_from_db()
        self.assertIsNone(self.training.grade)
        self.assertEqual(self.training.result, 'Pendente')
        data.update(revision=self.training.version, organization='6')
        response = self.client.post(url, data)
        self.assertTrue(response.context['form'].errors)
        data['organization'] = '4'
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(ReactionEvaluation.objects.get().organization, 4)
        self.assertEqual(self.client.post(url, data).status_code, 409)
        with self.assertRaises(ProtectedError):
            self.training.delete()
        self.assertEqual(self.client.post(reverse('training_delete', args=[self.training.pk])).status_code, 302)
        self.assertTrue(Training.objects.filter(pk=self.training.pk).exists())

    def test_exam_form_questions_edit_then_freeze(self):
        url = reverse('exam_create', args=[self.training.pk])
        data = self.evidence(title='Prova formulário', kind='Final', pass_percent='50')
        data.update({'questions-TOTAL_FORMS': '1', 'questions-INITIAL_FORMS': '0',
            'questions-0-number': '1', 'questions-0-prompt': 'Pergunta',
            'questions-0-expected_answer': 'Resposta', 'questions-0-points': '10'})
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        exam = Exam.objects.get()
        self.assertEqual(exam.maximum, 10)
        edit = reverse('exam_edit', args=[self.training.pk, exam.pk])
        self.assertEqual(self.client.get(edit).status_code, 200)
        self.start(exam)
        self.assertEqual(self.client.get(edit).status_code, 409)
        self.assertEqual(self.client.post(edit, data).status_code, 409)
        other = Training.objects.create(employee=self.employee, title='Outro', start=date(2026,1,1),
            end=date(2026,1,2), days=2, hours_per_day=7, attendance=0)
        self.assertEqual(self.client.get(reverse('exam_edit', args=[other.pk, exam.pk])).status_code, 404)

    def test_attempt_scoring_threshold_and_authorized_retake(self):
        exam, q1, q2 = self.make_exam()
        self.assertEqual(self.start(exam).status_code, 302)
        attempt = exam.attempts.get()
        self.assertIsNone(attempt.percent)
        self.assertEqual(self.start(exam, 'Trop tôt').status_code, 400)
        url = reverse('attempt_edit', args=[attempt.pk])
        data = self.mark_data(attempt, q1, q2)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        attempt.refresh_from_db()
        self.assertEqual(attempt.total, 3)
        self.assertEqual(attempt.maximum, 5)
        self.assertEqual(attempt.percent, 60)
        self.assertEqual(attempt.grade20, 12)
        self.assertEqual(attempt.result, 'Aprovado')
        self.training.refresh_from_db()
        self.assertIsNone(self.training.grade)
        self.assertEqual(self.training.result, 'Pendente')
        self.assertContains(self.client.get(url), 'Aprovado')
        self.assertEqual(self.client.post(url, data).status_code, 409)
        self.assertEqual(self.start(exam).status_code, 400)
        self.assertEqual(self.start(exam, 'Nova avaliação autorizada').status_code, 302)
        second = exam.attempts.get(number=2)
        self.assertEqual(second.authorized_by, self.staff.username)
        data = self.mark_data(second, q1, q2, **{f'q{q1.pk}_awarded': '0', f'q{q2.pk}_awarded': '0'})
        self.client.post(reverse('attempt_edit', args=[second.pk]), data)
        second.refresh_from_db()
        self.assertEqual(second.percent, 0)
        self.assertEqual(second.result, 'Reprovado')
        attempt.refresh_from_db()
        self.assertEqual(attempt.result, 'Aprovado')

    def test_missing_score_not_zero_and_invalid_points_no_partial_save(self):
        exam, q1, q2 = self.make_exam()
        self.start(exam)
        attempt = exam.attempts.get()
        url = reverse('attempt_edit', args=[attempt.pk])
        for value in ['', '-1', '3']:
            data = self.mark_data(attempt, q1, q2, **{f'q{q1.pk}_awarded': value})
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['form'].errors)
            self.assertFalse(attempt.answers.filter(awarded__isnull=False).exists())
        data = self.mark_data(attempt, q1, q2, action='save', **{f'q{q1.pk}_awarded': ''})
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 409)
        attempt.refresh_from_db()
        self.assertFalse(attempt.finalized)
        self.assertIsNone(attempt.total)

    def test_invalid_links_model_validation_and_empty_exam(self):
        exam, q1, q2 = self.make_exam()
        empty = Exam.objects.create(training=self.training, title='Vazia')
        self.assertEqual(self.start(empty).status_code, 400)
        self.start(exam)
        attempt = exam.attempts.get()
        other_q = ExamQuestion.objects.create(exam=empty, number=1, prompt='X', expected_answer='Y', points=1)
        with self.assertRaises(ValidationError):
            ExamAnswer(attempt=attempt, question=other_q).full_clean()
        with self.assertRaises(ValidationError):
            ExamAttempt(exam=exam, number=2).full_clean()
        with self.assertRaises(ValidationError):
            ExamAnswer(attempt=attempt, question=q1, awarded=3).full_clean()

    def test_audit_failure_rolls_back_learning_and_attempt_creation(self):
        url = reverse('evaluation_edit', args=[self.training.pk])
        with patch('training.evaluations.record', side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError):
                self.client.post(url, self.evidence(logistics='4', grade='12', result='Aprovado'))
        self.training.refresh_from_db()
        self.assertIsNone(self.training.grade)
        self.assertEqual(self.training.version, 1)
        self.assertFalse(TrainingEvaluation.objects.exists())
        exam, _, _ = self.make_exam()
        with patch('training.evaluations.record', side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError):
                self.start(exam)
        self.assertFalse(ExamAttempt.objects.exists())
        self.assertFalse(ExamAnswer.objects.exists())

    def test_normal_model_saves_preserve_frozen_questions_and_final_results(self):
        exam, q1, q2 = self.make_exam()
        self.start(exam)
        for obj in [exam, q1]:
            with self.assertRaises(ValidationError):
                obj.save()
        attempt = exam.attempts.get()
        self.client.post(reverse('attempt_edit', args=[attempt.pk]), self.mark_data(attempt, q1, q2))
        attempt.refresh_from_db()
        with self.assertRaises(ValidationError):
            attempt.save()
        with self.assertRaises(ValidationError):
            attempt.answers.first().save()
