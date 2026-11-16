"""The four institutional training domains (Domínios de Competências).

Old free-text areas are mapped to a domain; a value that is neither a domain
nor a known old area is kept as written so no information is lost. An empty
area stays "Área por confirmar" unless the data migration classified it.
"""
import unicodedata

TECNICO = 'Técnico'
GESTAO = 'Gestão e Soft Skills'
SEGURANCA = 'Segurança'
TI = 'Tecnologia de Informação'
DOMAINS = (TECNICO, GESTAO, SEGURANCA, TI)
UNCONFIRMED_LABEL = 'Área por confirmar'

DESCRIPTIONS = {
    TECNICO: 'Actividades técnicas e operacionais: tratamento de água, redes, manutenção, contagem, laboratório e produção.',
    GESTAO: 'Gestão administrativa, organização do trabalho, comunicação, liderança, atendimento e competências comportamentais.',
    SEGURANCA: 'Prevenção de riscos, segurança e saúde no trabalho, incêndios, espaços confinados, primeiros socorros e emergências.',
    TI: 'Ferramentas informáticas, aplicações digitais e sistemas de informação (Excel, Word, PowerPoint, GMAO/CMMS).',
}


def key(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


LEGACY = {key(old): domain for old, domain in {
    'Técnica': TECNICO, 'Técnico': TECNICO,
    'Segurança': SEGURANCA,
    'TI / Sistemas': TI, 'Tecnologia de Informação': TI,
    'Soft skills': GESTAO, 'Comunicação': GESTAO, 'Liderança': GESTAO,
    'Emocional': GESTAO, 'Cliente': GESTAO, 'Social': GESTAO,
    'Gestão e Soft Skills': GESTAO,
}.items()}


def normalize_area(value):
    """Return the domain for a known value; keep unknown text unchanged."""
    text = (value or '').strip()
    return LEGACY.get(key(text), text)


def domain_choices(current=''):
    choices = [('', UNCONFIRMED_LABEL)] + [(domain, domain) for domain in DOMAINS]
    current = (current or '').strip()
    if current and current not in DOMAINS:
        # Preserve an unclassified value instead of erasing it on edit.
        choices.append((current, f'{current} (por classificar)'))
    return choices
