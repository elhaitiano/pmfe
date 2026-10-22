"""Generate the reviewed SQLite group insert from the provisional Excel list."""
import csv
import json
import os
from pathlib import Path

root = Path(__file__).resolve().parent.parent
with (root / 'exports/05_grupos_formacao_excel.csv').open(encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f, delimiter=';'))
assert len(rows) == 70

def sql(value):
    if value is None:
        return 'NULL'
    return "'" + str(value).replace("'", "''") + "'"

values = []
for r in rows:
    values.append('(' + ', '.join(sql(v) for v in [
        r['Referência provisória'], r['Código Formação'], r['Acção de Formação'],
        r['Formador(a)'], '' if r['Entidade Formadora'].isdigit() else r['Entidade Formadora'],
        r['Local da Formação'], r['Data de Início'], r['Data de Fim'],
        int(r['Duração (Dias)']), r['Horas/Dia'], int(r['Formandos distintos']),
        json.dumps(r, ensure_ascii=False),
    ]) + ')')
script = '''-- SQLite / PMFE. 70 grupos provisórios derivados de PARTICIPACOES.
-- Regras autorizadas: 08:00–16:00, pausa de 1 hora, capacidade 30.
-- Estado Planeada para permitir revisão em Planeamento antes de activar.
-- Não cria inscrições, presenças nem liga participações históricas.
-- Não inventa datas de sessões: confirmar o calendário ao editar cada grupo.
-- Formador conserva o nome da fonte; trainer_record fica NULL para associação posterior.
-- Categoria usa o catálogo. Entidades numéricas ficam vazias; origem completa na auditoria.
-- Reexecução ou possível grupo duplicado: cancela TODA a transacção.
-- Crie uma cópia de segurança antes de executar. Não usar simultaneamente com outra importação.
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
CREATE TEMP TABLE pmf_group_source (
    ref TEXT PRIMARY KEY, code TEXT NOT NULL, title TEXT NOT NULL,
    trainer TEXT NOT NULL, provider TEXT NOT NULL, location TEXT NOT NULL,
    start TEXT NOT NULL, end TEXT NOT NULL, days INTEGER NOT NULL,
    hours NUMERIC NOT NULL, people INTEGER NOT NULL, provenance TEXT NOT NULL
);
INSERT INTO pmf_group_source VALUES
''' + ',\n'.join(values) + ''';
CREATE TEMP TABLE pmf_group_check (
    valid INTEGER CONSTRAINT verificar_catalogo_reexecucao_e_duracao CHECK(valid = 1)
);
INSERT OR ROLLBACK INTO pmf_group_check
SELECT CASE WHEN
    (SELECT COUNT(*) FROM pmf_group_source) = 70
    AND NOT EXISTS (SELECT 1 FROM training_auditevent WHERE action='IMPORT_GROUPS_70_V1')
    AND NOT EXISTS (
        SELECT 1 FROM pmf_group_source s WHERE
        (SELECT COUNT(*) FROM training_course c WHERE UPPER(c.code)=UPPER(s.code)) <> 1
        OR s.days NOT BETWEEN 1 AND 366 OR s.hours NOT BETWEEN 0.5 AND 7
        OR s.people > 30 OR s.start > s.end
        OR s.days > CAST(julianday(s.end)-julianday(s.start)+1 AS INTEGER)
    )
    AND NOT EXISTS (
        SELECT 1 FROM pmf_group_source s
        JOIN training_course c ON UPPER(c.code)=UPPER(s.code)
        JOIN training_traininggroup g ON g.course_id=c.id
            AND g.start=s.start AND g.end=s.end
    )
THEN 1 ELSE 0 END;
CREATE TEMP TABLE pmf_group_plan AS
SELECT s.*, c.id AS course_id, c.area AS area,
    MAX(c.next_group, COALESCE((SELECT MAX(g.number)+1 FROM training_traininggroup g
                              WHERE g.course_id=c.id),1))
    + ROW_NUMBER() OVER (PARTITION BY c.id ORDER BY s.start,s.end,s.ref)-1 AS number
FROM pmf_group_source s JOIN training_course c ON UPPER(c.code)=UPPER(s.code);
INSERT OR ROLLBACK INTO training_traininggroup (
    course_id, number, title, area, trainer, trainer_record_id, provider, location,
    capacity, hours_per_day, break_hours, start_time, end_time,
    start, end, days, state, revision, created
)
SELECT course_id,number,title,area,trainer,NULL,provider,location,
    30,hours,1,'08:00:00','16:00:00',start,end,days,'Planeada',1,STRFTIME('%Y-%m-%d %H:%M:%f','now')
FROM pmf_group_plan ORDER BY course_id,number;
UPDATE training_course
SET next_group=(SELECT MAX(g.number)+1 FROM training_traininggroup g WHERE g.course_id=training_course.id),
    revision=revision+1
WHERE id IN (SELECT course_id FROM pmf_group_plan);
INSERT INTO training_auditevent (created,actor,action,detail)
SELECT STRFTIME('%Y-%m-%d %H:%M:%f','now'),'SQL import','IMPORT_GROUPS_70_V1',
    json_object('reference',p.ref,'group_id',g.id,'source',json(p.provenance),
                'defaults','08:00-16:00; pausa 1h; capacidade 30; autorizados pelo utilizador',
                'review','Grupo inferido; confirmar calendario antes de activar')
FROM pmf_group_plan p JOIN training_traininggroup g ON g.course_id=p.course_id AND g.number=p.number;
DROP TABLE pmf_group_plan;
DROP TABLE pmf_group_check;
DROP TABLE pmf_group_source;
COMMIT;
'''
path = root / 'exports/06_insert_training_groups.sql'
path.write_text(script)
os.chmod(path, 0o600)
print(path.name, len(rows), 'groups')
