"""Движок сигналов — эвристические торговые сигналы."""

from engine.calculator import Calculator


class Signal:
    """Один сигнал."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"

    def __init__(self, name: str, description: str, bias: str, icon: str = ""):
        self.name = name
        self.description = description
        self.bias = bias
        self.icon = icon

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "bias": self.bias,
            "icon": self.icon,
        }


class SignalEngine:
    """Генерирует сигналы на основе текущих данных."""

    @staticmethod
    def generate(chain: dict, spot: float, nearest_expiry: str | None) -> list[dict]:
        signals = []
        if not chain or not nearest_expiry or spot <= 0:
            return signals

        expiry_data = chain.get(nearest_expiry, {})
        if not expiry_data:
            return signals

        signal_objs = []

        # 1. Call/Put Buildup — анализ распределения OI
        total_call_oi = sum(d.get("C", {}).get("oi", 0) for d in expiry_data.values())
        total_put_oi = sum(d.get("P", {}).get("oi", 0) for d in expiry_data.values())

        # OI выше спота
        above_call_oi = sum(
            d.get("C", {}).get("oi", 0)
            for s, d in expiry_data.items() if s > spot
        )

        if total_call_oi > 0 and above_call_oi / total_call_oi > 0.5:
            signal_objs.append(Signal(
                "CALL BUILDUP",
                f"OI на CALL выше спота: {above_call_oi:.0f} ({above_call_oi/total_call_oi*100:.0f}%)",
                Signal.BULLISH, "📈"
            ))

        # 2. IV уровень ATM
        atm_iv = Calculator.get_atm_iv(expiry_data, spot)
        if atm_iv["avg_iv"] > 0:
            iv_pct = atm_iv["avg_iv"] * 100
            if iv_pct > 70:
                signal_objs.append(Signal(
                    "IV EXPANSION",
                    f"ATM IV = {iv_pct:.0f}% — ожидание сильного движения",
                    Signal.NEUTRAL, "⚡"
                ))
            elif iv_pct < 30:
                signal_objs.append(Signal(
                    "IV COMPRESSION",
                    f"ATM IV = {iv_pct:.0f}% — низкая волатильность",
                    Signal.NEUTRAL, "😴"
                ))

        # 3. Skew
        skew_data = Calculator.get_25d_skew(expiry_data)
        skew = skew_data["skew"]
        if skew > 5:
            signal_objs.append(Signal(
                "BULLISH SKEW",
                f"25D Skew: +{skew:.1f}% — рынок смещается к росту",
                Signal.BULLISH, "🐂"
            ))
        elif skew < -5:
            signal_objs.append(Signal(
                "BEARISH SKEW",
                f"25D Skew: {skew:.1f}% — рынок ожидает снижение",
                Signal.BEARISH, "🐻"
            ))

        # 4. Gamma risk
        gex_result = Calculator.get_gamma_exposure_full(chain, spot)
        gex_data = gex_result.get("data", [])
        gex_metrics = gex_result.get("metrics", {})
        above_gex = [g for g in gex_data if g["strike"] > spot and abs(g["net_gex"]) > 0]
        if above_gex:
            max_gex = max(above_gex, key=lambda g: abs(g["net_gex"]))
            if max_gex["net_gex"] > 0:
                signal_objs.append(Signal(
                    f"GAMMA RISK ABOVE {max_gex['strike']//1000}K",
                    f"Высокая Gamma и OI выше {max_gex['strike']//1000}K — риск ускорения",
                    Signal.BULLISH, "⚠️"
                ))

        # 5. Put/Call Ratio
        if total_call_oi > 0:
            pc_ratio = total_put_oi / total_call_oi
            if pc_ratio < 0.7:
                signal_objs.append(Signal(
                    "LOW PUT/CALL",
                    f"Put/Call = {pc_ratio:.2f} — бычий сентимент",
                    Signal.BULLISH, "✅"
                ))
            elif pc_ratio > 1.3:
                signal_objs.append(Signal(
                    "HIGH PUT/CALL",
                    f"Put/Call = {pc_ratio:.2f} — медвежий сентимент",
                    Signal.BEARISH, "🔴"
                ))

        # 6. Market Bias
        bullish_count = sum(1 for s in signal_objs if s.bias == Signal.BULLISH)
        bearish_count = sum(1 for s in signal_objs if s.bias == Signal.BEARISH)
        if bullish_count > bearish_count:
            bias = "BULLISH"
            bias_type = Signal.BULLISH
        elif bearish_count > bullish_count:
            bias = "BEARISH"
            bias_type = Signal.BEARISH
        else:
            bias = "NEUTRAL"
            bias_type = Signal.NEUTRAL

        signal_objs.append(Signal(
            f"MARKET BIAS: {bias}",
            f"Bullish: {bullish_count}, Bearish: {bearish_count}",
            bias_type, "🎯"
        ))

        return [s.to_dict() for s in signal_objs]
