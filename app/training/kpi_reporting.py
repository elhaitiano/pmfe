"""Shared presentation metadata for HTML, CSV, Excel and PDF."""
from dataclasses import asdict, is_dataclass

STATUS = {'valid': 'Confirmado', 'partial': 'Parcial', 'to_verify': 'Por verificar',
          'no_data': 'Sem dados', 'inconsistent': 'Inconsistente'}
LABELS = {'hours': ('Horas frequentadas', 'horas-participante'),
          'equivalent_days': ('H/D equivalentes', 'dias de 7 horas'),
          'participations': ('Participações efectivas', 'participações'),
          'people': ('Colaboradores com participação confirmada', 'pessoas'),
          'coverage': ('Cobertura do cadastro actual', '%'),
          'hours_per_person': ('Horas por colaborador participante', 'horas/pessoa'),
          'attendance': ('Assiduidade ponderada por horas', '%')}
REASONS = {'legacy_without_daily_dates':'Histórico sem datas diárias fiáveis', 'unverified_session':'Sessões com presença/duração por verificar',
           'session_identity_or_state':'Sessão incompatível com participação', 'invalid_session_hours':'Horas de sessão inconsistentes', 'planned': 'Formação planeada', 'unverified_attendance': 'Presenças por verificar',
           'missing_attendance': 'Presenças desconhecidas', 'missing_duration': 'Duração desconhecida',
           'invalid_attendance': 'Presenças inválidas', 'invalid_duration': 'Duração inválida',
           'attendance_exceeds_planned': 'Presenças excedem o previsto',
           'duplicate_identity': 'Identidade de participação repetida', 'conflicting_versions': 'Versões em conflito',
           'planned_with_attendance': 'Formação planeada com presença', 'group_course_mismatch': 'Grupo e formação incompatíveis',
           'missing_employee_identity': 'Funcionário não identificado', 'outside_reference_people': 'Pessoas fora do cadastro de referência'}


def describe_summary(summary):
    return [dict(key=key, label=('Cobertura do efectivo DRH aprovado' if key=='coverage' and summary.scope.reference.startswith('DRH #') else LABELS[key][0]), unit=LABELS[key][1], value=m.value,
        status=STATUS[m.status], numerator=m.numerator, denominator=m.denominator,
        included=m.included, excluded=m.excluded,
        reasons='; '.join(f'{REASONS.get(reason, reason)}: {count}' for reason,count in m.reasons.items()))
        for key,m in summary.metrics.items()]


def export_rows(summary):
    """The same definitions and precision in all exported evidence tables."""
    rows = [['Base temporal', summary.scope.time_basis], ['Período', summary.scope.period],
            ['Referência', summary.scope.reference], ['Fonte', summary.source],
            ['Participações sem repartição diária', summary.unallocated_participations],
            ['Dias efectivos completos', summary.effective_days if summary.effective_days is not None else 'Por verificar'],
            ['Dias verificados (subtotal)', len(summary.verified_day_pairs)]]
    rows.extend([f'Cadastro — {key} por confirmar',value] for key,value in summary.incomplete_registry.items())
    rows.extend([f'Filtro: {key}', str(value)] for key,value in summary.scope.filters.items() if value not in ('', None, []))
    rows.append(['Indicador', 'Valor', 'Unidade', 'Estado', 'Numerador', 'Denominador', 'Incluídos', 'Excluídos', 'Motivos'])
    for item in describe_summary(summary):
        rows.append([item['label'], item['value'] if item['value'] is not None else 'Sem dados', item['unit'],
            item['status'], item['numerator'], item['denominator'],item['included'],item['excluded'],item['reasons']])
    return rows


def archive_summary(summary):
    # JSON-safe immutable snapshot; retain structured identity lists and filters.
    from datetime import date
    from decimal import Decimal
    def serialize(value):
        if isinstance(value, dict):
            return {str(k): serialize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [serialize(v) for v in sorted(value)] if isinstance(value, (set, frozenset)) else [serialize(v) for v in value]
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, date):
            return value.isoformat()
        if hasattr(value, 'pk'):
            return value.pk
        return value
    return serialize(asdict(summary) if is_dataclass(summary) else summary)

def outcome_rows(academic):
    return [['Coorte terminada — referência', str(academic['reference_date'])],
        ['Conclusão (%)', academic['completion_rate'], 'Concluídas', academic['completion_count'], 'Determinadas', academic['completion_determined'], 'Pendentes', academic['completion_pending'], 'Elegíveis', academic['ended_count']],
        ['Aproveitamento (%)', academic['approval_rate'], 'Aprovadas', academic['approved'], 'Determinadas', academic['result_count'], 'Pendentes', academic['result_pending']],
        ['Logística /5', academic['logistics'], 'Equivalente (%)', academic['logistics_percent'], 'Respostas', academic['logistics_count'], 'Agregados excluídos', academic['logistics_excluded_groups']]]

DEFINITIONS = {
    'people':'Distinct Employee identities with positive verified attendance in the selected period.',
    'hours':'Sum of verified attended session hours; unallocated legacy totals are separate.',
    'attendance':'Sum of verified attended hours / matching planned hours × 100; confirmed zero included.',
    'equivalent_days':'Verified participant hours / 7; round only after aggregation.',
    'coverage':'Distinct confirmed participants / exact approved DRH reference or labelled current registry × 100.',
    'completion':'Explicit validated completions / determined eligible ended participations × 100.',
    'approval':'Validated approved results / determined approved and failed results × 100.',
    'certificate_issue':'Issued / obtained in the same identified obtained cohort at the reference date × 100.',
    'certificate_delivery':'Delivered / issued in that same obtained cohort at the reference date × 100.',
    'classification':'Inactive until an institutional comparable classification scale is defined.',
    'privacy':'Groups of 1–4: withhold aggregate release and complementary totals; nominal rights remain separate.',
}
