import json
import os
from django.core.management.base import BaseCommand
from inventory import gig


class Command(BaseCommand):
    help = "Test the GIG Logistics connection."

    def add_arguments(self, parser):
        parser.add_argument("--company", action="store_true")
        parser.add_argument("--stations", action="store_true")
        parser.add_argument("--quote", action="store_true")
        parser.add_argument("--waybill", default="")
        parser.add_argument("--create", action="store_true")
        parser.add_argument("--station", default="3")

    def dump(self, obj):
        self.stdout.write(json.dumps(obj, indent=2, default=str))

    def handle(self, *args, **o):
        gig.get_token(force=True)
        self.stdout.write(self.style.SUCCESS("GIG login OK"))
        email = os.environ.get("GIG_EMAIL", "")

        try:
            if o["company"]:
                c = gig.company_details(email)
                keep = ["Name", "CustomerCode", "CompanyType", "Address", "City", "State",
                        "PhoneNumber", "ReturnServiceCentre", "ReturnAddress"]
                self.dump({k: c.get(k) for k in keep})

            if o["stations"]:
                st = gig.stations()
                self.stdout.write(f"{len(st)} stations")
                lagos = [s for s in st if (s.get("state") or "").upper() == "LAGOS"]
                self.stdout.write(f"{len(lagos)} in Lagos, first 15:")
                self.dump(lagos[:15])

            if o["quote"]:
                code = os.environ.get("GIG_CUSTOMER_CODE") or gig.company_details(email).get("CustomerCode")
                payload = {
                    "SenderStationId": 3, "ReceiverStationId": 3, "VehicleType": 2,
                    "SenderLocation": {"Latitude": 6.451, "Longitude": 3.4844},
                    "ReceiverLocation": {"Latitude": 6.6018, "Longitude": 3.3515},
                    "CustomerCode": code, "CustomerType": 0, "PickUpOptions": 1,
                    "ShipmentItems": [{
                        "ItemName": "GNSS receiver (test)", "Description": "Survey equipment",
                        "Quantity": 1, "Weight": 5, "IsVolumetric": False,
                        "ShipmentType": 1, "Value": 100000,
                    }],
                }
                self.dump(gig.price(payload))

            if o["create"]:
                data = {
                    "receiver": {"name": "Test Receiver", "phone": "+2348012345678",
                                 "address": "12 Admiralty Way, Lekki Phase 1, Lagos",
                                 "lat": 6.4474, "lng": 3.4723, "station_id": o["station"]},
                    "vehicle_type": 2,
                    "items": [{"name": "GNSS receiver (test)", "description": "Survey equipment",
                               "quantity": 1, "weight": 5, "value": 100000}],
                }
                quote = gig.price(gig.build_price_payload(data))
                self.stdout.write(f"Quote grand total: {quote.get('GrandTotal')}")
                created = gig.create_shipment(gig.build_capture_payload(data))
                self.stdout.write(self.style.SUCCESS(f"Created waybill {created['waybill']}"))
                self.dump(created["raw"])

            if o["waybill"]:
                res = gig.track(o["waybill"])
                self.stdout.write(f"Waybill {res['waybill']}: {len(res['events'])} events, "
                                  f"status = {res['status']}, amount = {res['amount']}")
                for e in res["events"]:
                    self.stdout.write(f"  {e['occurred_at']}  {e['code']:8} {e['status'] or '-':20} {e['description']}")
        except gig.GigError as e:
            self.stdout.write(self.style.ERROR(f"GIG error {e.status}: {e}"))
            self.dump(e.payload)