"""Atomic, idempotent ingestion of documentary assertions, never inferred results."""
import hashlib
import json
from pathlib import Path

from django.db import transaction
from .models import DocumentaryRecord, Employee
from .management.commands.import_excel import parse_date


TABLES = {
    'CONTEXTOS_DE_ORIGEM': 'context',
    'REGISTOS_NOMINAIS': 'nominal',
    'PRESENCAS_DECLARADAS': 'attendance_assertion',
    'TESTES_ESCALA_ORIGINAL': 'original_test',
    'CERTIFICADOS_REFERENCIAS': 'certificate_reference',
    'PENDENCIAS_SEPARADAS': 'held_assertion',
    'TESTES_SEM_VINCULO': 'unlinked_test',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load_package(folder):
    folder = Path(folder)
    raw = (folder / 'MANIFESTO.json').read_bytes()
    manifest = json.loads(raw)
    entries = {e['file']: e for e in manifest}
    if len(entries) != len(manifest):
        raise ValueError('Manifesto contém nomes duplicados.')
    tables = {}
    for name in ['CADASTRO_EPAL', *TABLES]:
        filename = name + '.json'
        entry = entries.get(filename)
        if not entry:
            raise ValueError('Tabela ausente do manifesto: ' + filename)
        data = (folder / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('Integridade inválida: ' + filename)
        rows = json.loads(data)
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError('Tabela inválida: ' + filename)
        tables[name] = rows
    return tables, hashlib.sha256(raw).hexdigest()


def prepare_package(folder):
    tables, package_hash = load_package(folder)
    employees = {}
    for r in tables['CADASTRO_EPAL']:
        number, name = str(r['number']), r['Nome'].strip()
        if number in employees or not number or not name or str(r['Nº Mecanográfico']) != number:
            raise ValueError('Identidade cadastral inválida ou duplicada.')
        employees[number] = dict(
            name=name, direction=r.get('Direcção e Gabinete') or '',
            category=r.get('Categoria') or '', function=r.get('Função') or '',
            area=r.get('Área alocada') or '', phone=r.get('Telefone') or '',
            birth_date=parse_date(r.get('Data de nascimento')),
            admission_date=parse_date(r.get('Data de admissão')), source_data=r,
        )
    if not employees:
        raise ValueError('O cadastro oficial não pode estar vazio.')
    # The verified source defines the roster size; an old total is not a rule.
    manifest=json.loads((Path(folder)/'MANIFESTO.json').read_text(encoding='utf-8'))
    entry=next(e for e in manifest if e['file']=='CADASTRO_EPAL.json')
    declared=entry.get('rows',entry.get('row_count'))
    if declared is not None and len(employees)!=declared:
        raise ValueError('O total do cadastro não corresponde ao manifesto verificado da fonte.')
    records, seen = [], set()
    for table, kind in TABLES.items():
        for r in tables[table]:
            number = r.get('number')
            if number is not None and number not in employees:
                raise ValueError('Registo fora do cadastro oficial: ' + str(number))
            if kind != 'context' and number is None:
                raise ValueError('Registo individual sem número mecanográfico.')
            if kind == 'attendance_assertion' and r.get('state') not in ('PRESENTE_DECLARADO', 'AUSENTE_DECLARADO'):
                raise ValueError('Presença incerta fora da tabela de pendências.')
            if kind == 'original_test' and r.get('state') != 'VALOR_LITERAL_CONCORDANTE':
                raise ValueError('Teste discordante fora da tabela de pendências.')
            for claim in r.get('claims', []):
                source = claim.get('source', {})
                if kind in ('attendance_assertion', 'original_test') and (source.get('financial_register') or source.get('preparatory_or_derived_or_synthetic_source')):
                    raise ValueError('Fonte administrativa ou derivada não permitida como resultado.')
            identity = r.get('id')
            if kind == 'context':
                identity = r['Contexto documental']
            elif kind == 'unlinked_test':
                identity = digest([r['source_record_id'], r['stage'], r['literal_result'].get('cell')])
            if not identity:
                raise ValueError('Registo sem identificador estável.')
            key = 'epal-documentary:' + kind + ':' + identity
            payload_hash = digest(r)
            if key in seen:
                raise ValueError('Identificador documental duplicado: ' + key)
            seen.add(key)
            state = r.get('state') or ('PENDENTE_VALIDACAO' if kind in ('context', 'nominal') else 'POR_CONFIRMAR')
            records.append(dict(source_key=key, kind=kind, number=number, state=state, payload=r, payload_sha256=payload_hash, package_sha256=package_hash))
    return employees, records


def import_package(folder, apply=False, preserve_legacy=False):
    employees, records = prepare_package(folder)
    report = dict(mode='apply' if apply else 'dry-run', employees_created=0, employees_existing=0,
                  records_created=0, records_existing=0, academic_results_created=0, calendar_groups_created=0)
    with transaction.atomic():
        existing = {e.number: e for e in Employee.objects.all()}
        if set(existing) - set(employees) and not preserve_legacy:
            raise ValueError('A base contém trabalhadores fora do cadastro; reconciliação necessária antes da carga.')
        report['legacy_employees_preserved_outside_package'] = len(set(existing) - set(employees))
        for number, fields in employees.items():
            employee = existing.get(number)
            if employee:
                if employee.name.strip().casefold() != fields['name'].casefold():
                    raise ValueError('Nome divergente para o número ' + number)
                report['employees_existing'] += 1
            else:
                employee = Employee(number=number, **fields)
                employee.full_clean()
                employee.save()
                existing[number] = employee
                report['employees_created'] += 1
        known = {r.source_key: r for r in DocumentaryRecord.objects.all()}
        new = []
        for values in records:
            number = values.pop('number')
            prior = known.get(values['source_key'])
            if prior:
                if prior.payload_sha256 != values['payload_sha256'] or prior.employee_id != (existing[number].pk if number else None):
                    raise ValueError('Versão documental alterada; reconciliação necessária: ' + values['source_key'])
                report['records_existing'] += 1
            else:
                new.append(DocumentaryRecord(employee=existing[number] if number else None, **values))
        DocumentaryRecord.objects.bulk_create(new, batch_size=200)
        report['records_created'] = len(new)
        if not apply:
            transaction.set_rollback(True)
    return report
