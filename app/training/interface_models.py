"""Operational views with preserved, separately accessible historical originals."""
from django.db import models


class EmployeeInterfaceManager(models.Manager):
    def get_queryset(self):return super().get_queryset().filter(registry_active=True)


class TrainingInterfaceManager(models.Manager):
    def get_queryset(self):return super().get_queryset().exclude(interface_state='legacy_preserved')
