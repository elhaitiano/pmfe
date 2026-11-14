"""Staff-only trainer registry."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction, OperationalError
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from .scheduling import save_trainer
from django.db.models import Q, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from .forms import StyledModelForm
from .models import Trainer
from .views import staff_required, record


class TrainerForm(StyledModelForm):
    active = forms.BooleanField(label="Disponivel para novas reservas", required=False, initial=True)

    class Meta:
        model = Trainer
        fields = ['name', 'organization', 'email', 'phone', 'specialty', 'active']


@login_required
@staff_required
@never_cache
@require_http_methods(['GET'])
def index(request):
    query = request.GET.get('q', '').strip()
    rows = Trainer.objects.annotate(group_count=Count('groups', distinct=True))
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(organization__icontains=query) | Q(specialty__icontains=query))
    return render(request, 'trainers/index.html', {
        'q': query, 'page': Paginator(rows.order_by('name', 'pk'), 25).get_page(request.GET.get('page')),
    })


@login_required
@staff_required
@never_cache
@require_http_methods(['GET', 'POST'])
def edit(request, pk=None):
    trainer = get_object_or_404(Trainer, pk=pk) if pk else Trainer()
    form = TrainerForm(request.POST if request.method == 'POST' else None, instance=trainer)
    if request.method == 'POST' and form.is_valid():
        try:
            save_trainer(form.save(commit=False), request.user.username)
            messages.success(request, 'Formador guardado.')
            return redirect('trainer_index')
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        except OperationalError as exc:
            if 'locked' not in str(exc).lower():
                raise
            return HttpResponse('Planeamento ocupado. Reabra a pagina e tente novamente.', status=409)
    return render(request, 'trainers/form.html', {'form': form, 'title': 'Editar formador' if pk else 'Novo formador'})
