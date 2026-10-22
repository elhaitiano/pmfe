"""Export candidate groups without inventing schedules or changing the database."""
import argparse
import csv
import hashlib
import os
import unicodedata
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
import openpyxl
from openpyxl.utils.datetime import from_excel


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).split()).casefold()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    book = openpyxl.load_workbook(args.workbook, read_only=True, data_only=True)
    sheet = book['PARTICIPACOES']
    rows = iter(sheet.values)
    headers = next(rows)
    groups = defaultdict(list)
    seen = set()
    dimensions = ['Código Formação', 'Acção de Formação', 'Data de Início', 'Data de Fim',
                  'Formador(a)', 'Local da Formação', 'Entidade Formadora', 'Duração (Dias)', 'Horas/Dia']
    for line, values in enumerate(rows, 2):
        if not any(v is not None for v in values):
            continue
        row = dict(zip(headers, values))
        identity = str(row['ID Participação'] or '').strip()
        if not identity or identity in seen:
            raise ValueError(f'ID de participação vazio ou repetido na linha {line}')
        seen.add(identity)
        for key in ['Data de Início', 'Data de Fim']:
            value = row[key]
            if not isinstance(value, (date, datetime)):
                value = from_excel(float(value), book.epoch)
            row[key] = value.strftime('%Y-%m-%d')
        row['_line'] = line
        groups[tuple(normalized(row.get(k)) for k in dimensions)].append(row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(args.workbook.read_bytes()).hexdigest()
    with args.output.open('w', encoding='utf-8-sig', newline='') as output:
        os.chmod(args.output, 0o600)
        writer = csv.writer(output, delimiter=';')
        writer.writerow(['Referência provisória', *dimensions, 'Áreas na fonte', 'Participações',
                         'Formandos distintos', 'IDs de participação', 'Linhas PARTICIPACOES',
                         'Revisão necessária', 'Ficheiro de origem', 'SHA256'])
        for index, (key, members) in enumerate(sorted(groups.items()), 1):
            row = members[0]
            warnings = ['Agrupamento inferido; confirmar a turma',
                        'Sem horário, capacidade ou calendário diário na fonte']
            if not row.get('Formador(a)'):
                warnings.append('Formador em falta')
            if isinstance(row.get('Entidade Formadora'), (int, float)) or str(row.get('Entidade Formadora') or '').isdigit():
                warnings.append('Entidade formadora numérica: rever')
            areas = sorted({str(r.get('Área/Categoria Formação') or '') for r in members})
            if len(areas) > 1:
                warnings.append('Categorias divergentes')
            people = {str(r['Nº Mecanográfico']) for r in members}
            if len(people) < len(members):
                warnings.append('Formando repetido no mesmo agrupamento')
            writer.writerow([f'PROV-{index:03}', *[row.get(k) for k in dimensions],
                             ' | '.join(areas), len(members), len(people),
                             ' | '.join(str(r['ID Participação']) for r in members),
                             ' | '.join(str(r['_line']) for r in members),
                             '; '.join(warnings), args.workbook.name, digest])
    book.close()
    print(f'{len(seen)} participações; {len(groups)} grupos provisórios; {args.output.name}')


if __name__ == '__main__':
    main()
