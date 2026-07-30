"""BaseExchangeAdapter — Abstract interface for all exchange adapters.

Each exchange adapter must implement this interface to provide
unified access to options market data regardless of the underlying
exchange API specifics.
"""

import time
import logging
from abc import ABC, abstractmethod
from typing import Optional

log = logging.getLogger(__name__)


class ExchangeHealth:
    """Health status snapshot for an exchange adapter."""

    STATUS_ONLINE = "ONLINE"
    STATUS_DEGRADED = "DEGRADED"      # latency > 2s
    STATUS_DELAYED = "DELAYED"        # stale > 5s
    STATUS_OFFLINE = "OFFLINE"        # no data

    __slots__ = ("status", "latency_ms", "last_update_ts", "stale_seconds",
                 "error_message", "ws_connected")

    def __init__(self):
        self.status: str = self.STATUS_OFFLINE
        self.latency_ms: float = 0.0
        self.last_update_ts: float = 0.0
        self.stale_seconds: float = 0.0
        self.error_message: str = ""
        self.ws_connected: bool = False

    def update(self, latency_ms: float = 0.0, ws_connected: bool = False):
        """Update health metrics and recalculate status."""
        now = time.time()
        self.latency_ms = latency_ms
        self.ws_connected = ws_connected
        self.last_update_ts = now
        self.stale_seconds = 0.0
        self.error_message = ""
        self._recalculate_status()

    def mark_error(self, error: str):
        """Mark exchange as having an error."""
        self.error_message = error
        self.status = self.STATUS_OFFLINE

    def refresh_staleness(self):
        """Recalculate staleness based on current time."""
        if self.last_update_ts > 0:
            self.stale_seconds = time.time() - self.last_update_ts
        self._recalculate_status()

    def _recalculate_status(self):
        """Determine status from latency and staleness."""
        if self.last_update_ts == 0:
            self.status = self.STATUS_OFFLINE
        elif self.stale_seconds > 10:
            self.status = self.STATUS_OFFLINE
        elif self.stale_seconds > 5:
            self.status = self.STATUS_DELAYED
        elif self.latency_ms > 2000:
            self.status = self.STATUS_DEGRADED
        else:
            self.status = self.STATUS_ONLINE

    def to_dict(self) -> dict:
        """Serialize health status."""
        return {
            "status": self.status,
            "latency_ms": round(self.latency_ms, 1),
            "last_update": self.last_update_ts,
            "stale_seconds": round(self.stale_seconds, 1),
            "ws_connected": self.ws_connected,
            "error": self.error_message,
        }

    @property
    def is_healthy(self) -> bool:
        return self.status in (self.STATUS_ONLINE, self.STATUS_DEGRADED)

    @property
    def is_usable(self) -> bool:
        """Can this exchange contribute to aggregation?"""
        return self.status != self.STATUS_OFFLINE


class BaseExchangeAdapter(ABC):
    """Abstract base class for exchange data adapters.

    Each adapter is responsible for:
    - Connecting to the exchange (REST + WebSocket)
    - Fetching instruments, option tickers, spot price
    - Reporting health status
    - Tracking aggregate OI and volume
    """

    def __init__(self, exchange_id: str, quality_factor: float):
        self.exchange_id = exchange_id
        self.quality_factor = quality_factor
        self.health = ExchangeHealth()
        self._total_oi: float = 0.0
        self._total_volume: float = 0.0
        self._running: bool = False

    # ── Lifecycle ────────────────────────────────────────────────────

    @abstractmethod
    async def start(self) -> None:
        """Connect WebSocket and start data polling.

        Must set self._running = True and begin background tasks.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect all connections and stop polling.

        Must set self._running = False and cancel background tasks.
        """

    # ── Data fetching ────────────────────────────────────────────────

    @abstractmethod
    async def fetch_instruments(self) -> list[dict]:
        """Fetch list of all active BTC option instruments.

        Returns:
            List of raw instrument dicts in exchange-native format.
        """

    @abstractmethod
    async def fetch_option_tickers(self) -> list[dict]:
        """Fetch current tickers for all BTC options.

        Must include: OI, IV, Greeks, prices, volume.

        Returns:
            List of raw ticker dicts in exchange-native format.
        """

    @abstractmethod
    async def fetch_spot_price(self) -> Optional[float]:
        """Fetch current BTC spot/index price from this exchange.

        Returns:
            Spot price as float, or None if unavailable.
        """

    # ── Health and metrics ───────────────────────────────────────────

    def get_health(self) -> dict:
        """Get current health status dict."""
        self.health.refresh_staleness()
        return self.health.to_dict()

    def get_total_oi(self) -> float:
        """Get total options open interest on this exchange."""
        return self._total_oi

    def get_total_volume(self) -> float:
        """Get total 24h options volume on this exchange."""
        return self._total_volume

    @property
    def is_running(self) -> bool:
        return self._running

    def __repr__(self) -> str:
        return (f"{self.__class__.__name__}("
                f"exchange={self.exchange_id}, "
                f"quality={self.quality_factor}, "
                f"health={self.health.status})")
