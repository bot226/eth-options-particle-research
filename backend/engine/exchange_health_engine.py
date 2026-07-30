"""ExchangeHealthEngine — Exchange health monitoring and status aggregation.

Tracks per-exchange health: ONLINE | DEGRADED | DELAYED | OFFLINE.
Provides aggregated data quality assessment.
"""

import logging
from typing import Dict, Any

log = logging.getLogger(__name__)


class ExchangeHealthEngine:
    """Aggregates and reports exchange health status."""

    @staticmethod
    def calculate(
        exchange_health: Dict[str, dict],
        multi_exchange_enabled: bool = False,
    ) -> Dict[str, Any]:
        """Build health status report.

        Args:
            exchange_health: {exchange_id: {status, latency_ms, last_update, ...}}
            multi_exchange_enabled: whether multi-exchange mode is active

        Returns:
            Standardized health report dict (always present in API response).
        """
        if not multi_exchange_enabled or not exchange_health:
            # Single-exchange mode — return default Bybit-only health
            return {
                "mode": "single_exchange",
                "primary_source": "bybit",
                "source_count": 1,
                "active_sources": 1,
                "total_sources": 1,
                "exchanges": {
                    "bybit": {
                        "status": "ONLINE",
                        "latency_ms": 0,
                        "last_update": 0,
                        "stale_seconds": 0,
                        "ws_connected": True,
                        "error": "",
                    }
                },
                "data_quality": "GOOD",
            }

        total = len(exchange_health)
        active = 0
        primary_source = None
        worst_status = "ONLINE"

        status_priority = {"ONLINE": 0, "DEGRADED": 1, "DELAYED": 2, "OFFLINE": 3}

        for ex_id, health in exchange_health.items():
            status = health.get("status", "OFFLINE")
            if status != "OFFLINE":
                active += 1
                if primary_source is None:
                    primary_source = ex_id

            # Track worst status
            if status_priority.get(status, 3) > status_priority.get(worst_status, 0):
                worst_status = status

        # Determine overall data quality
        if active == 0:
            data_quality = "CRITICAL"
        elif active < total:
            data_quality = "DEGRADED"
        elif worst_status in ("DEGRADED", "DELAYED"):
            data_quality = "DEGRADED"
        else:
            data_quality = "GOOD"

        # Primary source: prefer deribit if available, then bybit
        for preferred in ["deribit", "bybit"]:
            if preferred in exchange_health:
                h = exchange_health[preferred]
                if h.get("status", "OFFLINE") != "OFFLINE":
                    primary_source = preferred
                    break

        return {
            "mode": "multi_exchange",
            "primary_source": primary_source or "none",
            "source_count": active,
            "active_sources": active,
            "total_sources": total,
            "exchanges": exchange_health,
            "data_quality": data_quality,
        }
