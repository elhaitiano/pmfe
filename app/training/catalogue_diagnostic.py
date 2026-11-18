"""Pure catalogue analysis: no model imports, database access or mutations."""
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher


EXCLUDED_SOURCE_KEYS = (
    'pilar3b:PF-00285', 'pilar3b:PF-00434', 'pilar3b:PF-00459',
    'pilar3b:PF-01388', 'pilar3b:PF-01854',
)


def normalise(value):
    # Same identity rule as planning.normalise; no dependency on its views.
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


def alias_signature(value):
    value = unicodedata.normalize('NFKD', normalise(value))
    return ' '.join(re.sub(r'[^\w\s]', ' ', ''.join(
        c for c in value if not unicodedata.combining(c))).split())


def analyse_catalogue(rows, courses=()):
    """Suggest identities, never merge similar titles or infer group membership."""
    owners = defaultdict(set)
    for course in courses:
        for title in [course['title'], *course['aliases']]:
            if normalise(title):
                owners[normalise(title)].add(course['id'])
    grouped = defaultdict(list)
    excluded = empty = 0
    for row in rows:
        if row.get('source_key') in EXCLUDED_SOURCE_KEYS:
            excluded += 1
            continue
        key = normalise(row['title'])
        if not key:
            empty += 1
            continue
        grouped[key].append(row)
    candidates = []
    for key, items in sorted(grouped.items()):
        reasons = []
        matches = sorted(owners[key])
        areas = sorted({r['area'] for r in items})
        durations = sorted({(r['days'], str(r['hours_per_day'])) for r in items})
        if not matches:
            reasons.append('Validar nova acção e respectivos valores por defeito.')
        if len(matches) > 1:
            reasons.append('Título ou alias corresponde a várias acções existentes.')
        if len(areas) > 1 or not all(areas):
            reasons.append('Área ausente ou divergente; validar classificação.')
        if len(durations) > 1:
            reasons.append('Durações diferentes; não inferir valores por defeito.')
        candidates.append({
            'normalised_title': key,
            'title_variants': dict(sorted(Counter(r['title'] for r in items).items())),
            'participations': len(items), 'areas': areas,
            'duration_variants': [{'days': d, 'hours_per_day': h} for d, h in durations],
            'existing_course_ids': matches, 'validation_reasons': reasons,
        })
    # Suggestions only: accent/punctuation changes and near matches need review.
    suggestions = []
    titles = sorted(set(grouped) | set(owners))
    for i, left in enumerate(titles):
        for right in titles[i + 1:]:
            if left not in grouped and right not in grouped:
                continue
            a, b = alias_signature(left), alias_signature(right)
            if a and b and (a == b or SequenceMatcher(None, a, b).ratio() >= .9):
                suggestions.append({'left': left, 'right': right,
                                    'action': 'Validar possível alias; não fundir automaticamente.'})
    suggested_titles = {s[k] for s in suggestions for k in ('left', 'right')}
    for candidate in candidates:
        if candidate['normalised_title'] in suggested_titles:
            candidate['validation_reasons'].append('Possível alias de outro título; rever equivalência.')
        candidate['needs_validation'] = bool(candidate['validation_reasons'])
    return {
        'mode': 'diagnostic-only',
        'summary': {
            'candidate_actions': len(candidates),
            'new_candidates': sum(not c['existing_course_ids'] for c in candidates),
            'ambiguous_existing_matches': sum(len(c['existing_course_ids']) > 1 for c in candidates),
            'needs_validation': sum(c['needs_validation'] for c in candidates),
            'analysed_participations': sum(c['participations'] for c in candidates),
            'excluded_duplicates': excluded, 'empty_title_participations': empty,
            'possible_alias_pairs': len(suggestions),
        },
        'excluded_source_keys': list(EXCLUDED_SOURCE_KEYS),
        'candidates': candidates, 'possible_aliases': suggestions,
        'limitations': [
            'Sem proposta de grupos ou alteração de participações.',
            'Sem equivalência automática por semelhança de títulos.',
            'Leituras separadas do histórico e catálogo; repetir se houver alterações concorrentes.',
        ],
    }
