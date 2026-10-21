import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from swiss_transport_mcp import siri_sx as sx

HEADER = b'<Siri xmlns="http://www.siri.org.uk/siri"><ServiceDelivery><SituationExchangeDelivery><Situations>'
FOOTER = b"</Situations></SituationExchangeDelivery></ServiceDelivery></Siri>"
ALERT = """<PtSituationElement><SituationNumber>1</SituationNumber><Summary xml:lang="fr">Genève: travaux</Summary><Description xml:lang="de">Lausanne gesperrt</Description><ValidityPeriod><StartTime>2020-01-01T00:00:00Z</StartTime><EndTime>2099-01-01T00:00:00Z</EndTime></ValidityPeriod><Progress>open</Progress></PtSituationElement>""".encode()
CONFIG = SimpleNamespace(
    base_url="https://api.opentransportdata.swiss/la/siri-sx", api_key="test-secret"
)


def feed():
    return sx.parse_chunks([HEADER, ALERT, FOOTER])


def fake_http(monkeypatch, body=HEADER + ALERT + FOOTER, code=200):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(code, content=body)

    real = httpx.Client
    monkeypatch.setattr(
        sx.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handle), **kw)
    )
    return calls


def test_stream_and_localized_filter(monkeypatch):
    alerts = sx.parse_chunks(
        (HEADER + ALERT + FOOTER)[i : i + 7] for i in range(0, len(HEADER + ALERT + FOOTER), 7)
    )
    result = {
        "status": "ok",
        "snapshot": {"fetched_at": time.time(), "alerts": alerts},
        "cached": True,
    }
    output = json.loads(sx.render(result, "Geneve-Lausanne", "FR", 3))
    assert output["matched_count"] == 1
    assert output["alerts"][0]["summary"]["text"] == "Genève: travaux"
    assert output["alerts"][0]["validity"] == "active"
    assert "NOT proof" in output["interpretation"]
    assert json.loads(sx.render(result, "Bern", "FR", 3))["matched_count"] == 0


@pytest.mark.parametrize(
    "body",
    [
        b"<html/>",
        b"<Siri>",
        HEADER + b"<Status>false</Status>" + FOOTER,
        b'<!DOCTYPE x [<!ENTITY y "z">]><x/>',
    ],
)
def test_invalid_feed(body):
    with pytest.raises((ValueError, sx.ET.ParseError)):
        sx.parse_chunks([body])


def test_empty_is_valid():
    assert sx.parse_chunks([HEADER, FOOTER]) == []


def test_limits(monkeypatch):
    monkeypatch.setattr(sx, "MAX_BYTES", 10)
    with pytest.raises(ValueError):
        sx.parse_chunks([HEADER])


def test_dates():
    alert = feed()[0]
    alert["periods"] = [{"start": "2099-01-01T00:00:00Z", "end": None}]
    assert sx.period_status(alert, time.time()) == "planned"
    alert["periods"] = [{"start": "2000-01-01T00:00:00Z", "end": "2001-01-01T00:00:00Z"}]
    assert sx.period_status(alert, time.time()) == "expired"
    alert["periods"] = [{"start": "2020-01-01T00:00:00", "end": None}]
    assert sx.period_status(alert, time.time()) == "unknown"
    alert["progress"] = "closed"
    assert sx.period_status(alert, time.time()) == "closed"


def test_cache_and_persistence(monkeypatch, tmp_path):
    calls = fake_http(monkeypatch)
    assert sx.refresh(CONFIG, tmp_path)["cached"] is False
    assert sx.refresh(CONFIG, tmp_path)["cached"] is True
    assert len(calls) == 1
    state = (tmp_path / "siri-state.json").read_text()
    assert "test-secret" not in state
    assert len(json.loads(state)["attempts"]) == 1


@pytest.mark.parametrize("count,age", [(10, 60), (48, 7200)])
def test_budget(monkeypatch, tmp_path, count, age):
    calls = fake_http(monkeypatch)
    sx.save_state(
        tmp_path / "siri-state.json", {"attempts": [time.time() - age] * count, "snapshot": None}
    )
    assert sx.refresh(CONFIG, tmp_path)["status"] == "rate_limited"
    assert not calls


@pytest.mark.parametrize(
    "code,status",
    [
        (401, "authentication_error"),
        (403, "authentication_error"),
        (429, "rate_limited"),
        (500, "upstream_error"),
    ],
)
def test_failures_consume_budget(monkeypatch, tmp_path, code, status):
    calls = fake_http(monkeypatch, code=code)
    assert sx.refresh(CONFIG, tmp_path)["status"] == status
    sx.refresh(CONFIG, tmp_path)
    assert len(calls) == 1
    assert len(sx.read_state(tmp_path / "siri-state.json")["attempts"]) == 1


def test_concurrent(monkeypatch, tmp_path):
    calls = fake_http(monkeypatch)
    monkeypatch.setenv("SIRI_SX_STATE_DIR", str(tmp_path))
    client = SimpleNamespace(_configs={"siri_sx": CONFIG})

    async def run():
        return await asyncio.gather(
            *(sx.get_disruptions(client, filter_text=q) for q in ["Genève", "Lausanne", "Bern"])
        )

    results = asyncio.run(run())
    assert len(calls) == 1
    assert all(json.loads(r)["status"] == "ok" for r in results)


def test_corrupt_state(monkeypatch, tmp_path):
    calls = fake_http(monkeypatch)
    (tmp_path / "siri-state.json").write_text("broken")
    monkeypatch.setenv("SIRI_SX_STATE_DIR", str(tmp_path))
    client = SimpleNamespace(_configs={"siri_sx": CONFIG})
    assert json.loads(asyncio.run(sx.get_disruptions(client)))["status"] == "state_error"
    assert not calls


def test_large_feed_memory():
    import tracemalloc

    # 40 MiB of irrelevant XML must not remain resident as a complete tree.
    block = b"<Extensions><Ignored>" + b"x" * 8000 + b"</Ignored></Extensions>"

    def chunks():
        yield HEADER
        for _ in range(5000):
            yield block
        yield ALERT
        yield FOOTER

    tracemalloc.start()
    try:
        assert len(sx.parse_chunks(chunks())) == 1
        _, peak = tracemalloc.get_traced_memory()
        assert peak < 8 * 1024 * 1024
    finally:
        tracemalloc.stop()


def test_process_lock(monkeypatch, tmp_path):
    calls = fake_http(monkeypatch)
    with (tmp_path / "siri.lock").open("a") as lock:
        sx.fcntl.flock(lock, sx.fcntl.LOCK_EX | sx.fcntl.LOCK_NB)
        assert sx.refresh(CONFIG, tmp_path)["status"] == "busy"
    assert not calls


def test_expired_snapshot_not_used_on_error(monkeypatch, tmp_path):
    fake_http(monkeypatch, code=500)
    sx.save_state(
        tmp_path / "siri-state.json",
        {"attempts": [], "snapshot": {"fetched_at": time.time() - sx.TTL - 1, "alerts": feed()}},
    )
    result = json.loads(sx.render(sx.refresh(CONFIG, tmp_path), "Genève", "FR", 3))
    assert result["status"] == "upstream_error"
    assert "alerts" not in result
