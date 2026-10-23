"""Prepare a SQLite trainer registry import; never execute it on the live database."""
import hashlib
import json
import os
import unicodedata
from collections import defaultdict
from pathlib import Path
import openpyxl

root = Path(__file__).resolve().parent.parent
source = root / 'PMF_100_Formandos_Demonstracao_Completa_Avaliacoes_360.xlsx'
book = openpyxl.load_workbook(source, read_only=True, data_only=True)
rows = iter(book['PARTICIPACOES'].values)
headers = next(rows)
trainers = defaultdict(list)
for line, values in enumerate(rows, 2):
    row = dict(zip(headers, values))
    name = ' '.join(unicodedata.normalize('NFKC', str(row.get('Formador(a)') or '')).split())
    if name:
        trainers[name.casefold()].append((line, name, str(row.get('Entidade Formadora') or '').strip()))
book.close()

def quote(value):
    return 'NULL' if value is None else "'" + str(value).replace("'", "''") + "'"

records = []
variants = []
summary = []
digest = hashlib.sha256(source.read_bytes()).hexdigest()
for key, entries in sorted(trainers.items()):
    names = sorted({e[1] for e in entries})
    organizations = sorted({e[2] for e in entries if e[2] and not e[2].isdigit()})
    organization = organizations[0] if len(organizations) == 1 else None
    import_key = 'excel-trainer:' + hashlib.sha256(key.encode()).hexdigest()
    warnings = []
    if len(organizations) > 1:
        warnings.append('Várias entidades na fonte; confirmar a entidade actual.')
    if not organizations:
        warnings.append('Entidade não indicada na fonte.')
    if any(e[2].isdigit() for e in entries):
        warnings.append('Entidade numérica inválida ignorada.')
    if key in ['adelina cacombo', 'adelina cocombo']:
        warnings.append('Grafias Cacombo/Cocombo mantidas separadas; confirmar identidade.')
    provenance = dict(import_key=import_key, workbook=source.name, sha256=digest,
                      sheet='PARTICIPACOES', rows=[e[0] for e in entries], name_variants=names,
                      source_organizations=sorted({e[2] for e in entries}), review=warnings,
                      matching='Nome normalizado (espaços e maiúsculas); sem fusão por semelhança.')
    records.append('(' + ', '.join(quote(v) for v in [import_key, names[0], organization, json.dumps(provenance, ensure_ascii=False)]) + ')')
    variants.extend('(' + ', '.join(quote(v) for v in [import_key, n]) + ')' for n in names)
    summary.append({'name': names[0], 'organization': organization, 'review': warnings})
count = len(records)
sql = f'''-- SQLite / PMFE: {count} cadastros de formadores extraídos de PARTICIPACOES.
-- Sem telefones, e-mails ou especialidades inventados. Activos para selecção nos grupos.
-- Entidade única conhecida: preenchida; várias entidades ou nenhuma: NULL, rever source_data.
-- Não altera formações, grupos, presenças, notas ou certificados.
-- Reexecução não duplica registos desta importação nem sobrescreve edições posteriores.
-- Cadastro manual com o mesmo nome: interrompe a importação para revisão.
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;
CREATE TEMP TABLE pmf_trainer_source (
    import_key TEXT PRIMARY KEY, name TEXT NOT NULL, organization TEXT, provenance TEXT NOT NULL
);
INSERT INTO pmf_trainer_source VALUES
''' + ',\n'.join(records) + ''';
CREATE TEMP TABLE pmf_trainer_names (import_key TEXT NOT NULL, name TEXT NOT NULL);
INSERT INTO pmf_trainer_names VALUES
''' + ',\n'.join(variants) + f''';
CREATE TEMP TABLE pmf_trainer_check (
    valid INTEGER CONSTRAINT cadastro_formador_duplicado_rever_antes_de_importar CHECK(valid=1)
);
INSERT OR ROLLBACK INTO pmf_trainer_check
SELECT CASE WHEN (SELECT COUNT(*) FROM pmf_trainer_source)={count}
AND NOT EXISTS (
    SELECT 1 FROM pmf_trainer_names n JOIN training_trainer t ON TRIM(t.name)=n.name COLLATE NOCASE
    WHERE COALESCE(json_extract(t.source_data,'$.import_key'),'') <> n.import_key
)
AND NOT EXISTS (
    SELECT s.import_key FROM pmf_trainer_source s JOIN training_trainer t
    ON json_extract(t.source_data,'$.import_key')=s.import_key
    GROUP BY s.import_key HAVING COUNT(*)>1
)
THEN 1 ELSE 0 END;
CREATE TEMP TABLE pmf_new_trainers AS
SELECT s.* FROM pmf_trainer_source s WHERE NOT EXISTS (
    SELECT 1 FROM training_trainer t WHERE json_extract(t.source_data,'$.import_key')=s.import_key
);
INSERT OR ROLLBACK INTO training_trainer (
    name,organization,email,phone,specialty,active,source_data,created,updated
)
SELECT name,organization,NULL,NULL,NULL,1,provenance,
    STRFTIME('%Y-%m-%d %H:%M:%f','now'),STRFTIME('%Y-%m-%d %H:%M:%f','now')
FROM pmf_new_trainers;
INSERT INTO training_auditevent (created,actor,action,detail)
SELECT STRFTIME('%Y-%m-%d %H:%M:%f','now'),'SQL import','IMPORT_TRAINERS_EXCEL',
    json_object('inserted',(SELECT COUNT(*) FROM pmf_new_trainers),'source','PARTICIPACOES')
WHERE EXISTS (SELECT 1 FROM pmf_new_trainers);
DROP TABLE pmf_new_trainers;
DROP TABLE pmf_trainer_check;
DROP TABLE pmf_trainer_names;
DROP TABLE pmf_trainer_source;
COMMIT;
'''
output = root / 'exports/07_insert_trainers.sql'
output.write_text(sql)
os.chmod(output, 0o600)
print(json.dumps({'file':output.name,'trainers':count,'participations':sum(len(x) for x in trainers.values()),'review':[r for r in summary if r['review']]},ensure_ascii=False))
