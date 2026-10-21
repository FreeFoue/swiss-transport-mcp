"""Bounded SIRI-SX parsing and persistent shared snapshot/quota handling.

No passenger queries or credentials are written to disk. A snapshot is textual
search evidence, not proof that an entire itinerary is disruption-free.
"""

import asyncio
import fcntl
import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .api_infrastructure import TransportAPIClient
from .net_security import resolve_ssl_verify, validate_egress_url

TTL = 35 * 60
MAX_BYTES = 128 * 1024 * 1024
MAX_ALERTS = 20000
MAX_ELEMENT_BYTES = 2 * 1024 * 1024


def name(tag):
    return tag.rsplit("}", 1)[-1]


def iso(value):
    return datetime.fromtimestamp(value, UTC).isoformat()


def parse_date(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp() if dt.utcoffset() is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def normalize(value):
    return "".join(
        c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c)
    )


def extract(element):
    def texts(tag):
        return [" ".join(e.itertext()).strip()[:4000] for e in element.iter() if name(e.tag) == tag]

    localized = {}
    for tag in ("Summary", "Description"):
        values = {}
        for e in element.iter():
            if name(e.tag) != tag:
                continue
            lang = e.get("{http://www.w3.org/XML/1998/namespace}lang", "").lower()
            for child in e.iter():
                lang = lang or child.get("{http://www.w3.org/XML/1998/namespace}lang", "").lower()
            text = " ".join(" ".join(e.itertext()).split())[:4000]
            if text:
                values.setdefault(lang or "und", text)
        localized[tag.lower()] = values
    periods = []
    for e in element.iter():
        if name(e.tag) == "ValidityPeriod":
            fields = {name(c.tag): (c.text or "").strip() for c in e}
            periods.append({"start": fields.get("StartTime"), "end": fields.get("EndTime")})
    affected = sorted(
        set(
            v
            for tag in (
                "LineRef",
                "StopPointRef",
                "StopPlaceRef",
                "PublishedLineName",
                "StopPointName",
                "OperatorRef",
            )
            for v in texts(tag)
            if v
        )
    )[:200]
    return {
        "truncated_fields": sorted(
            k.removeprefix("truncated_") for k in element.attrib if k.startswith("truncated_")
        ),
        "id": next(iter(texts("SituationNumber")), ""),
        **localized,
        "affected": affected,
        "periods": periods,
        "severity": next(iter(texts("Severity")), "unknown"),
        "progress": next(iter(texts("Progress")), "unknown"),
    }


def parse_chunks(chunks):
    """Retain only bounded passenger fields, never a whole situation tree."""
    parser = ET.XMLPullParser(events=("start", "end"))
    stack, alerts = [], []
    total = since_release = 0
    delivery = False
    situation = capture = None
    counts = {}
    tail = b""
    wanted = {
        "Summary",
        "Description",
        "ValidityPeriod",
        "SituationNumber",
        "Severity",
        "Progress",
        "LineRef",
        "StopPointRef",
        "StopPlaceRef",
        "PublishedLineName",
        "StopPointName",
        "OperatorRef",
    }
    for chunk in chunks:
        total += len(chunk)
        since_release += len(chunk)
        if total > MAX_BYTES:
            raise ValueError("feed_size_limit")
        if since_release > MAX_ELEMENT_BYTES:
            raise ValueError("element_size_limit")
        scan = tail + chunk
        if b"<!DOCTYPE" in scan.upper() or b"<!ENTITY" in scan.upper() or b"\x00" in chunk:
            raise ValueError("unsupported_xml")
        tail = scan[-16:]
        parser.feed(chunk)
        for event, e in parser.read_events():
            tag = name(e.tag)
            if event == "start":
                stack.append(e)
                if tag == "SituationExchangeDelivery":
                    delivery = True
                if tag == "PtSituationElement":
                    if situation is not None:
                        raise ValueError("unrecognized_feed")
                    situation = ET.Element("PtSituationElement")
                    counts = {}
                elif situation is not None and capture is None and tag in wanted:
                    capture = e
                continue
            if tag == "ErrorCondition" or (
                tag == "Status" and (e.text or "").strip().lower() == "false"
            ):
                raise ValueError("upstream_error")
            if capture is e:
                # Collapse each field now, including nested multilingual text.
                # Independent budgets preserve text even after thousands of refs.
                budget = 32 if tag in ("Summary", "Description", "ValidityPeriod") else 200
                counts[tag] = counts.get(tag, 0) + 1
                keep = counts[tag] <= budget
                if not keep:
                    situation.set("truncated_" + tag, "true")
                field = ET.SubElement(situation, tag) if keep else ET.Element(tag)
                if tag == "ValidityPeriod":
                    for child in e:
                        if name(child.tag) in ("StartTime", "EndTime"):
                            ET.SubElement(field, name(child.tag)).text = (child.text or "")[:100]
                else:
                    field.text = " ".join(" ".join(e.itertext()).split())[:4000]
                    for child in e.iter():
                        lang = child.get("{http://www.w3.org/XML/1998/namespace}lang")
                        if lang:
                            field.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
                            break
                capture = None
            if tag == "PtSituationElement":
                alerts.append(extract(situation))
                if len(alerts) > MAX_ALERTS:
                    raise ValueError("alert_count_limit")
                situation = None
            if capture is None:
                if len(stack) > 1:
                    stack[-2].remove(e)
                e.clear()
                since_release = 0
            stack.pop()
    parser.close()
    if not delivery:
        raise ValueError("unrecognized_feed")
    return alerts


def read_state(path):
    if not path.exists():
        return {"attempts": [], "snapshot": None, "retry_after": 0}
    data = json.loads(path.read_text())
    if not isinstance(data.get("attempts"), list) or "snapshot" not in data:
        raise ValueError("invalid_state")
    return data


def save_state(path, state):
    temp = path.with_suffix(".tmp")
    with temp.open("w") as f:
        os.chmod(temp, 0o600)
        json.dump(state, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def refresh(config, directory):
    """Serialize across processes; reserve budget durably before any network call."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "siri.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "status": "busy",
                "message": "Refresh already in progress; no conclusion possible.",
            }
        path = directory / "siri-state.json"
        state = read_state(path)  # Corrupt state fails closed: never reset quota silently.
        now = time.time()
        snapshot = state["snapshot"]
        if snapshot and 0 <= now - snapshot["fetched_at"] < TTL:
            return {"status": "ok", "snapshot": snapshot, "cached": True}
        attempts = [t for t in state["attempts"] if t > now - 86400]
        state["attempts"] = attempts
        if (
            len(attempts) >= 48
            or sum(t > now - 3600 for t in attempts) >= 10
            or now < state.get("retry_after", 0)
        ):
            return {"status": "rate_limited", "message": "No fresh verification available."}
        attempts.append(now)
        save_state(path, state)
        try:
            with httpx.Client(
                timeout=30, follow_redirects=True, verify=resolve_ssl_verify()
            ) as http:
                with http.stream(
                    "GET",
                    validate_egress_url(config.base_url),
                    headers={
                        "Authorization": f"Bearer {config.api_key}",
                        "Accept": "application/xml",
                    },
                ) as response:
                    response.raise_for_status()
                    deadline = time.monotonic() + 60

                    def chunks():
                        for chunk in response.iter_bytes(chunk_size=16384):
                            if time.monotonic() > deadline:
                                raise TimeoutError()
                            yield chunk

                    alerts = parse_chunks(chunks())
            state["snapshot"] = {"fetched_at": time.time(), "alerts": alerts}
            state["retry_after"] = 0
            save_state(path, state)
            return {"status": "ok", "snapshot": state["snapshot"], "cached": False}
        except (httpx.HTTPError, ValueError, ET.ParseError, TimeoutError) as exc:
            status = "unavailable"
            if isinstance(exc, httpx.HTTPStatusError):
                code = exc.response.status_code
                status = "authentication_error" if code in (401, 403) else "upstream_error"
                if code == 429:
                    status = "rate_limited"
            elif isinstance(exc, (ValueError, ET.ParseError)):
                status = "invalid_feed"
            # Only fixed diagnostic codes, never response bodies or signed URLs.
            diagnostic = {"code": "request_failed"}
            if isinstance(exc, ET.ParseError):
                diagnostic = {"code": "xml_parse_error", "position": exc.position}
            elif isinstance(exc, ValueError):
                known = {
                    "feed_size_limit",
                    "element_size_limit",
                    "unsupported_xml",
                    "upstream_error",
                    "alert_count_limit",
                    "alert_fields_limit",
                    "unrecognized_feed",
                }
                diagnostic = {"code": str(exc) if str(exc) in known else "invalid_value"}
            elif isinstance(exc, httpx.HTTPStatusError):
                diagnostic = {"code": "http_error", "http_status": exc.response.status_code}
            elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                diagnostic = {"code": "download_timeout"}
            # No automatic retries; every failed download still consumes budget.
            state["retry_after"] = time.time() + (3600 if status == "rate_limited" else 300)
            save_state(path, state)
            return {
                "status": status,
                "message": "Verification failed. Do not infer no disruptions.",
                "diagnostic": diagnostic,
            }


def period_status(alert, now):
    if "ValidityPeriod" in alert.get("truncated_fields", []):
        return "unknown"
    if alert["progress"].lower() == "closed":
        return "closed"
    states = []
    for period in alert["periods"]:
        start, end = parse_date(period["start"]), parse_date(period["end"])
        if start is None or (period["end"] and end is None) or (end is not None and end < start):
            states.append("unknown")
        elif start > now:
            states.append("planned")
        elif end is not None and end <= now:
            states.append("expired")
        else:
            states.append("active")
    for status in ("active", "unknown", "planned", "expired"):
        if status in states:
            return status
    return "unknown"


def render(result, query, language, limit):
    if result["status"] != "ok":
        return json.dumps(result, ensure_ascii=False)
    snapshot = result["snapshot"]
    now = time.time()
    terms = re.findall(r"\w+", normalize(query or ""))
    selected = []
    unreadable = 0
    incomplete = sum(bool(a.get("truncated_fields")) for a in snapshot["alerts"])
    for alert in snapshot["alerts"]:
        if not alert["summary"] and not alert["description"]:
            unreadable += 1
            continue
        searchable = normalize(
            " ".join(
                [*alert["summary"].values(), *alert["description"].values(), *alert["affected"]]
            )
        )
        if terms and not any(
            re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", searchable) for t in terms
        ):
            continue
        validity = period_status(alert, now)
        if validity in ("expired", "closed"):
            continue

        def pick(values):
            key = language.lower() if language.lower() in values else next(iter(values), None)
            return {"text": values.get(key, ""), "language": key}

        selected.append(
            {
                **alert,
                "summary": pick(alert["summary"]),
                "description": pick(alert["description"]),
                "validity": validity,
            }
        )
    severity = {"severe": 0, "normal": 1, "slight": 2}
    selected.sort(key=lambda x: (x["validity"] != "active", severity.get(x["severity"], 3)))
    limit = max(1, min(int(limit), 50))
    return json.dumps(
        {
            "status": "ok",
            "source": "SIRI-SX",
            "fetched_at": iso(snapshot["fetched_at"]),
            "age_seconds": max(0, int(now - snapshot["fetched_at"])),
            "cache_ttl_seconds": TTL,
            "cached": result["cached"],
            "query": query,
            "match_mode": "any_term_text_search",
            "scope": "text_matches_not_verified_route_coverage",
            "matched_count": len(selected),
            "truncated": len(selected) > limit,
            "unreadable_alerts": unreadable,
            "incomplete_alerts_in_snapshot": incomplete,
            "alerts": selected[:limit],
            "interpretation": "No matches is NOT proof of no disruptions on a route. "
            "Truncated fields mean incomplete details and potentially missed text matches. "
            "Planned/unknown validity alerts are NOT confirmed active. "
            "Report snapshot age. Times retain UTC offsets; do not guess local times.",
        },
        ensure_ascii=False,
    )


async def get_disruptions(
    client: TransportAPIClient, filter_text=None, language="DE", max_results=20
):
    config = client._configs.get("siri_sx")
    if not config:
        return json.dumps(
            {"status": "not_configured", "message": "Disruption verification unavailable."}
        )
    if not hasattr(client, "_siri_lock"):
        client._siri_lock = asyncio.Lock()
    async with client._siri_lock:
        try:
            directory = Path(
                os.environ.get("SIRI_SX_STATE_DIR", "~/.local/state/ridolo")
            ).expanduser()
            result = await asyncio.to_thread(refresh, config, directory)
            return render(result, filter_text, language, max_results)
        except (OSError, ValueError, TypeError, KeyError):
            return json.dumps(
                {"status": "state_error", "message": "Cannot verify disruptions safely."}
            )
