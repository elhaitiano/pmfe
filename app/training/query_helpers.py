"""Request-scoped SQL annotations for documentary indicators."""
from django.db.models import Exists,OuterRef


def receipt_aware(rows):
    from .models import PrivateDocument
    receipts=PrivateDocument.objects.filter(training_id=OuterRef('pk'),employee_id=OuterRef('employee_id'),active=True,kind='participant_receipt')
    return rows.annotate(_has_participant_receipt=Exists(receipts))


def dashboard_summary(rows):
    """Read indicator metadata without loading the complete imported evidence.

    Keep each JSON object intact (including nulls and original scales). The
    original source_data remains deferred and accessible for reports/documents.
    """
    from django.db.models import F, Value, JSONField
    from django.db.models.functions import JSONObject, NullIf
    keys = ('_EPAL_CONSOLIDATED_FACTS', '_original_assessment',
            '_certificate_delivery_declaration', '_certificate_summary', '_attendance_import_v1')
    source = JSONObject(**{
        key: NullIf(F('source_data__' + key), Value('null'), output_field=JSONField())
        for key in keys
    })
    return rows.annotate(_dashboard_source=source).defer('source_data')
