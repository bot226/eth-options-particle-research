"""Вычислительный движок: ATM IV, Skew, GEX, Probability, Term Structure."""

import math
import numpy as np
from scipy.stats import norm
from scipy.interpolate import PchipInterpolator
from collections import defaultdict


def _dte_from_expiry_str(expiry_str: str) -> int | None:
    """Преобразует expiry вида 'YYYYMMDD' (или legacy '29NOV24') в DTE (дни до экспирации)."""
    from engine.instrument_normalizer import InstrumentNormalizer
    import datetime
    
    if len(expiry_str) == 8 and expiry_str.isdigit():
        return InstrumentNormalizer.canonical_expiry_to_dte(expiry_str)
        
    months = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    try:
        day = int(expiry_str[:2])
        mon_str = expiry_str[2:5]
        year = int("20" + expiry_str[5:7])
        month = months.get(mon_str.upper())
        if month is None:
            return None
        exp_date = datetime.date(year, month, day)
        dte = (exp_date - datetime.date.today()).days
        return max(dte, 0)
    except Exception:
        return None


class Calculator:
    """Статические методы для вычислений из данных DataManager."""

    @staticmethod
    def get_atm_strike(strikes: list[int], spot: float) -> int | None:
        """Ближайший strike к текущей спот-цене."""
        if not strikes or spot <= 0:
            return None
        return min(strikes, key=lambda s: abs(s - spot))

    @staticmethod
    def get_atm_iv(chain_expiry: dict, spot: float) -> dict:
        """ATM IV для конкретного expiry.
        Возвращает {'call_iv': ..., 'put_iv': ..., 'avg_iv': ...}
        """
        strikes = sorted(chain_expiry.keys())
        atm = Calculator.get_atm_strike(strikes, spot)
        if atm is None or atm not in chain_expiry:
            return {"call_iv": 0, "put_iv": 0, "avg_iv": 0}
        data = chain_expiry[atm]
        c_iv = data.get("C", {}).get("markIv", 0)
        p_iv = data.get("P", {}).get("markIv", 0)
        avg = (c_iv + p_iv) / 2 if c_iv and p_iv else (c_iv or p_iv)
        return {"call_iv": c_iv, "put_iv": p_iv, "avg_iv": avg}

    @staticmethod
    def get_25d_skew(chain_expiry: dict) -> dict:
        """25-Delta Risk Reversal = IV(25d Call) - IV(25d Put).
        Ищем контракты с delta ≈ 0.25 (call) и ≈ -0.25 (put).
        """
        best_call = None
        best_put = None
        best_call_diff = float("inf")
        best_put_diff = float("inf")

        for strike, data in chain_expiry.items():
            c = data.get("C", {})
            p = data.get("P", {})
            if c.get("delta") and c.get("markIv"):
                diff = abs(c["delta"] - 0.25)
                if diff < best_call_diff:
                    best_call_diff = diff
                    best_call = c
            if p.get("delta") and p.get("markIv"):
                diff = abs(p["delta"] + 0.25)  # put delta отрицательный
                if diff < best_put_diff:
                    best_put_diff = diff
                    best_put = p

        call_iv = best_call["markIv"] if best_call else 0
        put_iv = best_put["markIv"] if best_put else 0
        skew = (call_iv - put_iv) * 100 if call_iv and put_iv else 0

        return {
            "call_25d_iv": call_iv,
            "put_25d_iv": put_iv,
            "skew": skew,
        }

    @staticmethod
    def get_iv_term_structure(chain: dict, spot: float) -> dict:
        """IV Term Structure: ATM IV, 25D Call IV, 25D Put IV по каждому expiry."""
        points = []
        for expiry_str, strikes_data in chain.items():
            dte = _dte_from_expiry_str(expiry_str)
            if dte is None or dte < 0:
                continue
            atm_data = Calculator.get_atm_iv(strikes_data, spot)
            skew_data = Calculator.get_25d_skew(strikes_data)
            
            points.append({
                "expiry": expiry_str,
                "dte": dte,
                "atm_iv": atm_data["avg_iv"],
                "call_25d_iv": skew_data.get("call_iv", 0) * 100,  # Ensure %
                "put_25d_iv": skew_data.get("put_iv", 0) * 100,
            })
            
        points.sort(key=lambda x: x["dte"])
        
        # Calculate metrics
        metrics = {
            "regime": "NORMAL",
            "front_premium": 0,
            "slope": 0,
            "event_risk": "Low"
        }
        
        if len(points) >= 2:
            # front_iv: IV of nearest expiry (usually 1D to 7D)
            # far_iv: IV of expiry around 30D+
            front_iv = points[0]["atm_iv"]
            far_iv = points[-1]["atm_iv"]
            for p in points:
                if p["dte"] >= 30:
                    far_iv = p["atm_iv"]
                    break
                    
            slope = far_iv - front_iv
            front_premium = front_iv - far_iv
            metrics["slope"] = slope
            metrics["front_premium"] = front_premium
            
            # Regime Logic
            if slope < -5:
                metrics["regime"] = "PANIC"
                metrics["event_risk"] = "High"
            elif front_iv < 45 and far_iv < 45 and abs(slope) < 5:
                metrics["regime"] = "COMPRESSION"
            elif front_iv > 65 and far_iv > 65:
                metrics["regime"] = "EXPANSION"
                
            if front_premium > 10:
                metrics["event_risk"] = "High"
            elif front_premium > 3:
                metrics["event_risk"] = "Elevated"

        return {"data": points, "metrics": metrics}

    @staticmethod
    def get_oi_by_expiry(chain: dict) -> list[dict]:
        """Агрегированный OI по expiry."""
        result = []
        for expiry_str, strikes_data in chain.items():
            dte = _dte_from_expiry_str(expiry_str)
            if dte is None:
                continue
            calls_oi = 0
            puts_oi = 0
            for strike, data in strikes_data.items():
                calls_oi += data.get("C", {}).get("oi", 0)
                puts_oi += data.get("P", {}).get("oi", 0)
            total = calls_oi + puts_oi
            ratio = puts_oi / calls_oi if calls_oi > 0 else 0
            result.append({
                "expiry": expiry_str,
                "dte": dte,
                "calls_oi": calls_oi,
                "puts_oi": puts_oi,
                "total_oi": total,
                "put_call_ratio": round(ratio, 2),
            })
        result.sort(key=lambda x: x["dte"])
        return result

    @staticmethod
    def get_gamma_exposure_full(chain: dict, spot: float) -> dict:
        """Institutional GEX: агрегация по ВСЕМ expiries с DTE-weighting.

        Crypto-native формула (1 contract = 1 BTC):
            GEX = gamma × OI × contract_size × spot
        Call GEX положительный, Put GEX отрицательный (market maker perspective).

        DTE-weighting: weighted_gex = raw_gex / sqrt(max(dte, 1))
        Near-expiry gamma доминирует.
        """
        contract_size = 1.0
        # Aggregate GEX per strike across all expiries
        strike_agg: dict[int, dict] = {}

        for expiry_str, strikes_data in chain.items():
            dte = _dte_from_expiry_str(expiry_str)
            if dte is None or dte < 0:
                continue
            dte_weight = 1.0 / math.sqrt(max(dte, 1))

            for strike, data in strikes_data.items():
                c = data.get("C", {})
                p = data.get("P", {})
                raw_call = c.get("gamma", 0) * c.get("oi", 0) * contract_size * spot
                raw_put = -p.get("gamma", 0) * p.get("oi", 0) * contract_size * spot

                if strike not in strike_agg:
                    strike_agg[strike] = {"call_gex": 0, "put_gex": 0}
                strike_agg[strike]["call_gex"] += raw_call * dte_weight
                strike_agg[strike]["put_gex"] += raw_put * dte_weight

        # Build sorted points list
        points = []
        total_pos = 0
        total_neg = 0

        for strike in sorted(strike_agg.keys()):
            agg = strike_agg[strike]
            call_gex = agg["call_gex"]
            put_gex = agg["put_gex"]
            net_gex = call_gex + put_gex

            # Near-spot importance: importance = abs(net_gex) / (dist_pct + 0.5)^2
            dist_pct = abs(strike - spot) / spot * 100 if spot > 0 else 100
            importance = abs(net_gex) / ((dist_pct + 0.5) ** 2) if net_gex != 0 else 0

            if net_gex > 0:
                total_pos += net_gex
            else:
                total_neg += net_gex

            points.append({
                "strike": strike,
                "call_gex": round(call_gex, 4),
                "put_gex": round(put_gex, 4),
                "net_gex": round(net_gex, 4),
                "importance": round(importance, 4),
                "dist_pct": round(dist_pct, 2),
            })

        # Cumulative GEX (running sum low→high)
        cumulative = 0
        for p in points:
            cumulative += p["net_gex"]
            p["cumulative_gex"] = round(cumulative, 4)

        total_net = total_pos + total_neg

        # Near-spot GEX (strikes within ±3% of spot)
        near_spot_gex = sum(p["net_gex"] for p in points if p["dist_pct"] <= 3)

        # ---------- Metrics ----------
        metrics = {
            "total_net_gex": round(total_net, 4),
            "total_positive_gex": round(total_pos, 4),
            "total_negative_gex": round(total_neg, 4),
            "near_spot_gex": round(near_spot_gex, 4),
            "gamma_wall_above": 0,
            "gamma_wall_below": 0,
            "gamma_flip": 0,
            "regime": "LOW GAMMA",
            "squeeze_risk": "Low",
            "pinning_prob": "Low",
        }

        if not points:
            return {"data": points, "metrics": metrics}

        above = [p for p in points if p["strike"] > spot]
        below = [p for p in points if p["strike"] <= spot]

        # Gamma Walls — strike with max absolute GEX
        if above:
            wall_a = max(above, key=lambda x: abs(x["net_gex"]))
            metrics["gamma_wall_above"] = wall_a["strike"]
        if below:
            wall_b = max(below, key=lambda x: abs(x["net_gex"]))
            metrics["gamma_wall_below"] = wall_b["strike"]

        # Flip Zone — nearest sign change to spot
        flip_zone = 0
        min_dist = float("inf")
        for i in range(1, len(points)):
            p1, p2 = points[i - 1], points[i]
            if (p1["net_gex"] > 0 > p2["net_gex"]) or (p1["net_gex"] < 0 < p2["net_gex"]):
                denom = p1["net_gex"] - p2["net_gex"]
                if denom != 0:
                    ratio = p1["net_gex"] / denom
                    flip_strike = p1["strike"] + ratio * (p2["strike"] - p1["strike"])
                    dist = abs(flip_strike - spot)
                    if dist < min_dist:
                        min_dist = dist
                        flip_zone = flip_strike
        metrics["gamma_flip"] = round(flip_zone, 0)

        # Regime detection
        # Use max importance to normalize threshold
        max_imp = max((p["importance"] for p in points), default=0)
        if max_imp == 0:
            max_imp = 1

        if near_spot_gex > 0 and abs(near_spot_gex) > abs(total_net) * 0.4:
            metrics["regime"] = "PINNING"
            metrics["pinning_prob"] = "High"
        elif total_net > 0:
            metrics["regime"] = "POSITIVE GAMMA"
            if near_spot_gex > 0:
                metrics["pinning_prob"] = "Medium"
        elif total_net < 0:
            metrics["regime"] = "NEGATIVE GAMMA"
        else:
            metrics["regime"] = "LOW GAMMA"

        # Squeeze risk: negative gamma wall within 3% of spot
        for wall_list, dist_fn in [(below, lambda w: (spot - w["strike"]) / spot),
                                     (above, lambda w: (w["strike"] - spot) / spot)]:
            if wall_list:
                wall = max(wall_list, key=lambda x: abs(x["net_gex"]))
                d = dist_fn(wall)
                if wall["net_gex"] < 0 and d < 0.03:
                    metrics["squeeze_risk"] = "High"
                elif wall["net_gex"] < 0 and d < 0.06:
                    if metrics["squeeze_risk"] != "High":
                        metrics["squeeze_risk"] = "Medium"

        return {"data": points, "metrics": metrics}

    @staticmethod
    def get_major_oi_levels(chain_expiry: dict, top_n: int = 5) -> list[dict]:
        """Top-N strikes по суммарному OI."""
        levels = []
        for strike, data in chain_expiry.items():
            total_oi = data.get("C", {}).get("oi", 0) + data.get("P", {}).get("oi", 0)
            call_oi = data.get("C", {}).get("oi", 0)
            put_oi = data.get("P", {}).get("oi", 0)
            levels.append({
                "strike": strike,
                "total_oi": total_oi,
                "call_oi": call_oi,
                "put_oi": put_oi,
            })
        levels.sort(key=lambda x: x["total_oi"], reverse=True)
        return levels[:top_n]

    @staticmethod
    def probability_distribution(chain_expiry: dict, spot: float, dte: int) -> dict:
        """Эмпирическое распределение вероятностей из Дельт опционов (Risk-Neutral PDF).
        Учитывает skew и fat tails реального рынка.
        """
        strikes_data = sorted(chain_expiry.keys())
        if len(strikes_data) < 3 or spot <= 0 or dte <= 0:
            return {"x": [], "pdf": [], "metrics": {}}

        k_list = []
        cdf_list = []
        
        for k in strikes_data:
            data = chain_expiry[k]
            c_delta = data.get("C", {}).get("delta")
            p_delta = data.get("P", {}).get("delta")
            
            # P(X > K) = Call Delta. CDF(K) = P(X <= K) = 1 - Call Delta
            # Or CDF(K) = - Put Delta
            cdf_vals = []
            if c_delta is not None and 0 < c_delta < 1:
                cdf_vals.append(1 - c_delta)
            if p_delta is not None and -1 < p_delta < 0:
                cdf_vals.append(-p_delta)
                
            if cdf_vals:
                k_list.append(k)
                cdf_list.append(float(np.mean(cdf_vals)))

        # Fallback на Black-Scholes, если нет дельт
        if len(k_list) < 5:
            atm = Calculator.get_atm_strike(strikes_data, spot)
            atm_data = chain_expiry.get(atm, {})
            c_iv = atm_data.get("C", {}).get("markIv", 0)
            p_iv = atm_data.get("P", {}).get("markIv", 0)
            sigma = (c_iv + p_iv) / 2 if c_iv and p_iv else (c_iv or p_iv)
            if sigma <= 0:
                return {"x": [], "pdf": [], "metrics": {}}
            t = dte / 365.0
            sqrt_t = math.sqrt(t)
            x = np.linspace(spot * 0.7, spot * 1.3, 200)
            d = (np.log(x / spot) + 0.5 * sigma ** 2 * t) / (sigma * sqrt_t)
            pdf = norm.pdf(d) / (x * sigma * sqrt_t)
            return {"x": x.tolist(), "pdf": pdf.tolist(), "metrics": {"bias": "Neutral", "tail_risk": "Low", "peak": spot}}

        # Убираем дубликаты CDF для PCHIP
        k_clean = [k_list[0]]
        cdf_clean = [cdf_list[0]]
        for i in range(1, len(k_list)):
            if k_list[i] > k_clean[-1] and cdf_list[i] >= cdf_clean[-1]:
                if cdf_list[i] == cdf_clean[-1]:
                    # Принудительная строгая монотонность
                    cdf_clean.append(cdf_clean[-1] + 1e-6)
                else:
                    cdf_clean.append(cdf_list[i])
                k_clean.append(k_list[i])

        if len(k_clean) < 5:
            return {"x": [], "pdf": [], "metrics": {}}

        # PCHIP Interpolation
        interp = PchipInterpolator(k_clean, cdf_clean)
        
        # Расширяем область видимости x
        min_k = min(k_clean) * 0.9
        max_k = max(k_clean) * 1.1
        x = np.linspace(min_k, max_k, 200)
        
        # PDF is derivative of CDF
        pdf = interp.derivative()(x)
        # Очистка артефактов
        pdf = np.maximum(pdf, 0)
        
        # Metrics
        peak_idx = np.argmax(pdf)
        peak_price = x[peak_idx]
        
        bias = "Neutral"
        if peak_price > spot * 1.02:
            bias = "Bullish"
        elif peak_price < spot * 0.98:
            bias = "Bearish"
            
        # Tail risk: probability mass > 1.15 spot and < 0.85 spot
        left_tail_mass = np.sum(pdf[x < spot * 0.85]) * (x[1] - x[0])
        right_tail_mass = np.sum(pdf[x > spot * 1.15]) * (x[1] - x[0])
        
        tail_risk = "Low"
        if left_tail_mass > 0.1 or right_tail_mass > 0.1:
            tail_risk = "Elevated"
        if left_tail_mass > 0.2 or right_tail_mass > 0.2:
            tail_risk = "High"
            
        metrics = {
            "bias": bias,
            "tail_risk": tail_risk,
            "peak": peak_price,
            "left_tail": left_tail_mass,
            "right_tail": right_tail_mass
        }

        return {"x": x.tolist(), "pdf": pdf.tolist(), "metrics": metrics}

    @staticmethod
    def get_heatmap_data(chain_expiry: dict, spot: float, dm_instance=None) -> list[dict]:
        """Данные для тепловой карты: strike + OI/Vol/IV для calls и puts + Gamma & Deltas."""
        rows = []
        for strike in sorted(chain_expiry.keys()):
            data = chain_expiry[strike]
            c = data.get("C", {})
            p = data.get("P", {})
            
            c_sym = c.get("symbol", "")
            p_sym = p.get("symbol", "")
            
            c_oi_delta = dm_instance.get_oi_delta_pct(c_sym, 24) if dm_instance and c_sym else 0.0
            p_oi_delta = dm_instance.get_oi_delta_pct(p_sym, 24) if dm_instance and p_sym else 0.0
            
            # Объем Bybit уже является 24h volume. Чтобы показать дельту объема,
            # мы могли бы также логировать volume. Для текущей задачи мы оставим volume_delta 0
            # или можем симулировать всплеск, если volume > средний.
            
            rows.append({
                "strike": strike,
                "is_atm": abs(strike - spot) == min(
                    abs(s - spot) for s in chain_expiry.keys()
                ) if spot > 0 else False,
                "distance_from_spot_pct": ((strike - spot) / spot * 100) if spot > 0 else 0,
                "calls_oi": c.get("oi", 0),
                "calls_oi_delta": c_oi_delta,
                "calls_vol": c.get("volume", 0),
                "calls_iv": c.get("markIv", 0),
                "calls_gamma": c.get("gamma", 0),
                "puts_oi": p.get("oi", 0),
                "puts_oi_delta": p_oi_delta,
                "puts_vol": p.get("volume", 0),
                "puts_iv": p.get("markIv", 0),
                "puts_gamma": p.get("gamma", 0),
            })
        return rows

    @staticmethod
    def get_heatmap_metadata(heatmap_data: list[dict]) -> dict:
        """Анализирует данные heatmap и находит стены, кластеры и общий bias."""
        if not heatmap_data:
            return {}
            
        max_call_oi = max((r["calls_oi"] for r in heatmap_data), default=0)
        max_put_oi = max((r["puts_oi"] for r in heatmap_data), default=0)
        
        call_wall_strike = None
        put_wall_strike = None
        
        for r in heatmap_data:
            if r["calls_oi"] == max_call_oi and max_call_oi > 0:
                call_wall_strike = r["strike"]
            if r["puts_oi"] == max_put_oi and max_put_oi > 0:
                put_wall_strike = r["strike"]
                
        total_call_vol = sum(r["calls_vol"] for r in heatmap_data)
        total_put_vol = sum(r["puts_vol"] for r in heatmap_data)
        
        bias = "Neutral"
        if total_call_vol > total_put_vol * 1.5:
            bias = "Bullish"
        elif total_put_vol > total_call_vol * 1.5:
            bias = "Bearish"
            
        return {
            "call_wall": call_wall_strike,
            "put_wall": put_wall_strike,
            "bias": bias,
            "max_call_oi": max_call_oi,
            "max_put_oi": max_put_oi
        }
