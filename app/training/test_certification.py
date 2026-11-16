from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from django.test import SimpleTestCase
from .certification import certificate_indicators

D = Decimal


def row(employee=1, issued=None, delivered=None, issue_date=None, delivery_date=None):
    return SimpleNamespace(employee_id=employee, certificate_issued=issued, certified=delivered,
                           certificate_issue_date=issue_date, certificate_delivery_date=delivery_date)


class DeliveryRateTests(SimpleTestCase):
    def test_delivered_divided_by_issued_not_by_all_rows(self):
        rows = [row(1, True, True), row(2, True, True), row(3, True, False), row(4, True, None),
                row(5, None, None), row(6, False, None), row(7, None, None)]
        rate = certificate_indicators(rows)['delivery_rate']
        # The old formula gave 2 ÷ 7 = 28.57%.
        self.assertEqual((rate['value'], rate['numerator'], rate['denominator']), (D('50.00'), 2, 4))
        self.assertEqual(rate['status'], 'valid')

    def test_no_issued_certificate_gives_no_data_not_zero(self):
        rate = certificate_indicators([row(issued=None), row(issued=False)])['delivery_rate']
        self.assertIsNone(rate['value'])
        self.assertEqual(rate['status'], 'no_data')

    def test_zero_deliveries_on_issued_set_is_zero_percent(self):
        rate = certificate_indicators([row(issued=True, delivered=False)])['delivery_rate']
        self.assertEqual(rate['value'], D('0.00'))

    def test_delivery_without_confirmed_issue_is_flagged_not_counted(self):
        result = certificate_indicators([row(1, True, True), row(2, None, True)])
        rate = result['delivery_rate']
        self.assertEqual((rate['value'], rate['denominator']), (D('100.00'), 1))
        self.assertEqual(rate['excluded_delivered_without_issue'], 1)
        self.assertEqual(rate['status'], 'partial')
        self.assertEqual(result['delivered'], 2)

    def test_rate_is_not_clamped(self):
        rows = [row(i, True, True) for i in range(3)]
        self.assertEqual(certificate_indicators(rows)['delivery_rate']['value'], D('100.00'))


class CertificateControlTests(SimpleTestCase):
    def test_pending_and_unknown_are_kept_apart(self):
        result = certificate_indicators([row(1, True, False), row(2, True, None), row(3, None, None), row(4, False, None)])
        self.assertEqual((result['to_deliver'], result['delivery_unknown']), (1, 1))
        self.assertEqual((result['issued'], result['not_issued'], result['issue_unknown']), (2, 1, 1))

    def test_people_with_delivery_are_distinct(self):
        result = certificate_indicators([row(1, True, True), row(1, True, True), row(2, True, True)])
        self.assertEqual((result['delivered'], result['people_delivered']), (3, 2))

    def test_issue_rate_without_obtained_state_has_no_data(self):
        rate = certificate_indicators([row(issued=True)])['issue_rate']
        self.assertIsNone(rate['value'])
        self.assertEqual(rate['status'], 'no_data')


class CertificateEventsTests(SimpleTestCase):
    def test_issue_in_one_month_and_delivery_in_another(self):
        rows = [row(1, True, True, date(2026, 1, 20), date(2026, 2, 3))]
        january = certificate_indicators(rows, date(2026, 1, 1), date(2026, 1, 31))
        february = certificate_indicators(rows, date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual((january['issued_in_period'], january['delivered_in_period']), (1, 0))
        self.assertEqual((february['issued_in_period'], february['delivered_in_period']), (0, 1))

    def test_undated_events_are_not_assigned_to_a_month(self):
        rows = [row(1, True, True)]
        result = certificate_indicators(rows, date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual((result['issued_in_period'], result['issued_undated']), (0, 1))
        self.assertEqual((result['delivered_in_period'], result['delivered_undated']), (0, 1))


def obtained_row(employee=1, obtained=None, issued=None, delivered=None):
    item = row(employee, issued, delivered)
    item.certificate_obtained = obtained
    return item


class IssueRateTests(SimpleTestCase):
    def test_issued_divided_by_obtained_on_the_same_set(self):
        rows = [obtained_row(1, True, True), obtained_row(2, True, False), obtained_row(3, True, None),
                obtained_row(4, True, True), obtained_row(5, None, None)]
        result = certificate_indicators(rows)
        rate = result['issue_rate']
        self.assertEqual((rate['value'], rate['numerator'], rate['denominator']), (D('50.00'), 2, 4))
        self.assertEqual((result['to_issue'], result['issue_pending_unknown']), (1, 1))

    def test_no_obtained_confirmation_gives_no_data(self):
        rate = certificate_indicators([obtained_row(issued=True), obtained_row(obtained=False)])['issue_rate']
        self.assertIsNone(rate['value'])
        self.assertEqual(rate['status'], 'no_data')

    def test_legacy_issue_without_obtained_is_flagged_not_inferred(self):
        result = certificate_indicators([obtained_row(1, True, True), obtained_row(2, None, True)])
        rate = result['issue_rate']
        self.assertEqual((rate['value'], rate['denominator'], rate['excluded_issued_without_obtained']), (D('100.00'), 1, 1))
        self.assertEqual(rate['status'], 'partial')
        self.assertEqual(result['obtained_unknown'], 1)

    def test_obtained_is_not_deduced_from_issue_or_delivery(self):
        result = certificate_indicators([obtained_row(1, None, True, True)])
        self.assertEqual(result['obtained'], 0)

    def test_classification_stays_inactive(self):
        self.assertEqual(certificate_indicators([])['classification']['status'], 'inactive')
