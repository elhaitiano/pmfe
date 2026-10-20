"""Read-only source achievement; intentionally separate from Training.grade /20."""
import hashlib,json
from pathlib import Path
from functools import lru_cache
from decimal import Decimal
def index():
    from .private_data import load
    return load('achievement')['items']
def achievement(training,approved=None,original_index=None):
    originals=index() if original_index is None else original_index
    item=(approved or {}).get(training.source_key or f'manual:{training.pk}') or originals.get(training.source_key)
    if not item:return None
    # JSONField is deserialised; use the preserved SQL JSON text if annotated.
    from django.db.models import TextField
    from django.db.models.functions import Cast
    source=getattr(training,'source_json_text',None)
    if source is None:
        source=training.__class__.objects.filter(pk=training.pk).annotate(raw_source=Cast('source_data',TextField())).values_list('raw_source',flat=True).first()
    protected=[training.source_key,training.employee_id,training.title,training.start.isoformat(),training.end.isoformat(),source]
    if hashlib.sha256(json.dumps(protected,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()!=item['signature']:return None
    return {**item,'percentage':Decimal(item['percentage']) if item['percentage'] is not None else None,'qualitative':item.get('unit')=='qualitative','approved':bool(item.get('approved'))}

def attach(rows):
    if not rows:return
    if all(getattr(r,'_achievement_attached',False) for r in rows):
        from .evaluations import result
        for row in rows:row.evaluation=result(row)
        return
    from .models import Training
    from django.db.models import TextField
    from django.db.models.functions import Cast
    from .approved_evaluations import supplement_index
    approved=supplement_index()
    originals=index()
    needed=[r.pk for r in rows if (r.source_key in originals or (r.source_key or f'manual:{r.pk}') in approved) and not hasattr(r,'source_json_text')]
    raw=dict(Training.objects.filter(pk__in=needed).annotate(raw=Cast('source_data',TextField())).values_list('pk','raw')) if needed else {}
    for row in rows:
        if row.pk in raw:row.source_json_text=raw[row.pk]
        row.source_achievement=achievement(row,approved,originals)
        row._achievement_attached=True
        from .evaluations import result
        row.evaluation=result(row)
