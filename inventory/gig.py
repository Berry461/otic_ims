import os
import requests
from django.core.cache import cache
import ssl
from requests.adapters import HTTPAdapter

TOKEN_KEY = "gig_access_token"
TOKEN_TTL = 60 * 60 * 24 * 19  # GIG tokens last about 20 days


class GigError(Exception):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def _cfg():
    return {
        "base": os.environ.get("GIG_BASE_URL", "https://dev-thirdpartynode.theagilitysystems.com").rstrip("/"),
        "email": os.environ.get("GIG_EMAIL", ""),
        "password": os.environ.get("GIG_PASSWORD", ""),
    }


def _json(r):
    try:
        return r.json()
    except ValueError:
        return {}


def _find(obj, key):
    """Find a key anywhere in nested dicts (GIG nests responses inconsistently)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find(v, key)
            if found is not None:
                return found
    return None


def _payload(body):
    """Unwrap nested {'data': {'data': ...}} envelopes."""
    d = body
    for _ in range(3):
        if isinstance(d, dict) and "data" in d:
            d = d["data"]
        else:
            break
    return d


def _status(r, body):
    s = body.get("status") if isinstance(body, dict) else None
    return s if isinstance(s, int) and s >= 400 else r.status_code


def get_token(force=False):
    if not force:
        token = cache.get(TOKEN_KEY)
        if token:
            return token
    c = _cfg()
    if not c["email"] or not c["password"]:
        raise GigError("GIG_EMAIL and GIG_PASSWORD are not set.")
    r = _send("post", f"{c['base']}/login", retries=4,
                      json={"email": c["email"], "password": c["password"]}, timeout=20)
    body = _json(r)
    token = _find(body, "access-token")
    if _status(r, body) >= 400 or not token:
        raise GigError(body.get("message") or "GIG login failed", _status(r, body), body)
    cache.set(TOKEN_KEY, token, TOKEN_TTL)
    return token


class _Tls12Adapter(HTTPAdapter):
    """Limits the connection to TLS 1.2. Needed on networks that corrupt the newer handshake."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def _session():
    s = requests.Session()
    if os.environ.get("GIG_FORCE_TLS12", "").lower() in ("1", "true", "yes"):
        s.mount("https://", _Tls12Adapter())
    return s


def _send(method, url, retries=None, **kwargs):
    """HTTP call. Lookups (GET) and login retry up to 4 times; anything that
    books or changes something (create shipment) is never retried, so it can't be duplicated."""
    kwargs.setdefault("timeout", 30)
    attempts = retries or (4 if method.lower() == "get" else 1)
    last = None
    for n in range(attempts):
        try:
            with _session() as s:
                return s.request(method, url, **kwargs)
        except requests.RequestException as exc:
            last = exc
            if n < attempts - 1:
                time.sleep(0.6 * (n + 1))
    raise GigError(f"Could not reach GIG ({last.__class__.__name__}): {str(last)[:400]}")


def _request(method, path, params=None, json=None):
    c = _cfg()
    for attempt in (1, 2):
        token = get_token(force=(attempt == 2))
        r = _send(method, f"{c['base']}{path}", params=params, json=json,
                  headers={"access-token": token})
        body = _json(r)
        code = _status(r, body)
        if code == 401 and attempt == 1:
            cache.delete(TOKEN_KEY)
            continue
        if code >= 400:
            raise GigError(body.get("message") or f"GIG error {code}", code, body)
        return body


# GIG scan code -> our canonical status. Codes not listed are stored but
# never change a shipment's status (many are email/notification codes).
STATUS_MAP = {
    "CRT": "created", "CRH": "created", "MCRT": "created", "MCS": "created",
    "MAPT": "pickup_scheduled", "MENP": "pickup_scheduled",
    "MPIK": "picked_up", "PICKED": "picked_up",
    "MSVC": "in_transit", "MDSE": "in_transit", "ARP": "in_transit", "APT": "in_transit",
    "DPC": "in_transit", "DTR": "in_transit", "DSC": "in_transit", "AST": "in_transit",
    "DST": "in_transit", "ARO": "in_transit", "TRO": "in_transit", "GOP": "in_transit",
    "ACC": "in_transit", "DCC": "in_transit", "SRFS": "in_transit",
    "MAFD": "arrived_destination", "ARF": "arrived_destination", "AHD": "arrived_destination",
    "ADF": "arrived_destination", "ARFTPKU": "arrived_destination", "AD": "arrived_destination",
    "MSHC": "out_for_delivery", "WC": "out_for_delivery", "OFDU": "out_for_delivery",
    "MAHD": "delivered", "SHD": "delivered", "OKC": "delivered", "OKT": "delivered",
    "DFA": "delivery_failed", "FID": "delivery_failed", "ATD": "delivery_failed",
    "MRTE": "returned", "SRHUB": "returned", "SSR": "returned", "SRC": "returned", "RSR": "returned",
    "MSCC": "cancelled", "MSCP": "cancelled", "SSC": "cancelled",
    "DLD": "exception", "DLP": "exception", "DUBC": "exception", "DASH": "exception",
    "SDR": "exception", "SMIM": "exception", "CHR": "exception", "UCC": "exception",
    "MRR": "exception", "MRTD": "exception", "SRR": "exception", "CLS": "exception",
    "OVDSHP": "exception",
}


def normalise_events(shipment):
    events = []
    for e in (shipment or {}).get("MobileShipmentTrackings", []) or []:
        code = e.get("Status") or ""
        when = e.get("DateTimeUtc") or e.get("DateTime") or ""
        events.append({
            "key": f"{code}|{when}",  # dedupe key (GIG reuses tracking IDs)
            "code": code,
            "status": STATUS_MAP.get(code),
            "occurred_at": when,
            "description": e.get("ScanStatusReason") or e.get("ScanStatusIncident") or "",
            "location": e.get("Location") or "",
            "raw": e,
        })
    events.sort(key=lambda x: x["occurred_at"])
    return events


def current_status(events):
    for e in reversed(events):
        if e["status"]:
            return e["status"]
    return None


def track(waybill):
    body = _request("GET", "/track/mobileShipment", params={"Waybill": waybill})
    data = _payload(body)
    shipment = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    events = normalise_events(shipment)
    return {"waybill": waybill, "amount": shipment.get("Amount"),
            "status": current_status(events), "events": events, "raw": body}


def create_shipment(payload):
    body = _request("POST", "/capture/preshipment", json=payload)
    data = _payload(body)
    waybill = data.get("Waybill") if isinstance(data, dict) else None
    if not waybill:
        raise GigError("GIG did not return a waybill.", payload=body)
    return {"waybill": str(waybill), "raw": body}


def price(payload):
    return _payload(_request("POST", "/price", json=payload))


def stations():
    cached = cache.get("gig_stations")
    if cached is not None:
        return cached
    data = _payload(_request("GET", "/localstations/get")) or []
    result = [
        {"id": s["StationId"], "name": s["StationName"], "code": s.get("StationCode"), "state": s.get("StateName")}
        for s in data if not s.get("IsDeleted")
    ]
    cache.set("gig_stations", result, 60 * 60 * 24)
    return result


def validate_address(lat, lng):
    try:
        data = _payload(_request("POST", "/lga/validateAddress", json={"Latitude": lat, "Longitude": lng}))
        return {"deliverable": bool(data.get("HomeDeliveryStatus")), "lga": data.get("LGAName"), "state": data.get("LGAState")}
    except GigError as e:
        if e.status == 401:  # GIG answers 401 when it can't match a supported LGA
            return {"deliverable": False, "message": str(e)}
        raise

def company_details(email):
    data = _payload(_request("GET", "/companyDetails/get", params={"Email": email}))
    return data[0] if isinstance(data, list) and data else (data or {})


def sender_config(override=None):
    e = os.environ.get
    o = override or {}

    def pick(key, env, default=""):
        v = o.get(key)
        return v if v not in (None, "") else e(env, default)

    try:
        cfg = {
            "name": pick("name", "GIG_SENDER_NAME", "OTIC Geosystems"),
            "phone": pick("phone", "GIG_SENDER_PHONE"),
            "address": pick("address", "GIG_SENDER_ADDRESS"),
            "locality": pick("locality", "GIG_SENDER_LOCALITY"),
            "station_id": int(pick("station_id", "GIG_SENDER_STATION_ID", "0") or 0),
            "lat": float(pick("lat", "GIG_SENDER_LAT", "0") or 0),
            "lng": float(pick("lng", "GIG_SENDER_LNG", "0") or 0),
        }
    except (TypeError, ValueError):
        raise GigError("Sender details are not valid.")
    if not (cfg["phone"] and cfg["address"] and cfg["station_id"] and cfg["lat"] and cfg["lng"]):
        raise GigError("Sender details are incomplete. Fill in the sender section or set GIG_SENDER_* in .env.")
    return cfg


def customer_code():
    code = os.environ.get("GIG_CUSTOMER_CODE") or cache.get("gig_customer_code")
    if not code:
        code = company_details(os.environ.get("GIG_EMAIL", "")).get("CustomerCode")
        if code:
            cache.set("gig_customer_code", code, 60 * 60 * 24)
    if not code:
        raise GigError("Could not determine the GIG customer code.")
    return code


def _item(i):
    item = {
        "ItemName": str(i.get("name") or "Equipment")[:100],
        "Description": i.get("description") or i.get("name") or "Equipment",
        "Quantity": max(int(i.get("quantity") or 1), 1),
        "Weight": float(i.get("weight") or 0),
        "IsVolumetric": False,
        "ShipmentType": 1,
        "Value": float(i.get("value") or 0),
    }
    dims = [i.get("length"), i.get("width"), i.get("height")]
    if all(dims):
        item.update({"IsVolumetric": True, "Length": float(dims[0]),
                     "Width": float(dims[1]), "Height": float(dims[2])})
    return item


def build_price_payload(data):
    s, r = sender_config(data.get("sender")), data["receiver"]
    return {
        "SenderStationId": s["station_id"],
        "ReceiverStationId": int(r["station_id"]),
        "VehicleType": int(data.get("vehicle_type", 2)),
        "SenderLocation": {"Latitude": s["lat"], "Longitude": s["lng"]},
        "ReceiverLocation": {"Latitude": float(r["lat"]), "Longitude": float(r["lng"])},
        "CustomerCode": customer_code(),
        "CustomerType": int(os.environ.get("GIG_CUSTOMER_TYPE", "0")),
        "PickUpOptions": int(data.get("pickup_option", 0)),
        "ShipmentItems": [_item(i) for i in data["items"]],
    }


def build_capture_payload(data):
    s, r = sender_config(data.get("sender")), data["receiver"]
    addr = r["address"]
    details = {
        "VehicleType": int(data.get("vehicle_type", 2)),
        "IsBatchPickUp": 0,
        "IsFromAgility": 0,
        "IsPriorityShipment": bool(data.get("is_priority")),
        "IsCashOnDelivery": bool(data.get("is_cod")),
    }
    if data.get("is_cod"):
        details["CashOnDeliveryAmount"] = float(data.get("cod_amount") or 0)
    return {
        "SenderDetails": {
            "SenderName": s["name"], "SenderPhoneNumber": s["phone"],
            "SenderStationId": s["station_id"],
            "SenderAddress": s["address"][:500], "InputtedSenderAddress": s["address"],
            "SenderLocality": s["locality"] or s["address"][:60],
            "SenderLocation": {"Latitude": s["lat"], "Longitude": s["lng"],
                               "FormattedAddress": s["address"], "Name": "", "LGA": ""},
        },
        "ReceiverDetails": {
            "ReceiverStationId": int(r["station_id"]),
            "ReceiverName": r["name"], "ReceiverPhoneNumber": r["phone"],
            "ReceiverAddress": addr[:500], "InputtedReceiverAddress": addr,
            "ReceiverLocation": {"Latitude": float(r["lat"]), "Longitude": float(r["lng"]),
                                 "FormattedAddress": addr, "Name": "", "LGA": ""},
        },
        "ShipmentDetails": details,
        "ShipmentItems": [_item(i) for i in data["items"]],
    }