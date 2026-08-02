from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from engine_v2.domain.enums import DataQuality
from engine_v2.domain.models import Candle, Observation, TradeExecutionActual


def fixture_observations(product_id: str = "BTC_BINANCE_PERP", *, count: int = 80) -> list[Observation]:
    """Deterministic fixture used by tests and no-key smoke mode, not live fallback data."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    result: list[Observation] = []
    price = 60_000.0
    for index in range(count):
        open_time = now - timedelta(minutes=15 * (count - index))
        close_time = open_time + timedelta(minutes=15)
        close = price * (1 + (0.0008 if index % 7 else -0.0003))
        candle = Candle(product_id, "15m", open_time, close_time, price, max(price, close) * 1.001, min(price, close) * 0.999, close, 100 + index, 6_000_000 + index * 10_000, 100 + index, True, "fixture", now, now, DataQuality.OK)
        result.append(Observation(str(uuid4()), "fixture", "fixture", product_id, "candle_15m", open_time, None, now, now, now, None, DataQuality.OK, "2.0", candle.to_dict()))
        trade_time = close_time - timedelta(seconds=10)
        trade = TradeExecutionActual(product_id, str(index), close, 0.25 + (index % 3) * 0.05, "buy" if index % 4 else "sell", trade_time, "fixture")
        result.append(Observation(str(uuid4()), "fixture", "fixture", product_id, "trade", trade_time, None, now, now, now, None, DataQuality.OK, "2.0", trade.to_dict()))
        price = close
    forming_open = now
    forming = Candle(product_id, "15m", forming_open, forming_open + timedelta(minutes=15), price, price * 1.001, price * 0.999, price * 1.0005, 20, 1_200_000, 20, False, "fixture", now, now, DataQuality.OK)
    result.append(Observation(str(uuid4()), "fixture", "fixture", product_id, "candle_15m", forming_open, None, now, now, now, None, DataQuality.OK, "2.0", forming.to_dict()))
    return result
