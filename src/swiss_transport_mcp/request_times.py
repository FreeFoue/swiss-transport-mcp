"""Explicit request-time semantics, independent of host/passenger timezone."""
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def request_time(value: str | None, time_zone: str = 'Europe/Zurich') -> str:
    if value is None:
        return datetime.now(UTC).isoformat().replace('+00:00', 'Z')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?', value):
        raise ValueError('Use an ISO date and time, for example 2026-10-22T09:30:00.')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        try:
            zone = ZoneInfo(time_zone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise ValueError('Unknown time_zone. Supply an IANA zone such as Europe/Zurich.') from None
        candidates = set()
        for fold in (0, 1):
            utc = dt.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            if utc.astimezone(zone).replace(tzinfo=None) == dt:
                candidates.add(utc)
        if not candidates:
            raise ValueError('This local time does not exist due to a clock change. Ask for another time.')
        if len(candidates) != 1:
            raise ValueError('This local time is ambiguous due to a clock change. Ask for an explicit UTC offset.')
        dt = candidates.pop()
    return dt.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def validate_journey_time(value: str | None, time_zone: str = "Europe/Zurich",
                          *, now: datetime | None = None) -> str:
    """Upcoming journeys only; allow ten minutes for conversational latency."""
    normalized = request_time(value, time_zone)
    instant = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    current = now if now is not None else datetime.now(UTC)
    if value is not None and (current - instant).total_seconds() > 600:
        raise PastJourneyTime(
            f"Requested time {normalized} is in the past. Current UTC time: "
            f"{current.isoformat()}. Check the year, date and timezone. "
            "Do not change stops or retry unchanged; ask the user if the intended date is unclear."
        )
    return normalized


class PastJourneyTime(ValueError):
    """A journey request targets a past instant, not an API failure."""
