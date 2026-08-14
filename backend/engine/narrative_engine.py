"""
Narrative Engine — Cross-engine institutional intelligence synthesis.

Generates human-readable market intelligence summaries by combining
outputs from ALL engines into a unified narrative.

This engine does NOT calculate metrics.
It ONLY interprets and translates engine outputs into
institutional desk commentary.
"""

from typing import Dict, Any, List


# ── Label Translation Maps ──────────────────────────────────────────

GAMMA_REGIME_LABELS = {
    "POSITIVE_GAMMA": "Дилеры подавляют волатильность",
    "NEGATIVE_GAMMA": "Дилеры усиливают волатильность",
    "LOW_GAMMA": "Низкая гамма-активность",
}

DEALER_POSITIONING_LABELS = {
    "STABILIZING": "Хеджирование дилеров стабилизирует цену",
    "DESTABILIZING": "Хеджирование дилеров ускоряет движение",
    "NEUTRAL": "Позиционирование дилеров нейтральное",
}

PINNING_LABELS = {
    "HIGH": "Активный сильный пиннинг страйка",
    "MEDIUM": "Умеренное давление пиннинга",
    "LOW": "Слабое влияние пиннинга",
}

IV_REGIME_LABELS = {
    "COMPRESSION": "Подавление волатильности (компрессия)",
    "NORMAL": "Волатильность на нормальном уровне",
    "ELEVATED": "Повышенная волатильность",
    "VOL EXPANSION": "Расширение волатильности",
    "EXPANSION": "Расширение волатильности",
    "PANIC": "Режим паники (рыночный стресс)",
}

TERM_STRUCTURE_LABELS = {
    "CONTANGO": "Контанго временной структуры",
    "BACKWARDATION": "Бэквордация (инверсия временной структуры — краткосрочный стресс)",
    "FLAT": "Плоская временная структура",
}

SKEW_REGIME_LABELS = {
    "BALANCED": "Сбалансированный skew",
    "CALL_SKEW": "Повышенный спрос на хеджирование роста (Call-skew)",
    "PUT_SKEW": "Повышенный спрос на защиту от падения (Put-skew)",
}

SKEW_DOMINANCE_LABELS = {
    "NONE": "Направленное давление потоков отсутствует",
    "CALL_DOMINATED": "Преобладает поток на покупку Call-опционов",
    "PUT_DOMINATED": "Преобладает поток на покупку Put-опционов",
}

FLOW_BIAS_LABELS = {
    "BULLISH": "Обнаружено бычье давление потоков",
    "BEARISH": "Обнаружено медвежье давление потоков",
    "NEUTRAL": "Давление потоков сбалансировано",
}

MARKET_CONTROL_LABELS = {
    "DEALER_CONTROLLED": "Рынок под контролем дилеров",
    "PARTICIPANT_CONTROLLED": "Рынком движут активные участники",
    "NEUTRAL": "Контроль над рынком сбалансирован",
}

FRAGILITY_LABELS = {
    "ROBUST": "Рыночная структура устойчива",
    "FRAGILE": "Рыночная структура хрупкая",
    "STABLE": "Рыночная структура стабильная",
}

SCENARIO_LABELS = {
    "PINNED_TO_STRIKE": "Низковолатильный пиннинг",
    "VOLATILE_RANGE": "Повышенная внутридиапазонная волатильность",
    "SQUEEZE_RISK_ELEVATED": "Растущий риск сквиза",
    "NEUTRAL_RANGE": "Нейтральный диапазон",
}

EXECUTION_QUALITY_LABELS = {
    "HIGH": "Благоприятные условия для исполнения",
    "MODERATE": "Нейтральные условия для исполнения",
    "LOW": "Неблагоприятные (враждебные) условия для исполнения",
}

BIAS_LABELS = {
    "NEUTRAL": "Направленное смещение отсутствует",
    "BULLISH": "Бычье смещение рынка",
    "BEARISH": "Медвежье смещение рынка",
    "TRENDING": "Обнаружены признаки сильного тренда",
}

TREND_MATURITY_LABELS = {
    "DEVELOPING": "Формирующийся тренд",
    "MATURE": "Зрелый тренд",
    "EXHAUSTING": "Сигналы истощения тренда",
}


class NarrativeEngine:
    """
    Synthesizes all engine outputs into institutional narrative.
    Pure interpreter — no metric calculations.
    """

    @staticmethod
    def generate(gamma: dict, vol: dict, skew: dict, liq: dict,
                 flow: dict, meta: dict, scenario: dict,
                 execution: dict, spot: float,
                 divergences: list = None, leadership: dict = None,
                 advanced_intelligence: dict = None) -> Dict[str, Any]:

        result = {
            "status": "ok",
            "intelligence_summary": [],    # 1-4 key sentences
            "market_narrative": "",         # full paragraph
            "labels": {},                   # translated labels for all engines
            "five_answers": {},             # instant answers to 5 key questions
        }

        try:
            # ── Extract signals ──
            g_sig = gamma.get("signals", {})
            g_met = gamma.get("metrics", {})
            v_sig = vol.get("signals", {})
            v_met = vol.get("metrics", {})
            s_sig = skew.get("signals", {})
            l_sig = liq.get("signals", {})
            l_met = liq.get("metrics", {})
            f_sig = flow.get("signals", {})
            f_met = flow.get("metrics", {})
            m_sig = meta.get("signals", {})
            m_met = meta.get("metrics", {})
            sc_sig = scenario.get("signals", {})
            ex_sig = execution.get("signals", {})
            ex_met = execution.get("metrics", {})

            # ── Translated Labels ──
            result["labels"] = {
                "gamma_regime": GAMMA_REGIME_LABELS.get(g_sig.get("gamma_regime", ""), g_sig.get("gamma_regime", "")),
                "dealer_positioning": DEALER_POSITIONING_LABELS.get(g_sig.get("dealer_positioning", ""), g_sig.get("dealer_positioning", "")),
                "pinning_bias": PINNING_LABELS.get(g_sig.get("pinning_bias", ""), g_sig.get("pinning_bias", "")),
                "iv_regime": IV_REGIME_LABELS.get(v_sig.get("iv_regime", ""), v_sig.get("iv_regime", "")),
                "term_structure": TERM_STRUCTURE_LABELS.get(v_sig.get("term_structure_label", ""), v_sig.get("term_structure_label", "")),
                "skew_regime": SKEW_REGIME_LABELS.get(s_sig.get("skew_regime", ""), s_sig.get("skew_regime", "")),
                "skew_dominance": SKEW_DOMINANCE_LABELS.get(s_sig.get("skew_dominance", ""), s_sig.get("skew_dominance", "")),
                "flow_bias": FLOW_BIAS_LABELS.get(f_sig.get("flow_bias", ""), f_sig.get("flow_bias", "")),
                "market_control": MARKET_CONTROL_LABELS.get(m_sig.get("market_control", ""), m_sig.get("market_control", "")),
                "market_fragility": FRAGILITY_LABELS.get(m_sig.get("market_fragility", ""), m_sig.get("market_fragility", "")),
                "current_scenario": SCENARIO_LABELS.get(sc_sig.get("current_scenario", ""), sc_sig.get("current_scenario", "")),
                "execution_quality": EXECUTION_QUALITY_LABELS.get(ex_sig.get("execution_quality", ""), ex_sig.get("execution_quality", "")),
                "directional_bias": BIAS_LABELS.get(ex_sig.get("directional_bias", ""), ex_sig.get("directional_bias", "")),
                "trend_maturity": TREND_MATURITY_LABELS.get(m_sig.get("trend_maturity", ""), m_sig.get("trend_maturity", "")),
            }

            # ── Intelligence Summary (cross-engine synthesis) ──
            summary_lines = []

            gamma_regime = g_sig.get("gamma_regime", "")
            iv_regime = v_sig.get("iv_regime", "")
            pinning = g_sig.get("pinning_bias", "")
            flow_bias = f_sig.get("flow_bias", "")
            fragility = m_sig.get("market_fragility", "")
            control = m_sig.get("market_control", "")
            squeeze_risk = sc_sig.get("squeeze_risk", "LOW")
            breakout_risk = sc_sig.get("breakout_risk", "LOW")
            iv_velocity = v_met.get("iv_velocity", 0)
            call_wall = g_met.get("call_wall", 0)
            put_wall = g_met.get("put_wall", 0)
            expansion_risk = v_sig.get("volatility_expansion", "LOW")

            # Rule 1: Dealer control + compression + pinning
            if gamma_regime == "POSITIVE_GAMMA" and iv_regime == "COMPRESSION" and pinning == "HIGH":
                wall_str = ""
                if call_wall and put_wall:
                    wall_str = f" между ${_fmt_k(put_wall)}–${_fmt_k(call_wall)}"
                summary_lines.append(f"Дилеры подавляют волатильность вблизи концентрации ключевых страйков{wall_str}.")
                summary_lines.append("Вероятность прорыва на данный момент низкая.")

            # Rule 2: Positive gamma, stable
            elif gamma_regime == "POSITIVE_GAMMA" and iv_regime in ["COMPRESSION", "NORMAL"]:
                summary_lines.append("Дилеры сохраняют контроль над рынком. Положительная гамма сглаживает колебания цен.")
                if pinning in ["HIGH", "MEDIUM"]:
                    summary_lines.append(f"Цена ETH удерживается (пиннинг) вблизи крупной концентрации открытого интереса.")

            # Rule 3: Negative gamma + expansion
            elif gamma_regime == "NEGATIVE_GAMMA" and iv_regime in ["EXPANSION", "VOL EXPANSION", "ELEVATED"]:
                summary_lines.append("Нестабильность рынка увеличивается. Риск расширения волатильности растет.")
                if flow_bias == "BULLISH":
                    summary_lines.append("Бычий поток в зоне отрицательной гаммы — волатильность может вырасти из-за сквиза.")
                elif flow_bias == "BEARISH":
                    summary_lines.append("Медвежий поток в зоне отрицательной гаммы — повышенный риск каскадных ликвидаций.")

            # Rule 4: Compression weakening
            elif iv_regime == "COMPRESSION" and iv_velocity > 0:
                summary_lines.append("Сжатие волатильности ослабевает. Повышается вероятность расширения IV.")
                if squeeze_risk in ["MEDIUM", "HIGH"]:
                    summary_lines.append("Вероятность сквиза возрастает.")

            # Rule 5: Panic
            elif iv_regime == "PANIC":
                summary_lines.append("Активен режим PANIC. Экстремальные условия волатильности.")
                summary_lines.append("Качество исполнения сделок снижено. Рекомендуется соблюдать осторожность.")

            # Fallback: general market state
            if not summary_lines:
                summary_lines.append(result["labels"]["market_control"] + ".")
                summary_lines.append(result["labels"]["iv_regime"] + ".")
                if expansion_risk in ["HIGH", "ELEVATED"]:
                    summary_lines.append("Повышенный риск расширения волатильности.")

            # Add flow context if meaningful
            if flow_bias != "NEUTRAL" and len(summary_lines) < 4:
                summary_lines.append(result["labels"]["flow_bias"] + ".")

            # Add execution context
            if ex_sig.get("execution_quality") == "LOW" and len(summary_lines) < 4:
                summary_lines.append("Агрессивная среда для исполнения сделок — рекомендуется расширить стопы.")

            # Rule 6: Cross-exchange divergence
            if divergences:
                high_divs = [d for d in divergences if d.get("severity") == "HIGH"]
                if high_divs:
                    div = high_divs[0]
                    div_type = "IV" if div.get("type") == "iv_divergence" else "Skew"
                    summary_lines.append(f"Внимание: обнаружена сильная дивергенция ({div_type}) между биржами.")
                elif len(summary_lines) < 4:
                    summary_lines.append("Наблюдаются локальные расхождения между биржами (низкая/средняя дивергенция).")

            # Rule 7: Market Leadership
            if leadership and leadership.get("leadership_confidence", 0) > 50:
                iv_leader = leadership.get("iv_leader", "none")
                if iv_leader != "none" and len(summary_lines) < 4:
                    summary_lines.append(f"Биржа {iv_leader.capitalize()} в данный момент выступает лидером ценообразования волатильности.")

            # Rule 8: Advanced Intelligence (Causal Narratives)
            if advanced_intelligence and advanced_intelligence.get("status") == "experimental":
                adv_lines = []
                p1 = advanced_intelligence.get("phase_1", {})
                p2 = advanced_intelligence.get("phase_2", {})
                
                rt = p1.get("regime_transition", {}).get("metrics", {})
                gs = p1.get("gamma_surface", {}).get("features", {})
                lv = p1.get("liquidity_voids", {}).get("features", {})
                dh = p1.get("dealer_hedging", {}).get("metrics", {})
                
                ex = p2.get("execution_timing", {}).get("features", {})
                bt = p2.get("breakout_timing", {}).get("features", {})
                so = p2.get("synthetic_orderflow", {}).get("features", {})
                
                execution_state = ex.get("execution_state", "WAIT")
                execution_context = ex.get("execution_context", "")
                
                if execution_state != "WAIT":
                    adv_lines.append(f"[{execution_state}]: {execution_context}")
                
                expansion_prob = rt.get("expansion_probability", 0)
                if expansion_prob > 50:
                    reasons = []
                    
                    if rt.get("regime_stability_decay", 0) > 50:
                        reasons.append("ослабления поддержки дилеров")
                    
                    if dh.get("hedge_acceleration_risk") in ["HIGH", "MEDIUM"]:
                        reasons.append("высокого риска ускорения хеджирования")
                        
                    true_voids = lv.get("true_voids", [])
                    if true_voids:
                        void = true_voids[0]
                        reasons.append(f"наличия пустот ликвидности (от ${_fmt_k(void['start'])})")
                        
                    if so.get("structural_flow_imbalance") in ["STRONG_BUY_IMBALANCE", "STRONG_SELL_IMBALANCE"]:
                        reasons.append("агрессивного синтетического потока ордеров")
                        
                    if reasons:
                        reason_str = ", ".join(reasons)
                        adv_lines.append(f"Риск пробоя повышен ({expansion_prob}%) из-за: {reason_str}.")
                
                elif rt.get("compression_failure_risk", 0) > 50:
                    adv_lines.append(f"Сжатие нестабильно. {bt.get('estimated_breakout_window', '')}.")
                    
                # Replace summary lines with advanced causal logic if available
                if adv_lines:
                    # Keep at most 2 basic lines, then add advanced lines
                    summary_lines = summary_lines[:2] + adv_lines

            result["intelligence_summary"] = summary_lines[:4]
            result["market_narrative"] = " ".join(summary_lines[:4])

            # ── Five Instant Answers ──
            result["five_answers"] = {
                "who_controls": result["labels"]["market_control"],
                "volatility_state": result["labels"]["iv_regime"],
                "can_escape": "Маловероятно" if pinning == "HIGH" and squeeze_risk == "LOW" else
                              "Возможно" if squeeze_risk in ["MEDIUM", "HIGH"] else "Нейтрально",
                "stability": result["labels"]["market_fragility"],
                "execution": result["labels"]["execution_quality"],
                "data_integrity": "Согласовано" if not divergences else "Дивергенция",
            }

        except Exception as e:
            result["status"] = "error"
            result["intelligence_summary"] = ["Синтез рыночной информации недоступен."]
            result["market_narrative"] = "Синтез рыночной информации недоступен."

        return result


def _fmt_k(n):
    """Format number to K notation."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}K"
    return str(int(n))
