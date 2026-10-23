from django.utils import timezone
from django.utils.dateparse import parse_datetime
from . import gig
from .models import ShipmentEvent


def sync_shipment(shipment):
    result = gig.track(shipment.waybill)
    delivered_at = None
    for e in result["events"]:
        when = parse_datetime(e["occurred_at"]) if e["occurred_at"] else None
        if not when:
            continue

        # GIG does not give scans a unique id, so build one from the scan itself.
        # The same scan always gives the same key; a new scan gives a new key.
        key = e["key"] or f'{e["code"]}|{e["occurred_at"]}'

        # Rows saved earlier with a blank key: give them the proper key (no duplicate)
        ShipmentEvent.objects.filter(
            shipment=shipment, key="", code=e["code"], occurred_at=when
        ).update(key=key)

        ShipmentEvent.objects.get_or_create(
            shipment=shipment, key=key,
            defaults={"code": e["code"], "status": e["status"] or "", "description": e["description"][:255],
                      "location": e["location"][:255], "occurred_at": when, "raw": e["raw"]},
        )
        if e["status"] == "delivered":
            delivered_at = when
    if result["status"]:
        shipment.status = result["status"]
    if delivered_at and not shipment.delivered_at:
        shipment.delivered_at = delivered_at
    shipment.last_synced_at = timezone.now()
    shipment.save()
    return shipment