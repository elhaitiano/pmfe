from .models import Trainer, TrainingCentre, TrainingRoom
from .scheduling import save_resource
from .scheduling_guard import reservation_transaction

def confirm_fixture(group):
    group.refresh_from_db()
    if group.planning_confirmation == "Confirmado": return
    trainer=Trainer.objects.create(name=f"TESTE_F6_FIX_{group.pk}")
    centre=save_resource(TrainingCentre(code=f"TESTE_F6_FIX_{group.pk}",name="TESTE_F6_FIX"))
    room=save_resource(TrainingRoom(centre=centre,code="TESTE_F6_FIX",name="TESTE_F6_FIX",capacity=1000))
    with reservation_transaction():
        group.trainer_record=trainer;group.trainer=trainer.name
        group.centre=centre;group.room=room;group.planning_confirmation="Confirmado"
        group.save()
