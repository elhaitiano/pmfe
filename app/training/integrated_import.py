"""Reviewable, all-or-nothing historical import. Preview never writes model rows."""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import connection, transaction

from .excel_sources import (
    EMPLOYEE_FIELDS, TRAINING_FIELDS, digest, issue, normalized, read_sheet, text,
)
from .models import AuditEvent, Course, Employee, Training

EMPLOYEE_SHEETS = ['FUNCIONARIOS', 'FUNCIONARIOS_EPAL', 'BASE_EPAL', 'FUNCIONÁRIOS_ EPAL']
TRAINING_SHEETS = ['PARTICIPACOES', 'BASE_MESTRA_FORMACAO', 'Formandos']


@dataclass
class ImportPlan:
    report: dict
    employees: list
    trainings: list


def location(record):
    return {'sheet': record['sheet'], 'row': record['row']}


def add_issue(report, record, reason, severity='error', **extra):
    issue(report, severity, record['sheet'], record['row'], reason, **extra)


def database_digest():
    # Binds approval to the catalogue, identities, historical data and revisions reviewed.
    return digest({model._meta.label: list(model.objects.order_by('pk').values())
                   for model in (Employee, Course, Training)})


def provenance(records, sha):
    return {'sha256': sha, 'sources': [dict(**location(r), cells=r['raw']) for r in records]}


def totals(values):
    values = list(values)
    hours = sum((Decimal(v['attendance']) * v['hours_per_day'] for v in values
                 if v.get('hours_per_day') is not None), Decimal(0))
    planned = sum((Decimal(v['days']) * v['hours_per_day'] for v in values
                   if v.get('days') is not None and v.get('hours_per_day') is not None), Decimal(0))
    return {
        'participations': len(values),
        'attendance_declared': sum(v['attendance'] for v in values),
        'completed_hours_declared': str(hours),
        'planned_hours_declared': str(planned),
        'unknown_hours': sum(v.get('hours_per_day') is None for v in values),
        'attendance_confirmed': sum(v.get('attendance_confirmed', False) for v in values),
        'certificate_deliveries_confirmed': sum(v.get('certified') is True for v in values),
        'certificate_declarations': sum(bool(v.get('certificate_declaration')) for v in values),
    }


def prepare_training(record, employee, report):
    v = dict(record['values'])
    number, pid = v.pop('number', ''), v.pop('participation_id', '')
    if not number or employee is None:
        raise ValueError('Número mecanográfico sem cadastro reconciliado.')
    key = 'pilar3b:' + pid if pid else record['legacy_key']
    v['source_key'] = key
    v['source_course_code'] = v.get('source_course_code', '').upper()
    status = normalized(v.get('status', 'Por confirmar'))
    status_map = {'concluida': 'Concluído', 'concluido': 'Concluído', 'emcurso': 'Em Curso',
                  'naoiniciada': 'Planeado', 'planeado': 'Planeado', 'desistencia': 'Desistência',
                  'porconfirmar': 'Por confirmar'}
    if status not in status_map:
        raise ValueError('Situação da participação não reconhecida.')
    v['status'] = status_map[status]
    plan = v.get('plan_status', 'Por confirmar')
    if plan not in ('No Plano', 'Fora do Plano', 'Por confirmar'):
        add_issue(report, record, 'Estado do plano inválido conservado na origem; fica Por confirmar.', 'warning', value=plan)
        plan = 'Por confirmar'
    v['plan_status'] = plan
    v.setdefault('attendance', 0)
    if 'attendance' not in record['values']:
        raise ValueError('Presenças em falta; não serão convertidas em zero.')
    for field in ('days', 'hours_per_day', 'grade'):
        v.setdefault(field, None)
    confirmation = normalized(v.get('attendance_confirmed', ''))
    if confirmation not in ('', 'sim', 'nao', 'true', 'false', '1', '0'):
        raise ValueError('Confirmação das presenças inválida.')
    v['attendance_confirmed'] = confirmation in ('sim', 'true', '1')
    delivered = normalized(v.pop('certified', ''))
    if delivered not in ('', 'sim', 'nao', 'porconfirmar'):
        raise ValueError('Estado de entrega do certificado não reconhecido.')
    v['certified'] = True if delivered == 'sim' else False if delivered == 'nao' else None
    if v['certified'] is True and not v.get('certificate_delivery_date'):
        declaration = v.get('certificate_declaration', '')
        v['certificate_declaration'] = declaration or 'Entrega declarada na origem, sem data documental.'
        v['certificate_declaration_source'] = v.get('certificate_declaration_source') or f"{record['sheet']}, linha {record['row']}"
        v['certified'] = None
        add_issue(report, record, 'Entrega sem data conservada como declaração, não como entrega confirmada.', 'warning')
    code = v['source_course_code']
    if code:
        course = Course.objects.filter(code__iexact=code).first()
        if course:
            titles = [course.title, *(course.aliases or [])]
            if normalized(v.get('title')) not in {normalized(t) for t in titles}:
                raise ValueError('Código do catálogo corresponde a outro título; rever a ligação.')
            v['course_id'] = course.pk
        else:
            add_issue(report, record, 'Código sem correspondência no catálogo; participação conservada sem ligação.', 'warning', code=code)
    if v.get('area') and normalized(v['area']) == normalized(v.get('trainer')):
        add_issue(report, record, 'Categoria igual ao nome do formador; conservada apenas na origem.', 'warning')
        v['area'] = ''
    if v.get('provider', '').isdigit():
        add_issue(report, record, 'Entidade formadora numérica; conservada apenas na origem.', 'warning')
        v['provider'] = ''
    return v


def build_plan(workbook, source_sha256):
    report = {
        'mode': 'dry-run', 'source_sha256': source_sha256, 'database_sha256': database_digest(),
        'employees_created': 0, 'employees_updated': 0, 'employees_existing': 0,
        'trainings_created': 0, 'trainings_existing': 0,
        'issues': [], 'changes': [], 'sheets': [], 'duplicates_reconciled': 0,
        'source_policy': 'FUNCIONARIOS > FUNCIONARIOS_EPAL > BASE_EPAL; PARTICIPACOES > BASE_MESTRA_FORMACAO. Valores secundários completam lacunas; conflitos são apresentados.',
    }
    employee_records, training_records = {}, {}
    sheets = {normalized(name): name for name in workbook.sheetnames}
    used = set()
    if len(sheets) != len(workbook.sheetnames):
        issue(report, 'error', '', None, 'Nomes de folhas ambíguos após normalização.')
    for candidates, mapping, required, target in (
        (EMPLOYEE_SHEETS, EMPLOYEE_FIELDS, {'number', 'name'}, employee_records),
        (TRAINING_SHEETS, TRAINING_FIELDS, {'number', 'title'}, training_records),
    ):
        for name in candidates:
            if name == 'Formandos' and training_records:
                continue
            actual = sheets.get(normalized(name))
            if not actual or actual in used:
                continue
            used.add(actual)
            records = read_sheet(workbook[actual], mapping, required, workbook.epoch, report)
            seen, occurrences = set(), {}
            for record in records:
                if target is employee_records:
                    identifier = record['values'].get('number')
                else:
                    identifier = record['values'].get('participation_id')
                    if not identifier and normalized(actual) == 'formandos':
                        raw_hash = digest(record['raw'])
                        occurrences[raw_hash] = occurrences.get(raw_hash, 0) + 1
                        identifier = f'{raw_hash}:{occurrences[raw_hash]}'
                        record['legacy_key'] = identifier
                    elif not identifier:
                        add_issue(report, record, 'ID Participação obrigatório para o histórico integrado.')
                if not identifier:
                    add_issue(report, record, 'Identificador em falta.')
                    continue
                if identifier in seen:
                    add_issue(report, record, 'Identificador repetido na mesma folha.', identifier=identifier)
                seen.add(identifier)
                target.setdefault(identifier, []).append(record)
    report['ignored_sheets'] = [name for name in workbook.sheetnames if name not in used]
    if not employee_records:
        issue(report, 'error', '', None, 'Nenhum cadastro de trabalhadores reconhecido.')
    employees, objects = [], {}
    # Fill only missing profile information from participation rows, consistently across all rows.
    supplements = {}
    for records in training_records.values():
        primary = records[0]
        headers, raw = list(primary['raw']), list(primary['raw'].values())
        for field in ('company', 'cost_center', 'registration_validation'):
            names = {normalized(name) for name in EMPLOYEE_FIELDS[field]}
            value = next((text(v) for h, v in zip(headers, raw) if normalized(h) in names and text(v)), '')
            if value:
                supplements.setdefault(primary['values'].get('number'), {}).setdefault(field, set()).add(value)
    for number, records in employee_records.items():
        primary = records[0]
        merged = dict(primary['values'])
        for other in records[1:]:
            report['duplicates_reconciled'] += 1
            for field, value in other['values'].items():
                old = merged.get(field)
                if old in (None, ''):
                    merged[field] = value
                elif old != value:
                    severity = 'error' if field in ('name', 'identity_number', 'social_security_number') else 'warning'
                    add_issue(report, other, 'Fontes de cadastro discordam; prioridade da fonte principal.', severity,
                              identifier=number, field=field, selected=str(old), alternative=str(value))
        for field, values in supplements.get(number, {}).items():
            if not merged.get(field) and len(values) == 1:
                merged[field] = next(iter(values))
            elif len(values) > 1 or (merged.get(field) and any(v != merged[field] for v in values)):
                add_issue(report, primary, 'Dados de cadastro nas participações discordam; não substituem o cadastro principal.', 'warning', field=field, values=sorted(values))
        old = Employee.objects.filter(number=number).first()
        obj = Employee.objects.get(pk=old.pk) if old else Employee(number=number)
        proposed = {}
        for field, value in merged.items():
            before = getattr(obj, field)
            if value in ('', None) or before == value:
                continue
            if old and before not in ('', None):
                add_issue(report, primary, 'Valor existente protegido; rever antes de importar.', field=field,
                          identifier=number, before=str(before), proposed=str(value))
                continue
            setattr(obj, field, value)
            proposed[field] = {'before': before, 'after': value}
        obj.source_data = {**(old.source_data if old else {}), **{k: v for k, v in primary['raw'].items() if v not in ('', None)},
                           '_import': provenance(records, source_sha256)}
        try:
            obj.full_clean()
        except ValidationError as error:
            add_issue(report, primary, str(error), identifier=number)
        kind = 'create' if old is None else 'update' if proposed else 'existing'
        report[{'create': 'employees_created', 'update': 'employees_updated', 'existing': 'employees_existing'}[kind]] += 1
        report['changes'].append(dict(entity='employee', identifier=number, operation=kind,
                                      source=location(primary), fields=proposed))
        employees.append((obj, kind))
        objects[number] = obj
    trainings, reconciled = [], []
    for pid, records in training_records.items():
        primary = records[0]
        for other in records[1:]:
            if primary['values'] != other['values'] or {
                normalized(k): text(v) for k, v in primary['raw'].items()
            } != {normalized(k): text(v) for k, v in other['raw'].items()}:
                add_issue(report, other, 'Cópias da participação discordam; corrigir as fontes antes de importar.', identifier=pid)
            else:
                report['duplicates_reconciled'] += 1
        number = primary['values'].get('number')
        try:
            v = prepare_training(primary, objects.get(number), report)
            obj = Training(employee=objects[number], **v)
            obj.source_data = {**primary['raw'], '_import': provenance(records, source_sha256)}
            obj.full_clean(exclude=['employee'], validate_unique=False)
            old = Training.objects.filter(source_key=v['source_key']).first()
            differences = {}
            if old:
                if old.employee.number != number:
                    raise ValueError('Participação já associada a outro trabalhador.')
                for field, value in v.items():
                    before = getattr(old, field)
                    if value not in ('', None) and before != value:
                        differences[field] = {'before': before, 'after': value}
                if differences:
                    add_issue(report, primary, 'Participação existente difere da origem; nenhuma substituição automática.', identifier=pid,
                              fields={k: {a: str(b) for a, b in d.items()} for k, d in differences.items()})
                kind = 'existing'
            else:
                kind = 'create'
            report['trainings_existing' if old else 'trainings_created'] += 1
            report['changes'].append(dict(entity='training', identifier=pid, employee=number,
                                          operation=kind, source=location(primary),
                                          fields={k: {'before': None, 'after': value} for k, value in v.items()} if not old else differences))
            trainings.append((obj, kind, number))
            reconciled.append(v)
        except (ValueError, ValidationError) as error:
            add_issue(report, primary, str(error), identifier=pid)
    report['source_counts'] = {'employees': len(employee_records), 'participations': len(training_records)}
    report['totals'] = totals(reconciled)
    report['errors'] = sum(i['severity'] == 'error' for i in report['issues'])
    report['warnings'] = sum(i['severity'] == 'warning' for i in report['issues'])
    report['can_apply'] = report['errors'] == 0
    report['review_token'] = digest(report)
    # JSON-safe report, usable both by the command and the HTML preview.
    report = json.loads(json.dumps(report, ensure_ascii=False, default=str))
    return ImportPlan(report, employees, trainings)


def snapshot_database():
    db = str(connection.settings_dict['NAME'])
    if db == ':memory:' or db.startswith('file:memory'):
        return None
    path = Path(db)
    destination = path.parent / 'backups' / datetime.now(timezone.utc).strftime('before-import-%Y%m%d-%H%M%S-%f.sqlite3')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    destination.chmod(0o600)
    return str(destination)


def apply_reviewed(workbook, source_sha256, review_token, actor='import_excel'):
    if not review_token:
        raise ValueError('É necessário o review_token de uma simulação revista.')
    backup = snapshot_database()
    with transaction.atomic():
        # Reserve SQLite's write lock before checking the reviewed state.
        with connection.cursor() as cursor:
            cursor.execute('UPDATE training_employee SET number = number WHERE 0')
        plan = build_plan(workbook, source_sha256)
        if not plan.report['can_apply']:
            raise ValueError('Importação bloqueada por erros. Nenhum registo gravado.')
        if plan.report['review_token'] != review_token:
            raise ValueError('O Excel ou a base mudou desde a simulação. Faça uma nova revisão.')
        saved = {}
        for obj, kind in plan.employees:
            if kind != 'existing':
                obj.full_clean()
                obj.save()
            saved[obj.number] = obj
        for obj, kind, number in plan.trainings:
            if kind == 'create':
                obj.employee = saved[number]
                obj.full_clean()
                obj.save()
        report = {**plan.report, 'mode': 'apply', 'backup': backup}
        AuditEvent.objects.create(actor=actor, action='IMPORT_INTEGRATED', detail=json.dumps({
            k: report[k] for k in ('source_sha256', 'review_token', 'employees_created',
                                  'employees_updated', 'trainings_created', 'trainings_existing')
        }, ensure_ascii=False))
        return report
