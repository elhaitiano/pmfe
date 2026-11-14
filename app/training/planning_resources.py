from django import forms
from django.db import OperationalError
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from .models import TrainingCentre, TrainingRoom
from .views import staff_required
from .scheduling import save_resource

class CentreForm(forms.ModelForm):
    class Meta:
        model = TrainingCentre
        fields = ["code", "name", "active"]

class RoomForm(forms.ModelForm):
    class Meta:
        model = TrainingRoom
        fields = ["centre", "code", "name", "capacity", "active"]

@login_required
@staff_required
def resources(request):
    return render(request, "planning/resources.html", {"centres": TrainingCentre.objects.all(), "rooms": TrainingRoom.objects.select_related("centre").all()})

@login_required
@staff_required
def resource_edit(request, kind, pk=None):
    model, formclass = (TrainingCentre, CentreForm) if kind == "centre" else (TrainingRoom, RoomForm)
    instance = get_object_or_404(model, pk=pk) if pk else model()
    form = formclass(request.POST if request.method == "POST" else None, instance=instance)
    if request.method == "POST" and form.is_valid():
        try:
            save_resource(form.save(commit=False), request.user.username)
            messages.success(request, "Recurso guardado.")
            return redirect("planning_resources")
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        except OperationalError as exc:
            if "locked" not in str(exc).lower(): raise
            return HttpResponse("Planeamento ocupado. Reabra a pagina e tente novamente.", status=409)
    return render(request, "planning/resource_form.html", {"form": form, "kind": kind})
