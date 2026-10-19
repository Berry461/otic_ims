import csv
import io
import re
import time
from datetime import timedelta

from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import gig
from .models import Sale, Shipment
from .permissions import IsAdminOrStaff
from .shipping import sync_shipment

PERMS = [permissions.IsAuthenticated, IsAdminOrStaff]
STALE_AFTER = timedelta(minutes=10)
STALLED_AFTER = timedelta(days=3)
REFRESH_BUDGET_SECONDS = 20          # stay under gunicorn's request timeout
MAX_TRACK = 200
MAX_IMPORT_ROWS = 2000
WAYBILL_RE = re.compile(r"^[A-Za-z0-9\-]{5,40}$")

BOOKED = ("not_scanned", "created", "pickup_scheduled")
MOVING = ("picked_up", "in_transit", "arrived_destination", "out_for_delivery")
ATTENTION = ("delivery_failed", "exception", "returned")


# ---------- serialising ----------

def _last_event(s):
    events = list(s.events.all())          # ordered newest first
    return events[0] if events else None


def _is_stalled(s, last):
    if s.status in Shipment.FINAL_STATUSES:
        return False
    ref = last.occurred_at if last else s.created_at
    return timezone.now() - ref > STALLED_AFTER


def _row(s):
    last = _last_event(s)
    return {
        "id": s.id,
        "sale": s.sale_id,
        "source": s.source,
        "waybill": s.waybill,
        "reference": s.reference,
        "receiver_name": s.receiver_name,
        "receiver_phone": s.receiver_phone,
        "destination": s.destination or s.receiver_station_name or s.receiver_state,
        "status": s.status,
        "status_label": s.get_status_display(),
        "is_final": s.status in Shipment.FINAL_STATUSES,
        "is_stalled": _is_stalled(s, last),
        "last_event": last.description if last else "",
        "last_event_at": last.occurred_at if last else None,
        "last_sync_error": s.last_sync_error,
        "last_synced_at": s.last_synced_at,
        "amount": s.amount,
        "created_at": s.created_at,
    }

def _point(loc):
    try:
        return {"lat": float(loc.get("Latitude")), "lng": float(loc.get("Longitude"))}
    except (TypeError, ValueError, AttributeError):
        return None


def _detail(s):
    d = _row(s)
    d.update({
        "receiver_address": s.receiver_address,
        "receiver_state": s.receiver_state,
        "station_name": s.receiver_station_name,
        "pickup_option": s.pickup_option,
        "vehicle_type": s.vehicle_type,
        "is_priority": s.is_priority,
        "is_cod": s.is_cod,
        "cod_amount": s.cod_amount,
        "notes": s.notes,
        "items": s.items,
        "delivered_at": s.delivered_at,
        "gig_response": s.response_payload,
        "events": [
            {"code": e.code, "status": e.status, "description": e.description,
             "location": e.location, "occurred_at": e.occurred_at}
            for e in s.events.all()
        ],
        "map": {
            "sender": _point(((s.request_payload or {}).get("SenderDetails") or {}).get("SenderLocation")),
            "receiver": _point(((s.request_payload or {}).get("ReceiverDetails") or {}).get("ReceiverLocation")),
        },
    })
    return d


def _gig_error(exc):
    return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


# ---------- helpers ----------

def _try_sync(s):
    """Refresh one shipment from GIG. Never raises; returns an error string ('' if fine)."""
    try:
        sync_shipment(s)
        err = ""
    except Exception as exc:
        err = (str(exc) or exc.__class__.__name__)[:200]
    s.refresh_from_db()
    if s.source == "tracked" and s.status != "not_scanned" and not s.events.exists():
        s.status = "not_scanned"
    s.last_sync_error = err
    s.save(update_fields=["status", "last_sync_error", "updated_at"])
    return err


def _station_info(station_id):
    try:
        for st in gig.stations():
            if int(st["id"]) == int(station_id):
                return st.get("name", ""), st.get("state", "")
    except gig.GigError:
        pass
    return "", ""


def _resolve_station(r):
    if r.get("station_id") not in (None, ""):
        return int(r["station_id"])
    state = (r.get("state") or "").strip().lower()
    if state:
        matches = [st for st in gig.stations()
                   if (st.get("state") or "").strip().lower() == state]
        if len(matches) == 1:
            return int(matches[0]["id"])
    return None


def _parse(request):
    d = request.data
    r = dict(d.get("receiver") or {})
    for f in ("name", "phone", "address", "lat", "lng"):
        if r.get(f) in (None, ""):
            return None, f"Receiver {f} is required."
    try:
        r["lat"], r["lng"] = float(r["lat"]), float(r["lng"])
    except (TypeError, ValueError):
        return None, "Latitude and longitude must be numbers."
    if not (-90 <= r["lat"] <= 90 and -180 <= r["lng"] <= 180):
        return None, "Latitude or longitude is out of range."

    items = d.get("items") or []
    if not items:
        return None, "At least one item is required."
    for i in items:
        try:
            ok = float(i.get("weight") or 0) > 0
        except (TypeError, ValueError):
            ok = False
        if not ok:
            return None, f"A weight is required for '{i.get('name', 'item')}'."

    try:
        sid = _resolve_station(r)
    except gig.GigError as e:
        return None, str(e)
    if sid is None:
        return None, "Choose the GIG station for this delivery."
    r["station_id"] = sid

    is_cod = bool(d.get("is_cod"))
    cod_amount = d.get("cod_amount")
    if is_cod:
        try:
            if float(cod_amount or 0) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return None, "Enter the amount to collect for cash on delivery."

    try:
        vehicle = int(d.get("vehicle_type", 2))
    except (TypeError, ValueError):
        return None, "Vehicle type is not valid."

    return {
        "receiver": r,
        "items": items,
        "vehicle_type": vehicle,
        "pickup_option": 0,                      # home delivery only for now
        "is_priority": bool(d.get("is_priority")),
        "is_cod": is_cod,
        "cod_amount": cod_amount if is_cod else None,
        "sender": d.get("sender") or {},
        "reference": str(d.get("reference") or "").strip()[:100],
        "notes": str(d.get("notes") or "").strip(),
    }, None


def _split_waybills(raw):
    if isinstance(raw, (list, tuple)):
        raw = " ".join(str(x) for x in raw)
    seen, out = set(), []
    for w in re.split(r"[\s,;]+", str(raw or "")):
        w = w.strip()
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def _add_tracked(waybill, user, reference="", receiver="", destination=""):
    return Shipment.objects.create(
        carrier="gig", source="tracked", waybill=waybill, status="not_scanned",
        reference=reference, receiver_name=receiver, destination=destination,
        receiver_phone="", receiver_address="", items=[], created_by=user,
    )


# ---------- config / stations / quote ----------

class ShippingConfigView(APIView):
    """Sender defaults for the booking form (read from .env; contains no secrets)."""
    permission_classes = PERMS

    def get(self, request):
        import os
        e = os.environ.get
        return Response({"sender": {
            "name": e("GIG_SENDER_NAME", "OTIC Geosystems"),
            "phone": e("GIG_SENDER_PHONE", ""),
            "address": e("GIG_SENDER_ADDRESS", ""),
            "locality": e("GIG_SENDER_LOCALITY", ""),
            "lat": e("GIG_SENDER_LAT", ""),
            "lng": e("GIG_SENDER_LNG", ""),
            "station_id": e("GIG_SENDER_STATION_ID", ""),
        }})


class GigStationsView(APIView):
    permission_classes = PERMS

    def get(self, request):
        try:
            return Response(gig.stations())
        except gig.GigError as e:
            return _gig_error(e)


class CheckAddressView(APIView):
    permission_classes = PERMS

    def post(self, request):
        try:
            lat, lng = float(request.data.get("lat")), float(request.data.get("lng"))
        except (TypeError, ValueError):
            return Response({"detail": "Latitude and longitude must be numbers."}, status=400)
        try:
            return Response(gig.validate_address(lat, lng))
        except gig.GigError as e:
            return _gig_error(e)


class ShipmentQuoteView(APIView):
    permission_classes = PERMS

    def post(self, request):
        data, err = _parse(request)
        if err:
            return Response({"detail": err}, status=400)
        try:
            check = gig.validate_address(data["receiver"]["lat"], data["receiver"]["lng"])
            if not check.get("deliverable"):
                return Response({"detail": "GIG does not deliver to this location by home delivery. "
                                           "Check the coordinates or try another address.",
                                 "deliverable": False}, status=400)
            quote = gig.price(gig.build_price_payload(data))
        except gig.GigError as e:
            return _gig_error(e)
        name, state = _station_info(data["receiver"]["station_id"])
        return Response({
            "deliverable": True,
            "grand_total": quote.get("GrandTotal"),
            "delivery_price": quote.get("DeliverPrice"),
            "pickup_charge": quote.get("PickupCharge"),
            "insurance": quote.get("InsuranceValue"),
            "discount": quote.get("Discount"),
            "station_id": data["receiver"]["station_id"],
            "station_name": name,
            "station_state": state,
        })


# ---------- list / create ----------

class ShipmentListCreateView(APIView):
    permission_classes = PERMS

    def get(self, request):
        qs = Shipment.objects.prefetch_related("events")
        p = request.query_params
        if p.get("sale"):
            qs = qs.filter(sale_id=p["sale"])
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        q = (p.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(waybill__icontains=q) | Q(reference__icontains=q) |
                Q(receiver_name__icontains=q) | Q(destination__icontains=q) |
                Q(receiver_address__icontains=q) | Q(receiver_station_name__icontains=q)
            )
        return Response([_row(s) for s in qs[:500]])

    def post(self, request):
        data, err = _parse(request)
        if err:
            return Response({"detail": err}, status=400)

        sale = None
        if request.data.get("sale"):
            sale = get_object_or_404(Sale, pk=request.data["sale"])

        r = data["receiver"]
        try:
            check = gig.validate_address(r["lat"], r["lng"])
            if not check.get("deliverable"):
                return Response({"detail": "GIG does not deliver to this location by home delivery.",
                                 "deliverable": False}, status=400)
            quote = gig.price(gig.build_price_payload(data))
            payload = gig.build_capture_payload(data)
            created = gig.create_shipment(payload)
        except gig.GigError as e:
            return _gig_error(e)

        station_name, station_state = _station_info(r["station_id"])
        state = r.get("state") or station_state

        try:
            shipment = Shipment.objects.create(
                sale=sale, carrier="gig", source="booked",
                waybill=created["waybill"], status="created",
                reference=data["reference"], notes=data["notes"],
                receiver_name=r["name"], receiver_phone=r["phone"],
                receiver_address=r["address"], receiver_state=state,
                receiver_station_name=station_name,
                destination=station_name or state,
                pickup_option=0, vehicle_type=data["vehicle_type"],
                is_priority=data["is_priority"], is_cod=data["is_cod"],
                cod_amount=data["cod_amount"],
                items=data["items"], amount=quote.get("GrandTotal") or 0,
                request_payload=payload, response_payload=created.get("raw") or {},
                created_by=request.user,
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception(
                "GIG shipment %s was booked but could not be saved", created["waybill"])
            return Response({
                "detail": f"The shipment was booked with GIG (waybill {created['waybill']}) "
                          f"but could not be saved here: {exc}. Note the waybill and tell an admin.",
                "waybill": created["waybill"],
            }, status=500)

        _try_sync(shipment)
        shipment.refresh_from_db()
        return Response(_detail(shipment), status=201)


# ---------- detail / refresh ----------

class ShipmentDetailView(APIView):
    permission_classes = PERMS

    def get(self, request, pk):
        s = get_object_or_404(Shipment, pk=pk)
        stale = not s.last_synced_at or timezone.now() - s.last_synced_at > STALE_AFTER
        if stale and s.status not in Shipment.FINAL_STATUSES:
            _try_sync(s)
            s.refresh_from_db()
        return Response(_detail(s))

    def patch(self, request, pk):
        s = get_object_or_404(Shipment, pk=pk)
        limits = {"reference": 100, "receiver_name": 200, "destination": 150, "notes": None}
        for field, limit in limits.items():
            if field in request.data:
                val = str(request.data[field] or "").strip()
                setattr(s, field, val[:limit] if limit else val)
        s.save()
        return Response(_detail(s))

    def delete(self, request, pk):
        s = get_object_or_404(Shipment, pk=pk)
        if s.source != "tracked":
            return Response({"detail": "A booked shipment can't be removed. Cancel it instead."}, status=400)
        s.delete()
        return Response(status=204)


class ShipmentRefreshView(APIView):
    permission_classes = PERMS

    def post(self, request, pk):
        s = get_object_or_404(Shipment, pk=pk)
        _try_sync(s)
        s.refresh_from_db()
        return Response(_detail(s))


class ShipmentRefreshActiveView(APIView):
    """Refresh every unfinished shipment, oldest-synced first, within a time budget."""
    permission_classes = PERMS

    def post(self, request):
        deadline = time.monotonic() + REFRESH_BUDGET_SECONDS
        qs = (Shipment.objects.exclude(status__in=Shipment.FINAL_STATUSES)
              .order_by(F("last_synced_at").asc(nulls_first=True)))
        total = qs.count()
        refreshed = failed = 0
        for s in qs:
            if time.monotonic() > deadline:
                break
            if _try_sync(s):
                failed += 1
            else:
                refreshed += 1
        return Response({"total_active": total, "refreshed": refreshed, "failed": failed,
                         "remaining": max(total - refreshed - failed, 0)})


# ---------- stats / track / import ----------

class ShipmentStatsView(APIView):
    permission_classes = PERMS

    def get(self, request):
        rows = list(Shipment.objects.prefetch_related("events"))
        out = {"all": len(rows), "booked": 0, "in_transit": 0,
               "attention": 0, "delivered": 0, "stalled": 0, "last_checked": None}
        for s in rows:
            if s.status in BOOKED:
                out["booked"] += 1
            elif s.status in MOVING:
                out["in_transit"] += 1
            elif s.status in ATTENTION:
                out["attention"] += 1
            elif s.status == "delivered":
                out["delivered"] += 1
            if _is_stalled(s, _last_event(s)):
                out["stalled"] += 1
            if s.last_synced_at and (not out["last_checked"] or s.last_synced_at > out["last_checked"]):
                out["last_checked"] = s.last_synced_at
        return Response(out)


class ShipmentTrackView(APIView):
    """Add one or many waybills (booked elsewhere) so they can be tracked here."""
    permission_classes = PERMS

    def post(self, request):
        waybills = _split_waybills(request.data.get("waybills"))
        if not waybills:
            return Response({"detail": "Enter at least one waybill number."}, status=400)
        if len(waybills) > MAX_TRACK:
            return Response({"detail": f"Add at most {MAX_TRACK} waybills at a time."}, status=400)

        single = len(waybills) == 1
        ref = str(request.data.get("reference") or "").strip()[:100] if single else ""
        rcv = str(request.data.get("receiver") or "").strip()[:200] if single else ""
        dst = str(request.data.get("destination") or "").strip()[:150] if single else ""

        existing = set(Shipment.objects.filter(waybill__in=waybills).values_list("waybill", flat=True))
        added, skipped, failed = [], [], []
        for w in waybills:
            if w in existing:
                skipped.append(w)
            elif not WAYBILL_RE.match(w):
                failed.append({"waybill": w, "detail": "Not a valid waybill number."})
            else:
                added.append(_add_tracked(w, request.user, ref, rcv, dst))

                checked = 0
        for s in list(added):                # check the first few now; the rest on 'Refresh all'
            if checked >= 10:
                break
            checked += 1
            err = _try_sync(s)
            if err and "not found" in err.lower():
                failed.append({"waybill": s.waybill,
                               "detail": "GIG has no shipment with this waybill number."})
                s.delete()
                added.remove(s)
        return Response({
            "added": [s.waybill for s in added],
            "already_tracked": skipped,
            "failed": failed,
            "checked_now": checked,
        }, status=201 if added else 200)


class ShipmentImportView(APIView):
    """CSV with a 'waybill' column; optional 'reference', 'receiver', 'destination'."""
    permission_classes = PERMS
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "Choose a CSV file first."}, status=400)
        try:
            text = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return Response({"detail": "The file must be a UTF-8 CSV."}, status=400)

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or "waybill" not in [h.strip().lower() for h in reader.fieldnames]:
            return Response({"detail": "The CSV needs a 'waybill' column."}, status=400)

        rows = []
        for raw in reader:
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            if row.get("waybill"):
                rows.append(row)
        if not rows:
            return Response({"detail": "No waybills found in the file."}, status=400)
        if len(rows) > MAX_IMPORT_ROWS:
            return Response({"detail": f"Too many rows (max {MAX_IMPORT_ROWS})."}, status=400)

        existing = set(Shipment.objects.filter(
            waybill__in=[r["waybill"] for r in rows]).values_list("waybill", flat=True))
        added = skipped = invalid = 0
        seen = set()
        for r in rows:
            w = r["waybill"]
            if w in existing or w in seen:
                skipped += 1
            elif not WAYBILL_RE.match(w):
                invalid += 1
            else:
                _add_tracked(w, request.user, r.get("reference", "")[:100],
                             r.get("receiver", "")[:200], r.get("destination", "")[:150])
                added += 1
            seen.add(w)
        return Response({"added": added, "skipped": skipped, "invalid": invalid,
                         "note": "Use 'Refresh all active' to fetch their status from GIG."},
                        status=201 if added else 200)