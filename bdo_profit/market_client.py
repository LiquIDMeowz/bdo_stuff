import time
from dataclasses import dataclass

import requests

ARSHA_BASE = "https://api.arsha.io/v2"


@dataclass
class MarketSnapshot:
    item_id: int
    sub_id: int
    name: str
    price: float
    current_stock: int
    last_sold_price: float
    fetched_at: float


class MarketClient:
    def __init__(
        self,
        region: str = "eu",
        min_interval: float = 1.0,
        max_retries: int = 4,
        cache_ttl: float = 300.0,
    ):
        self.region = region
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.cache_ttl = cache_ttl
        self._session = requests.Session()
        self._last_request_time = 0.0
        self._cache: dict[tuple[int, int], MarketSnapshot] = {}

    def get_price(self, item_id: int, sub_id: int = 0) -> MarketSnapshot | None:
        key = (item_id, sub_id)
        cached = self._cache.get(key)
        now = time.time()
        if cached and (now - cached.fetched_at) < self.cache_ttl:
            return cached

        data = self._get_with_retry(
            "GetWorldMarketSubList", {"id": item_id, "sid": sub_id, "lang": "en"}
        )
        if data is None:
            return cached  # stale fallback if we have one, else None

        snapshot = MarketSnapshot(
            item_id=item_id,
            sub_id=sub_id,
            name=str(data.get("name", f"Item {item_id}")),
            price=float(data.get("basePrice", 0) or 0),
            current_stock=int(data.get("currentStock", 0) or 0),
            last_sold_price=float(data.get("lastSoldPrice", 0) or 0),
            fetched_at=now,
        )
        self._cache[key] = snapshot
        return snapshot

    def _get_with_retry(self, endpoint: str, params: dict) -> dict | None:
        url = f"{ARSHA_BASE}/{self.region}/{endpoint}"
        for attempt in range(self.max_retries):
            self._respect_rate_limit()
            try:
                resp = self._session.get(url, params=params, timeout=15)
            except requests.RequestException:
                self._last_request_time = time.time()
                self._backoff(attempt)
                continue
            self._last_request_time = time.time()
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    return None
            if resp.status_code == 500 and "Imperva" in (resp.text or ""):
                self._backoff(attempt)
                continue
            return None
        return None

    def _respect_rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def _backoff(self, attempt: int) -> None:
        time.sleep(min(2**attempt, 30))
