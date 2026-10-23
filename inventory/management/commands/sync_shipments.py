from django.core.management.base import BaseCommand

from inventory.models import Shipment
from inventory.shipping import sync_shipment


class Command(BaseCommand):
    help = "Ask GIG for new scans on every unfinished shipment and save them."

    def handle(self, *args, **options):
        finished = ["delivered", "cancelled", "returned"]
        ok = failed = 0
        for s in Shipment.objects.exclude(status__in=finished):
            try:
                sync_shipment(s)
                Shipment.objects.filter(pk=s.pk).update(last_sync_error="")
                ok += 1
            except Exception as exc:
                failed += 1
                Shipment.objects.filter(pk=s.pk).update(last_sync_error=str(exc)[:500])
                self.stderr.write(f"{s.waybill}: {exc}")
        self.stdout.write(f"Checked {ok}, failed {failed}")