import time
from unittest.mock import patch, MagicMock

from bdo_profit.market_client import MarketClient


def _mock_response(json_data, status_code=200, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = text or ""
    return resp


def test_get_price_parses_snapshot():
    client = MarketClient(min_interval=0)
    payload = {
        "name": "Caphras Stone",
        "id": 721003,
        "sid": 0,
        "basePrice": 885000,
        "currentStock": 486322,
        "totalTrades": 2567382461,
        "lastSoldPrice": 820000,
    }
    with patch.object(client._session, "get", return_value=_mock_response(payload)) as get:
        snap = client.get_price(721003)
    assert snap.name == "Caphras Stone"
    assert snap.price == 885000
    assert snap.current_stock == 486322
    assert snap.total_trades == 2567382461
    get.assert_called_once()
    call_url = get.call_args[0][0]
    assert "GetWorldMarketSubList" in call_url
    assert get.call_args[1]["params"]["id"] == 721003


def test_get_price_defaults_total_trades_to_zero_when_missing():
    client = MarketClient(min_interval=0)
    payload = {"name": "X", "id": 1, "sid": 0, "basePrice": 100, "currentStock": 1, "lastSoldPrice": 100}
    with patch.object(client._session, "get", return_value=_mock_response(payload)):
        snap = client.get_price(1)
    assert snap.total_trades == 0


def test_get_price_caches_within_ttl():
    client = MarketClient(min_interval=0, cache_ttl=300)
    payload = {"name": "X", "id": 1, "sid": 0, "basePrice": 100, "currentStock": 1, "lastSoldPrice": 100}
    with patch.object(client._session, "get", return_value=_mock_response(payload)) as get:
        client.get_price(1)
        client.get_price(1)
    assert get.call_count == 1


def test_get_price_retries_on_imperva_block():
    client = MarketClient(min_interval=0, max_retries=3)
    blocked = _mock_response({}, status_code=500, text='{"code":103,"message":"Imperva"}')
    ok_payload = {"name": "X", "id": 1, "sid": 0, "basePrice": 100, "currentStock": 1, "lastSoldPrice": 100}
    ok = _mock_response(ok_payload)
    with patch.object(client._session, "get", side_effect=[blocked, ok]) as get, \
         patch("time.sleep", return_value=None):
        snap = client.get_price(1)
    assert snap.price == 100
    assert get.call_count == 2


def test_get_price_handles_list_response_and_picks_matching_sid():
    # Items with enhancement levels (accessories etc.) return a LIST of per-sid
    # objects even when a single sid is requested.
    client = MarketClient(min_interval=0)
    payload = [
        {"name": "Tungrad Necklace", "id": 11629, "sid": 0, "basePrice": 1000, "currentStock": 5, "lastSoldPrice": 900},
        {"name": "Tungrad Necklace", "id": 11629, "sid": 1, "basePrice": 5000, "currentStock": 2, "lastSoldPrice": 4800},
        {"name": "Tungrad Necklace", "id": 11629, "sid": 2, "basePrice": 20000, "currentStock": 1, "lastSoldPrice": 19000},
    ]
    with patch.object(client._session, "get", return_value=_mock_response(payload)):
        snap = client.get_price(11629, sub_id=1)
    assert snap is not None
    assert snap.name == "Tungrad Necklace"
    assert snap.sub_id == 1
    assert snap.price == 5000
    assert snap.current_stock == 2
    assert snap.last_sold_price == 4800


def test_get_price_list_response_falls_back_to_first_when_sid_absent():
    client = MarketClient(min_interval=0)
    payload = [
        {"name": "Thing", "id": 42, "sid": 0, "basePrice": 100, "currentStock": 1, "lastSoldPrice": 90},
    ]
    with patch.object(client._session, "get", return_value=_mock_response(payload)):
        snap = client.get_price(42, sub_id=7)
    assert snap is not None
    assert snap.price == 100


def test_get_price_empty_list_response_returns_none_without_crashing():
    client = MarketClient(min_interval=0)
    with patch.object(client._session, "get", return_value=_mock_response([])):
        snap = client.get_price(999)
    assert snap is None


def test_get_price_empty_list_response_falls_back_to_stale_cache():
    client = MarketClient(min_interval=0, cache_ttl=0.01)
    ok_payload = {"name": "X", "id": 1, "sid": 0, "basePrice": 100, "currentStock": 1, "lastSoldPrice": 100}
    with patch.object(client._session, "get", return_value=_mock_response(ok_payload)):
        client.get_price(1)
    time.sleep(0.02)
    with patch.object(client._session, "get", return_value=_mock_response([])):
        snap = client.get_price(1)
    assert snap is not None
    assert snap.price == 100


def test_get_price_falls_back_to_stale_cache_on_persistent_failure():
    client = MarketClient(min_interval=0, cache_ttl=0.01, max_retries=1)
    ok_payload = {"name": "X", "id": 1, "sid": 0, "basePrice": 100, "currentStock": 1, "lastSoldPrice": 100}
    with patch.object(client._session, "get", return_value=_mock_response(ok_payload)):
        client.get_price(1)
    time.sleep(0.02)
    blocked = _mock_response({}, status_code=500, text='{"code":103}')
    with patch.object(client._session, "get", return_value=blocked), \
         patch("time.sleep", return_value=None):
        snap = client.get_price(1)
    assert snap is not None
    assert snap.price == 100
