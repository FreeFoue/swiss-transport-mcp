"""Offline place timezone resolution; never infer a zone from a passenger or ID."""
import math
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from tzfpy import get_tzs

OJP = 'http://www.vdv.de/ojp'
SIRI = 'http://www.siri.org.uk/siri'

def text(node, namespace, name):
    if node is None:
        return None
    el = node.find('./{' + namespace + '}' + name)
    return el.text.strip() if el is not None and el.text else None

@lru_cache(maxsize=2048)
def coordinate_zone(lon, lat):
    if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    zones = get_tzs(lon, lat)
    if len(zones) != 1:
        return None
    try:
        ZoneInfo(zones[0])
    except ZoneInfoNotFoundError:
        return None
    return zones[0]

def place_zones(root):
    result = {}
    for place in root.iter('{' + OJP + '}Place'):
        geo = place.find('./{' + OJP + '}GeoPosition')
        try:
            zone = coordinate_zone(float(text(geo, SIRI, 'Longitude')), float(text(geo, SIRI, 'Latitude')))
        except (TypeError, ValueError):
            zone = None
        for kind, ns, ref in [('StopPlace', OJP, 'StopPlaceRef'), ('StopPoint', SIRI, 'StopPointRef')]:
            item = place.find('./{' + OJP + '}' + kind)
            key = text(item, ns, ref)
            if key:
                if key in result and result[key] != zone:
                    result[key] = None
                else:
                    result[key] = zone
    return result

def endpoint_zone(node, zones):
    ref = text(node, SIRI, 'StopPointRef') or text(node, OJP, 'StopPlaceRef')
    return zones.get(ref)

def add_time(data, key, zone):
    if key not in data:
        return
    data[key + '_timezone'] = zone
    data[key + '_local'] = None
    data[key + '_display'] = None
    data[key + '_local_status'] = 'unknown_timezone'
    if not zone:
        return
    try:
        value = datetime.fromisoformat(data[key].replace('Z', '+00:00'))
        if value.tzinfo is None or value.utcoffset() is None:
            data[key + '_local_status'] = 'missing_source_offset'
            return
        local = value.astimezone(ZoneInfo(zone))
    except (ValueError, TypeError, AttributeError, ZoneInfoNotFoundError):
        data[key + '_local_status'] = 'invalid_timestamp_or_timezone'
        return
    data[key + '_local'] = local.isoformat()
    data[key + '_display'] = local.strftime('%Y-%m-%d %H:%M') + ' (' + zone + ')'
    data[key + '_local_status'] = 'resolved'

def enrich_event(event, call, zones):
    zone = endpoint_zone(call, zones)
    for key in ('scheduled_time', 'realtime_time'):
        add_time(event, key, zone)

def enrich_leg(leg, element, zones):
    board = element.find('.//{' + OJP + '}LegBoard')
    alight = element.find('.//{' + OJP + '}LegAlight')
    for key in ('departure', 'departure_realtime'):
        add_time(leg, key, endpoint_zone(board, zones))
    for key in ('arrival', 'arrival_realtime'):
        add_time(leg, key, endpoint_zone(alight, zones))

def enrich_trip(trip):
    legs = trip.get('legs', [])
    # A walk/transfer endpoint has no proven timezone here: do not borrow a distant stop's zone.
    first = legs[0].get('departure_timezone') if legs else None
    last = legs[-1].get('arrival_timezone') if legs else None
    add_time(trip, 'start_time', first)
    add_time(trip, 'end_time', last)
