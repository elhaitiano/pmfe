"""Literal certificate facts. Issue dates never substitute course dates."""
import hashlib
import re
import unicodedata
from datetime import date
from decimal import Decimal
from pathlib import Path
import os


def io_path(path):
    text=str(Path(path).absolute())
    return Path('\\\\?\\'+text) if os.name=='nt' and not text.startswith('\\\\?\\') else Path(text)


def normalize(text):
    return re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in unicodedata.normalize('NFKD', text or '').lower() if not unicodedata.combining(c))).strip()


def recipient_names(text):
    """Read the printed name, retaining surnames wrapped onto their own line."""
    lines=[' '.join(line.split()) for line in text.splitlines() if line.strip()]
    names=[]
    for i,line in enumerate(lines):
        if normalize(line).replace(' ','') not in ('certificado','certificadodeparticipacao','certificadodeaproveitamento'):continue
        parts=[]
        for following in lines[i+1:i+9]:
            value=normalize(following)
            if re.search(r'\d',value) or value.startswith(('luanda','certificamos','concluiu','realizada','director','formador','drh','dhr','angola')):break
            if not value or value.startswith(('de participacao','de aproveitamento')):continue
            if not parts and len(value.split())<2:continue
            parts.append(following)
        if parts:names.append(' '.join(parts))
    return list(dict.fromkeys(names))


MONTHS = {normalize(v): k for k, v in enumerate(['janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro'], 1)}


def course_period(text):
    text = normalize(text)
    # Only the clause describing the event, never the standalone 'Luanda, ...' issue line.
    match = re.search(r'\brealizad[ao]s?\b(.{0,260})', text)
    if not match:
        return None
    clause = match.group(1)
    numeric = re.search(r'\b(\d{1,2}) (\d{1,2}) (20\d{2}) (?:a|ate) (\d{1,2}) (\d{1,2}) (20\d{2})\b', clause)
    try:
        if numeric:
            a,b,c,d,e,f = map(int, numeric.groups())
            return [date(c,b,a).isoformat(), date(f,e,d).isoformat()]
        months = '|'.join(MONTHS)
        cross = re.search(r'\b(\d{1,2}) (?:de )?(' + months + r') (?:de )?(?:(20\d{2}) )?(?:a|ate) (\d{1,2}) (?:de )?(' + months + r') (?:de )?(20\d{2})', clause)
        if cross:
            a,m,y,b,n,z = cross.groups()
            return [date(int(y or z),MONTHS[m],int(a)).isoformat(), date(int(z),MONTHS[n],int(b)).isoformat()]
        same = re.search(r'\b(\d{1,2}) (?:a|ate|e) (\d{1,2}) (?:de )?(' + months + r') (?:de )?(20\d{2})', clause)
        if same:
            a,b,m,y = same.groups()
            return [date(int(y),MONTHS[m],int(a)).isoformat(), date(int(y),MONTHS[m],int(b)).isoformat()]
        one = re.search(r'\b(\d{1,2}) (?:de )?(' + months + r') (?:de )?(20\d{2})', clause)
        if one:
            d,m,y = one.groups()
            result = date(int(y),MONTHS[m],int(d)).isoformat()
            return [result,result]
    except ValueError:
        return None
    return None


def certificate_facts(pages):
    first = pages[0] if pages else ''
    text = '\n'.join(pages)
    compact = re.sub(r'\s+', '', first).lower()
    front = 'certificado' in compact and ('certificamos' in normalize(first) or 'concluiu' in normalize(first))
    back = len(pages) > 1 and all(('carga horaria' in normalize(p) or 'modulos' in normalize(p)) for p in pages[1:])
    total = re.search(r'duracao\s+(?:total\s+)?(?:de\s+)?(\d+(?:[.,]\d+)?)\s*horas?', unicodedata.normalize('NFKD', first).encode('ascii','ignore').decode().lower())
    total_hours = Decimal(total.group(1).replace(',','.')) if total else None
    back_hours = [Decimal(v.replace(',','.')) for p in pages[1:] for line in p.splitlines()
                  if 'total' not in normalize(line).split()
                  for v in re.findall(r'\b(\d+(?:[.,]\d+)?)\s*horas\b', line.lower())]
    day_labels = [int(v) for p in pages[1:] for v in re.findall(r'\b(\d+)\s+(?:o\s+)?dia\b', normalize(p))]
    uniform = None
    if back_hours and len(set(back_hours)) == 1 and sum(back_hours) == total_hours and len(day_labels) == len(back_hours) and sorted(day_labels) == list(range(1,len(day_labels)+1)):
        uniform = str(back_hours[0])
    number = re.search(r'certificado\s*(?:n[.]?[º°]|n[.]o[.]?|número|numero)\s*[:.\-]?\s*([A-Za-z0-9][A-Za-z0-9/\-]{1,45})', text, re.I)
    issue=re.search(r'\bluanda\s+(\d{1,2})\s+(?:de\s+)?('+'|'.join(MONTHS)+r')\s+(?:de\s+)?(20\d{2})',normalize(first))
    issue_date=None
    if issue:
        try:issue_date=date(int(issue.group(3)),MONTHS[issue.group(2)],int(issue.group(1))).isoformat()
        except ValueError:pass
    title=re.search(r'a(?:c|ç)?[cç][aã]o\s+de\s+forma[cç][aã]o\s*:\s*(.+?)\s*,?\s*realizad[ao]s?', ' '.join(first.split()), re.I)
    return {'course_period': course_period(first), 'pages': len(pages), 'certificate_front': front,
        'layout': 'Frente e verso no mesmo PDF' if front and back else 'Certificado de uma página' if front and len(pages)==1 else 'Formato por verificar',
        'total_hours': str(total_hours) if total_hours is not None else None,
        'uniform_hours_per_day': uniform, 'explicit_session_count': len(day_labels) if uniform else None,
        'certificate_number': number.group(1) if number else None, 'issue_date':issue_date,
        'course_title_literal':title.group(1).strip(' ,') if title else None,
        'completion_literal': bool(re.search(r'\bconcluiu\b',normalize(first))),
        'text_sha256': hashlib.sha256(normalize(text).encode()).hexdigest(),
        'front_text_sha256': hashlib.sha256(normalize(first).encode()).hexdigest()}
