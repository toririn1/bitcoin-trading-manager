from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .enums import AssetClass, DataQuality, ProductType
from .models import AssetSpec, ProductSpec


@dataclass(slots=True)
class RegistryStatus:
    supported: bool
    configured: bool
    status: str
    reason: str | None = None


@dataclass
class AssetRegistry:
    assets: dict[str, AssetSpec] = field(default_factory=dict)
    products: dict[str, ProductSpec] = field(default_factory=dict)
    statuses: dict[str, RegistryStatus] = field(default_factory=dict)

    def register_asset(self, asset: AssetSpec) -> AssetSpec:
        self.assets[asset.asset_id] = asset
        self.statuses.setdefault(asset.asset_id, RegistryStatus(True, False, "supported"))
        return asset

    def register_product(self, product: ProductSpec) -> ProductSpec:
        self.products[product.product_id] = product
        self.statuses[product.product_id] = RegistryStatus(
            supported=True,
            configured=product.is_tradable,
            status="configured" if product.is_tradable else "supported",
        )
        return product

    def mark(self, key: str, status: str, *, reason: str | None = None, supported: bool = True, configured: bool = False) -> None:
        self.statuses[key] = RegistryStatus(supported, configured, status, reason)

    def register_discovered_products(self, products: Iterable[ProductSpec]) -> list[ProductSpec]:
        registered = []
        for product in products:
            registered.append(self.register_product(product))
        return registered

    def product(self, product_id: str) -> ProductSpec | None:
        return self.products.get(product_id)

    def products_for(self, underlying_id: str) -> list[ProductSpec]:
        return [p for p in self.products.values() if p.underlying_id == underlying_id]

    def tradable_products(self) -> list[ProductSpec]:
        return [
            p for p in self.products.values()
            if p.is_tradable and p.role == "tradable"
        ]

    def reference_products(self) -> list[ProductSpec]:
        return [p for p in self.products.values() if p.role == "reference"]

    def to_dict(self) -> dict:
        return {
            "assets": [item.to_dict() for item in self.assets.values()],
            "products": [item.to_dict() for item in self.products.values()],
            "statuses": {key: {"supported": value.supported, "configured": value.configured, "status": value.status, "reason": value.reason} for key, value in self.statuses.items()},
        }


def build_default_registry() -> AssetRegistry:
    registry = AssetRegistry()
    assets = [
        AssetSpec("BTC", "Bitcoin", asset_class=AssetClass.CRYPTO, base_currency="BTC", quote_currency="USD"),
        AssetSpec("ETH", "Ethereum", asset_class=AssetClass.CRYPTO, base_currency="ETH", quote_currency="USD"),
        AssetSpec("ETH_BTC", "Ethereum / Bitcoin", asset_class=AssetClass.CRYPTO),
        AssetSpec("STABLECOIN_LIQUIDITY", "Stablecoin supply / liquidity proxy", asset_class=AssetClass.STABLECOIN),
        AssetSpec("BTC_ETF_FLOW", "Bitcoin ETF aggregate flow", asset_class=AssetClass.ETF),
        AssetSpec("NQ", "Nasdaq 100", asset_class=AssetClass.INDEX, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("QQQ", "Invesco QQQ", asset_class=AssetClass.ETF, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("SPY", "SPDR S&P 500", asset_class=AssetClass.ETF, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("VIX", "CBOE Volatility Index", asset_class=AssetClass.INDEX, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("HYG", "High Yield Corporate Bond ETF", asset_class=AssetClass.ETF, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("LQD", "Investment Grade Corporate Bond ETF", asset_class=AssetClass.ETF, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("SOXX", "iShares Semiconductor ETF", asset_class=AssetClass.ETF, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("SMH", "VanEck Semiconductor ETF", asset_class=AssetClass.ETF, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("SOXL", "Direxion Daily Semiconductor Bull 3X", asset_class=AssetClass.ETF, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("NVDA", "NVIDIA", asset_class=AssetClass.EQUITY, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("MU", "Micron Technology", asset_class=AssetClass.EQUITY, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("TSM", "Taiwan Semiconductor", asset_class=AssetClass.EQUITY, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("SK_HYNIX_KRX", "SK Hynix", asset_class=AssetClass.EQUITY, country="KR", timezone="Asia/Seoul", calendar_id="XKRX"),
        AssetSpec("SAMSUNG_KRX", "Samsung Electronics", asset_class=AssetClass.EQUITY, country="KR", timezone="Asia/Seoul", calendar_id="XKRX"),
        AssetSpec("KOSPI", "KOSPI", asset_class=AssetClass.INDEX, country="KR", timezone="Asia/Seoul", calendar_id="XKRX"),
        AssetSpec("KOSDAQ", "KOSDAQ", asset_class=AssetClass.INDEX, country="KR", timezone="Asia/Seoul", calendar_id="XKRX"),
        AssetSpec("USD_KRW", "US Dollar / Korean Won", asset_class=AssetClass.FX, country="KR", timezone="Asia/Seoul"),
        AssetSpec("US_2Y", "US Treasury 2Y", asset_class=AssetClass.RATE, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("US_10Y", "US Treasury 10Y", asset_class=AssetClass.RATE, country="US", timezone="America/New_York", calendar_id="XNYS"),
        AssetSpec("DXY", "US Dollar Index", asset_class=AssetClass.FX),
        AssetSpec("WTI", "WTI Crude", asset_class=AssetClass.COMMODITY),
        AssetSpec("GOLD", "Gold", asset_class=AssetClass.COMMODITY),
    ]
    for asset in assets:
        registry.register_asset(asset)

    # Only BTC products with known public venue identifiers are seeded. Equity,
    # CFD, and Gate products are added only after provider discovery.
    for product in (
        ProductSpec("BTC_BINANCE_PERP", "BTC", "binance", "binance_futures", "BTCUSDT", ProductType.PERPETUAL, quote_currency="USDT", settlement_currency="USDT", funding_supported=True, short_supported=True, is_tradable=True, price_source="binance_public", role="tradable", contract_type="perpetual", settlement_asset="USDT", taker_fee_bps=4.0, maker_fee_bps=2.0),
        ProductSpec("BTC_BINANCE_SPOT", "BTC", "binance", "binance_spot", "BTCUSDT", ProductType.SPOT, quote_currency="USDT", settlement_currency="USDT", short_supported=False, is_tradable=True, price_source="binance_public", role="tradable", contract_type="spot", settlement_asset="USDT", taker_fee_bps=10.0, maker_fee_bps=10.0),
        ProductSpec("BTC_BYBIT_PERP", "BTC", "bybit", "bybit_linear", "BTCUSDT", ProductType.PERPETUAL, quote_currency="USDT", settlement_currency="USDT", funding_supported=True, short_supported=True, is_tradable=True, price_source="bybit_public", role="tradable", contract_type="perpetual", settlement_asset="USDT", taker_fee_bps=5.5, maker_fee_bps=2.0),
    ):
        registry.register_product(product)
    for key in ("SOXL", "SK_HYNIX_KRX"):
        registry.mark(key, "temporarily_unavailable", reason="waiting_for_official_product_discovery", configured=False)
    return registry
