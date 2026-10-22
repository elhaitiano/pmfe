"""The learner's evaluation centre: real competency radar and longitudinal context."""
import math
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from .models import Employee
from .competencies import managed_cycles
from .competency_services import cycle_results
from .indicators import analyse_indicators
from .views import staff_required


def signature(cycle):
    return (cycle.role_id, cycle.config.get('weights'),
        list(cycle.items.order_by('competency_id').values_list(
            'competency_id','name','description','expected','weight')))


def radar_geometry(items, previous=None):
    """Stable competency axes; unknown values never become zero or synthetic axes."""
    if len(items) < 3:
        return None
    def point(index, value):
        angle = -math.pi / 2 + 2 * math.pi * index / len(items)
        return (220 + math.cos(angle) * 155 * float(value) / 5,
                220 + math.sin(angle) * 155 * float(value) / 5)
    def coords(p):
        return f'{p[0]:.2f},{p[1]:.2f}'
    def polygon(values):
        return ' '.join(coords(point(i, value)) for i,value in enumerate(values))
    axes = []
    for i,item in enumerate(items):
        x,y = point(i,5)
        lx,ly = point(i,5.5)
        dot = point(i,item['score']) if item['score'] is not None else None
        axes.append({'number':i+1,'name':item['name'],'x':f'{x:.2f}','y':f'{y:.2f}',
            'lx':f'{lx:.2f}','ly':f'{ly:.2f}',
            'dx':f'{dot[0]:.2f}' if dot else None,'dy':f'{dot[1]:.2f}' if dot else None})
    return {'axes':axes,'rings':[polygon([level]*len(items)) for level in range(1,6)],
        'target':polygon([item['expected'] for item in items]),
        'current':polygon([item['score'] for item in items]) if all(i['score'] is not None for i in items) else None,
        'previous':polygon(previous) if previous is not None and all(v is not None for v in previous) else None}


@login_required
@staff_required
def profile(request, pk):
    employee = get_object_or_404(Employee,pk=pk)
    cycles = list(managed_cycles(request.user).filter(employee=employee).prefetch_related('items').order_by('start','pk'))
    history = [{'cycle':c,'result':cycle_results(c)} for c in cycles]
    chosen = request.GET.get('cycle')
    if chosen:
        selected = next((r for r in history if str(r['cycle'].pk)==chosen),None)
        if selected is None:
            raise Http404
    else:
        selected = next((r for r in reversed(history) if r['result']['available']), history[-1] if history else None)
    items, comparison, eligible_previous = [], None, []
    if selected:
        cycle = selected['cycle']
        results = {i['id']:i for i in selected['result']['items']}
        items = [{'id':i.pk,'competency_id':i.competency_id,'name':i.name,'description':i.description,
            'expected':i.expected,'score':results.get(i.pk,{}).get('score'),
            'gap':results.get(i.pk,{}).get('gap')} for i in cycle.items.order_by('competency_id')]
        if selected['result']['available']:
            eligible_previous = [r for r in history if (r['cycle'].start,r['cycle'].pk)<(cycle.start,cycle.pk)
                and r['result']['available'] and signature(r['cycle'])==signature(cycle)]
        previous_id=request.GET.get('previous')
        if previous_id and previous_id!='none':
            comparison=next((r for r in eligible_previous if str(r['cycle'].pk)==previous_id),None)
            if comparison is None:
                raise Http404
        elif previous_id!='none' and eligible_previous:
            comparison=eligible_previous[-1]
    previous_scores=None
    if comparison:
        scores={i['id']:i['score'] for i in comparison['result']['items']}
        previous_scores=[scores[i.pk] for i in comparison['cycle'].items.order_by('competency_id')]
    for index,item in enumerate(items):
        item['previous']=previous_scores[index] if previous_scores is not None else None
        item['delta']=item['score']-item['previous'] if item['score'] is not None and item['previous'] is not None else None
    trainings=list(employee.trainings.select_related('evaluation','reaction').all())
    metrics=analyse_indicators(trainings)
    response=render(request,'evaluation_profile.html',{'employee':employee,'history':history,
        'selected':selected,'comparison':comparison,'previous_options':eligible_previous,
        'items':items,'radar':radar_geometry(items,previous_scores),'trainings':trainings,'metrics':metrics,
        'strengths':[i for i in items if i['score'] is not None and i['score']>=i['expected']],
        'gaps':[i for i in items if i['gap'] is not None and i['gap']>0]})
    response['Cache-Control']='private, no-store'
    return response
