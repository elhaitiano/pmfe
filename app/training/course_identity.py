"""Conservative orthographic identity for course titles, never for people."""
import re
from .certificate_evidence import normalize


def title_signature(title):
    text=normalize(title)
    text=text.replace('auto responsabilidade','autorresponsabilidade').replace('autoresponsabilidade','autorresponsabilidade')
    text=re.sub(r'\b\d+(?:\s*\d+)?\s*(?:h|horas|hora|dias|dia)\b',' ',text)
    text=re.sub(r'\bformador\s+interno[n]?\s+epal\b',' ',text)
    stop={'a','as','o','os','de','do','da','dos','das','e','em','no','na','nos','nas','para','por','com','como','ao','aos','um','uma'}
    words=[]
    for word in text.split():
        if word in stop:continue
        if word.endswith('oes'):word=word[:-3]+'ao'
        elif len(word)>4 and word.endswith('s'):word=word[:-1]
        words.append(word)
    return tuple(sorted(words))
