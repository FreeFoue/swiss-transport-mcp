import pytest
from swiss_transport_mcp.request_times import request_time
from swiss_transport_mcp import ojp_client

@pytest.mark.parametrize('value,zone,expected',[
 ('2026-10-22T09:30:00','Europe/Zurich','2026-10-22T07:30:00Z'),
 ('2026-01-22T09:30','Europe/Zurich','2026-01-22T08:30:00Z'),
 ('2026-10-22T09:30:00Z','Europe/Zurich','2026-10-22T09:30:00Z'),
 ('2026-10-22T09:30:00+02:00','Europe/Zurich','2026-10-22T07:30:00Z'),
 ('2026-10-22T00:30','Europe/Zurich','2026-10-21T22:30:00Z'),
 ('2026-10-22T09:30','Europe/Lisbon','2026-10-22T08:30:00Z'),
 ('2026-10-22T09:30','Europe/London','2026-10-22T08:30:00Z'),
 ('2026-10-22T09:30','Europe/Bucharest','2026-10-22T06:30:00Z'),
 ('2026-10-25T02:30+02:00','Europe/Zurich','2026-10-25T00:30:00Z'),
 ('2026-10-25T02:30+01:00','Europe/Zurich','2026-10-25T01:30:00Z'),
])
def test_instants(value,zone,expected):
    assert request_time(value,zone)==expected

@pytest.mark.parametrize('value',['2026-03-29T02:30','2026-10-25T02:30','2026-10-22','tomorrow','2026-13-01T09:30'])
def test_reject(value):
    with pytest.raises(ValueError): request_time(value)

def test_xml():
    assert '2026-10-22T07:30:00Z' in ojp_client.build_trip_request('8501120','8501030',dep_time='2026-10-22T09:30')
    assert '2026-10-22T07:30:00Z' in ojp_client.build_stop_event_request('8501120',dep_arr_time='2026-10-22T09:30')
    assert '2026-10-22T08:30:00Z' in ojp_client.build_trip_request('8501120','8501030',dep_time='2026-10-22T09:30',time_zone='Europe/London')

def test_now():
    from datetime import datetime,UTC
    assert abs((datetime.fromisoformat(request_time(None))-datetime.now(UTC)).total_seconds())<5
