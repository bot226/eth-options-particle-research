import re

def main():
    with open('backend/engine/manual_setup_classifier.py', 'r', encoding='utf-8') as f:
        content = f.read()

    new_classify = """    @classmethod
    def classify(cls, mos: Dict[str, Any]) -> Dict[str, Any]:
        fields = cls.source_fields(mos or {})

        status = "WATCH"
        setup_type = "NONE"
        bias = "NEUTRAL"
        confidence = "LOW"
        reason = "Контекст MOS неполный или нейтральный — ждать более чёткого подтверждения на графике."
        confirmation_needed = "Ждать подтверждения цены на графике перед рассмотрением ручной сделки."
        invalidation_condition = "Нет активного рабочего сетапа."
        invalidation_level = None
        avoid_reason = None
        setup_quality = "INCOMPLETE"
        missing_conditions = []
        ladder = []

        current_state = fields["current_state"]
        execution_state = fields["execution_timing_state"]
        event_type = fields["event_type"]
        gamma_slope = fields["gamma_slope_state"]
        cluster_score = fields["signal_cluster_score"]
        expansion_probability = fields["expansion_probability"]
        price = fields["price"]

        # ── Live level context (from _build_live_level_context) ──────────────
        live_ctx = mos.get("_live_level_ctx") or {}
        live_support_level = live_ctx.get("live_support_level")
        live_support_result = live_ctx.get("live_support_result")
        live_support_type = live_ctx.get("live_support_type")
        live_support_dist = live_ctx.get("live_support_distance_pct")
        live_support_source = live_ctx.get("live_support_source")
        
        live_resistance_level = live_ctx.get("live_resistance_level")
        live_resistance_result = live_ctx.get("live_resistance_result")
        live_resistance_type = live_ctx.get("live_resistance_type")
        live_resistance_dist = live_ctx.get("live_resistance_distance_pct")
        live_resistance_source = live_ctx.get("live_resistance_source")
        
        primary_live_level = live_ctx.get("primary_live_level")
        primary_live_side = live_ctx.get("primary_live_side")
        primary_live_result = live_ctx.get("primary_live_result")
        live_context_used = bool(live_ctx.get("live_context_used", False))
        
        # Default compat fields
        nearest_level = live_ctx.get("live_nearest_level") or fields.get("nearest_level")
        level_result = live_ctx.get("live_level_result") or fields.get("level_result")
        level_side = live_ctx.get("live_level_side") or fields.get("level_side")

        # ── Flow direction: prefer explicit field, fallback to synthetic_flow_pressure ──
        flow_direction = fields.get("short_term_flow_direction", "")
        raw_sfp = (
            mos.get("synthetic_flow_pressure")
            or mos.get("_sfp")
            or fields.get("_sfp")
        )
        _SFP_THRESHOLD = 15.0
        if raw_sfp is not None and (
            not flow_direction
            or flow_direction.upper() in ("", "NEUTRAL", "NONE", "UNKNOWN")
        ):
            if raw_sfp > _SFP_THRESHOLD:
                flow_direction = "BULLISH"
            elif raw_sfp < -_SFP_THRESHOLD:
                flow_direction = "BEARISH"
            else:
                flow_direction = "NEUTRAL"

        has_execution_window = execution_state in ("EXECUTION_WINDOW_OPEN", "EXECUTION_WINDOW", "EXPANSION_CONFIRMING")
        structure_unstable = execution_state == "STRUCTURE_UNSTABLE"
        expansion_ready = expansion_probability >= 50
        cluster_ready = cluster_score >= 55
        strong_cluster = cluster_score >= 70
        weak_cluster = cluster_score < 35

        # ── Replay context guard (SEPARATE from live context guard) ──────────
        replay_context_stale = bool(mos.get("_level_context_stale", True))
        level_context_meta = mos.get("_level_context_meta", {})

        no_actionable_event = not event_type
        bullish_flow = flow_direction in ("BULLISH", "UP", "LONG", "BUYING", "POSITIVE")
        bearish_flow = flow_direction in ("BEARISH", "DOWN", "SHORT", "SELLING", "NEGATIVE")
        gamma_weak = gamma_slope in ("weakening", "collapsing", "negative")

        # States that represent an active/trending market environment
        active_current_state = current_state not in (
            None, "", "UNKNOWN", "NEUTRAL_BALANCE", "INITIALIZING", "TRANSITION"
        )
        active_execution_state = execution_state not in (
            None, "", "UNKNOWN", "WAIT", "STRUCTURE_UNSTABLE", "STRUCTURE_UNKNOWN"
        )

        support_defended = live_support_result in (
            "SUPPORT_DEFENSE", "DEFENDED", "BOUNCE", "RECLAIM", "HOLD",
            "NEAR_SUPPORT", "AT_SUPPORT"
        )
        near_support_only = live_support_result == "NEAR_SUPPORT"
        resistance_rejected = live_resistance_result in (
            "RESISTANCE_REJECTION", "REJECTED", "REJECTION", "FAIL", "HOLD",
            "NEAR_RESISTANCE", "AT_RESISTANCE"
        )
        near_resistance_only = live_resistance_result == "NEAR_RESISTANCE"
        
        downside_false_break_reclaim = live_support_result in (
            "FALSE_BREAK_DOWNSIDE_RECLAIM", "FALSE_BREAK_RECLAIM", "DOWNSIDE_RECLAIM", "RECLAIM",
        )
        
        support_broken = live_support_result in ("BREAK", "BROKEN", "BREAKOUT", "BREAKDOWN")
        resistance_broken = live_resistance_result in ("BREAK", "BROKEN", "BREAKOUT", "BREAKDOWN")

        valid_support = live_support_level is not None and live_support_level > 0
        valid_resistance = live_resistance_level is not None and live_resistance_level > 0

        long_reversal_confirmed = valid_support and bullish_flow and (
            support_defended or downside_false_break_reclaim
        )
        short_reversal_confirmed = valid_resistance and bearish_flow and resistance_rejected

        # Selection state
        sel_level = None
        sel_side = None
        sel_source = None
        sel_dist = None
        sel_result = None
        sel_type = None

        def _set_selection(side: str):
            nonlocal sel_level, sel_side, sel_source, sel_dist, sel_result, sel_type
            if side == "SUPPORT" and valid_support:
                sel_level = live_support_level
                sel_side = "SUPPORT"
                sel_source = live_support_source
                sel_dist = live_support_dist
                sel_result = live_support_result
                sel_type = live_support_type
            elif side == "RESISTANCE" and valid_resistance:
                sel_level = live_resistance_level
                sel_side = "RESISTANCE"
                sel_source = live_resistance_source
                sel_dist = live_resistance_dist
                sel_result = live_resistance_result
                sel_type = live_resistance_type

        # --- UNSTABLE_STRUCTURE_AVOID: structure is actively breaking / regime changing ---
        unstable_event = event_type in ("REGIME_CHANGE", "STRUCTURE_BREAK", "REGIME_SHIFT")
        if structure_unstable or (unstable_event and not valid_support and not valid_resistance):
            status = "AVOID"
            setup_type = "UNSTABLE_STRUCTURE_AVOID"
            bias = "NEUTRAL"
            confidence = "LOW"
            setup_quality = "INCOMPLETE"
            avoid_reason = (
                "Структура нестабильна. Избегать ручного входа до стабилизации "
                "и появления чистой реакции от уровня."
            )
            reason = avoid_reason
            confirmation_needed = "Ждать стабилизации структуры, затем искать подтверждённый возврат или отказ от уровня."
            invalidation_condition = "Нет рабочего уровня отмены пока структура нестабильна."
            missing_conditions = [
                "структура нестабильна (STRUCTURE_UNSTABLE или REGIME_CHANGE)",
                "нет рабочего level_result",
                "нет валидного nearest_level",
                "нет чёткой структуры подтверждения/отмены",
            ]
        # --- true chop: weak/inactive market, no signal, no setup ---
        elif weak_cluster and not expansion_ready:
            status = "AVOID"
            setup_type = "NO_TRADE_CHOP"
            confidence = "MEDIUM"
            setup_quality = "INVALIDATED"
            avoid_reason = "Низкий signal_cluster_score и expansion_probability — чоп / неактивный рынок."
            reason = avoid_reason
            confirmation_needed = "Ждать нового контекста расширения MOS или реакции от уровня."
        elif support_defended and cluster_ready:
            _set_selection("SUPPORT")
            bias = "LONG" if bullish_flow else "NEUTRAL"
            is_at_level = not near_support_only
            status = "ENTRY_CANDIDATE" if (
                long_reversal_confirmed and has_execution_window and is_at_level
            ) else "FORMING" if (
                live_context_used and valid_support and (bullish_flow or near_support_only)
            ) else "WATCH"
            setup_type = "SUPPORT_DEFENSE_REVERSAL_SETUP"
            confidence = "HIGH" if strong_cluster and has_execution_window else "MEDIUM"
            setup_quality = "ACTIONABLE" if status == "ENTRY_CANDIDATE" else (
                "FORMING" if status == "FORMING" else "INCOMPLETE"
            )
            if bullish_flow:
                reason = "Защита поддержки совпадает с бычьим краткосрочным потоком и достаточным signal_cluster_score."
            else:
                reason = "Защита поддержки наблюдается, но flow не подтверждает long."
            confirmation_needed = "Искать подтверждение на графике: higher low, возврат или закрытие выше поддержки."
            invalidation_condition = "Защита поддержки не удалась или цена принимается ниже защищённого уровня."
            invalidation_level = round(sel_level * 0.998, 1) if sel_level else None
            missing_conditions = cls._entry_missing_conditions("LONG", fields, bool(invalidation_level))
        elif resistance_rejected and cluster_ready:
            _set_selection("RESISTANCE")
            bias = "SHORT" if bearish_flow else "NEUTRAL"
            is_at_level = not near_resistance_only
            status = "ENTRY_CANDIDATE" if (
                short_reversal_confirmed and has_execution_window and is_at_level
            ) else "FORMING" if (
                live_context_used and valid_resistance and (bearish_flow or near_resistance_only)
            ) else "WATCH"
            setup_type = "RESISTANCE_REJECTION_REVERSAL_SETUP"
            confidence = "HIGH" if strong_cluster and has_execution_window else "MEDIUM"
            setup_quality = "ACTIONABLE" if status == "ENTRY_CANDIDATE" else (
                "FORMING" if status == "FORMING" else "INCOMPLETE"
            )
            if bearish_flow:
                reason = "Отказ от сопротивления совпадает с медвежьим краткосрочным потоком и достаточным signal_cluster_score."
            else:
                reason = "Отказ от сопротивления наблюдается, но flow не подтверждает short."
            confirmation_needed = "Искать подтверждение на графике: lower high, фитиль отказа или закрытие ниже сопротивления."
            invalidation_condition = "Отказ от сопротивления не удался или цена принимается выше отвергнутого уровня."
            invalidation_level = round(sel_level * 1.002, 1) if sel_level else None
            missing_conditions = cls._entry_missing_conditions("SHORT", fields, bool(invalidation_level))
        elif (support_broken or resistance_broken) and expansion_ready and cluster_ready and (valid_support or valid_resistance):
            setup_type = "BREAKOUT_CONTINUATION_SETUP"
            if support_broken:
                _set_selection("SUPPORT")
                bias = "SHORT" if bearish_flow else "NEUTRAL"
            else:
                _set_selection("RESISTANCE")
                bias = "LONG" if bullish_flow else "NEUTRAL"
                
            flow_aligned = cls._flow_aligned_with_bias(flow_direction, bias)
            status = "ENTRY_CANDIDATE" if has_execution_window and flow_aligned and bias != "NEUTRAL" else "WATCH"
            confidence = "HIGH" if strong_cluster and gamma_weak else "MEDIUM"
            setup_quality = "ACTIONABLE" if status == "ENTRY_CANDIDATE" else "FORMING"
            reason = "Пробой уровня совмещён с поддержкой expansion_probability и signal_cluster_score."
            confirmation_needed = "Искать подтверждение на графике: удержание ретеста и продолжение от пробитого уровня."
            invalidation_condition = "Цена возвращает пробитый уровень против направления пробоя."
            invalidation_level = round(sel_level, 1) if sel_level else None
            missing_conditions = cls._entry_missing_conditions(bias, fields, bool(invalidation_level))
        elif current_state in ("COMPRESSION", "TRANSITION") and expansion_ready and cluster_ready:
            status = "WATCH"
            setup_type = "MID_RANGE_EXPANSION_SETUP"
            bias = cls._flow_bias(flow_direction)
            confidence = "HIGH" if strong_cluster and gamma_weak else "MEDIUM"
            
            if live_support_result in ("AT_SUPPORT", "NEAR_SUPPORT") and live_support_level:
                _set_selection("SUPPORT")
                invalidation_level = round(sel_level * 0.998, 1)
            elif live_resistance_result in ("AT_RESISTANCE", "NEAR_RESISTANCE") and live_resistance_level:
                _set_selection("RESISTANCE")
                invalidation_level = round(sel_level * 1.002, 1)
            else:
                invalidation_level = None

            has_invalidation = bool(invalidation_level)
            if valid_support or valid_resistance:
                if has_invalidation:
                    setup_quality = "FORMING"
                    missing_conditions = []
                else:
                    setup_quality = "INCOMPLETE"
                    missing_conditions = ["нет рабочего уровня отмены, вне зоны действия уровней"]
            else:
                setup_quality = "INCOMPLETE"
                missing_conditions = ["нет рабочего level_result", "нет рабочего уровня отмены"]
            reason = "Контекст компрессии/перехода совмещён с растущей expansion_probability и signal_cluster_score."
            confirmation_needed = "Искать подтверждение: пробой края диапазона, принятие цены и направленное продолжение."
            invalidation_condition = "Расширение останавливается и цена возвращается в середину диапазона."
        elif gamma_weak and expansion_ready and (bullish_flow or bearish_flow):
            status = "WATCH"
            setup_type = "FLOW_EXHAUSTION_REVERSAL_SETUP"
            if long_reversal_confirmed:
                _set_selection("SUPPORT")
                bias = "LONG"
                confidence = "MEDIUM" if cluster_ready else "LOW"
                setup_quality = "FORMING"
                reason = "Нисходящее истощение: возврат поддержки и бычий поток, но подтверждение графика ещё требуется."
                confirmation_needed = "Искать неудачное нисходящее продолжение, удержание возврата и бычье продолжение."
                invalidation_condition = "Возврат не удался и цена принимается ниже поддержки."
                invalidation_level = sel_level
                missing_conditions = [] if cluster_ready else ["signal_cluster_score не готов"]
            elif short_reversal_confirmed:
                _set_selection("RESISTANCE")
                bias = "SHORT"
                confidence = "MEDIUM" if cluster_ready else "LOW"
                setup_quality = "FORMING"
                reason = "Восходящее истощение: отказ от сопротивления и медвежий поток, но подтверждение графика ещё требуется."
                confirmation_needed = "Искать неудачное восходящее продолжение, удержание отказа и медвежье продолжение."
                invalidation_condition = "Отказ не удался и цена принимается выше сопротивления."
                if sel_level and not level_context_stale and short_reversal_confirmed:
                    invalidation_level = round(sel_level * 1.002, 1)
                else:
                    invalidation_level = None
                missing_conditions = [] if cluster_ready else ["signal_cluster_score не готов"]
            else:
                bias = "NEUTRAL"
                confidence = "MEDIUM" if cluster_ready else "LOW"
                setup_quality = "INCOMPLETE"
                reason = "Присутствует направленное истощение потока, но подтверждение разворота неполное."
                confirmation_needed = "Ждать неудачного нисходящего продолжения, возврата выше поддержки и разворота потока."
                invalidation_condition = "Нет рабочего уровня отмены до подтверждения разворотного уровня и флипа потока."
                invalidation_level = None
                missing_conditions = cls._flow_exhaustion_missing_conditions(
                    flow_direction, bool(valid_support or valid_resistance), support_defended or downside_false_break_reclaim
                )
        elif event_type in ("BREAKOUT_ALERT", "EXPANSION_PROBABILITY_RISING") or expansion_ready:
            status = "WATCH"
            setup_type = "MID_RANGE_EXPANSION_SETUP"
            bias = cls._flow_bias(flow_direction)
            confidence = "LOW" if not cluster_ready else "MEDIUM"
            
            if live_support_result in ("AT_SUPPORT", "NEAR_SUPPORT") and live_support_level:
                _set_selection("SUPPORT")
                invalidation_level = round(sel_level * 0.998, 1)
            elif live_resistance_result in ("AT_RESISTANCE", "NEAR_RESISTANCE") and live_resistance_level:
                _set_selection("RESISTANCE")
                invalidation_level = round(sel_level * 1.002, 1)
            else:
                invalidation_level = None
                
            has_invalidation = bool(invalidation_level)
            if cluster_ready and (valid_support or valid_resistance) and has_invalidation:
                setup_quality = "FORMING"
                missing_conditions = []
            else:
                setup_quality = "INCOMPLETE"
                missing_conditions = ["нет рабочего level_result", "нет рабочего уровня отмены"]
            reason = "MOS показывает контекст расширения, но подтверждение недостаточно для ENTRY_CANDIDATE."
            confirmation_needed = "Ждать взаимодействия с уровнем и принятия цены в направлении расширения."
            invalidation_condition = "Сигнал расширения затухает или цена остаётся в диапазоне."
        # --- NO_ACTIONABLE_SETUP: active environment, but no trading setup yet ---
        elif (
            active_current_state
            and active_execution_state
            and no_actionable_event
            and not (valid_support or valid_resistance)
            and invalidation_level is None
        ):
            status = "AVOID"
            setup_type = "NO_ACTIONABLE_SETUP"
            bias = "NEUTRAL"
            confidence = "LOW"
            setup_quality = "INCOMPLETE"
            reason = (
                f"{current_state} + {execution_state} + {flow_direction} поток. "
                "Среда активная, но нет события, реакции уровня, ближайшего уровня и уровня отмены. "
                "Ручной вход заблокирован до появления чистого сетапа."
            )
            confirmation_needed = "Ждать чистого MOS-сетапа и подтверждения цены на графике."
            invalidation_condition = "Нет рабочего уровня отмены до формирования сетапа."
            missing_conditions = [
                "нет event_type",
                "нет level_result",
                "нет nearest_level",
                "нет уровня отмены",
            ]
        else:
            status = "AVOID"
            setup_type = "NO_TRADE_CHOP"
            confidence = "LOW"
            setup_quality = "INCOMPLETE"
            avoid_reason = (
                "Нет выравненного сетапа: чоп / пиннинг / компрессия — "
                "нет события, реакции уровня, поток слабый или нейтральный."
            )
            reason = avoid_reason
            confirmation_needed = "Ждать более чистого MOS-сетапа и независимого подтверждения на графике."

        if setup_type == "NO_TRADE_CHOP":
            bias = "NEUTRAL"
            invalidation_condition = "Фильтр чопа отменяется только после выравнивания полей MOS в чётко определённый сетап."
            invalidation_level = None

        if setup_type == "UNSTABLE_STRUCTURE_AVOID":
            bias = "NEUTRAL"
            invalidation_level = None

        if status == "ENTRY_CANDIDATE":
            entry_missing = cls._entry_missing_conditions(bias, fields, bool(invalidation_level))
            if entry_missing:
                status = "WATCH"
                setup_quality = "INCOMPLETE"
                missing_conditions = entry_missing
                invalidation_level = None
                reason = "Ручной сетап не активен — обязательные условия входа неполные."

        # ── Live context guard: ENTRY_CANDIDATE requires live_context_used ───────
        if status == "ENTRY_CANDIDATE" and not live_context_used:
            status = "WATCH"
            setup_quality = "INCOMPLETE"
            if "live_context_unavailable" not in missing_conditions:
                missing_conditions = list(missing_conditions) + ["live_context_unavailable"]
            reason = "Нет live level context — вход заблокирован."

        # ── Replay stale guard: only fires when live_context_used=False ─────────
        if replay_context_stale and not live_context_used:
            if "stale replay context" not in missing_conditions:
                missing_conditions = list(missing_conditions) + ["stale replay context"]
            if status in ("ENTRY_CANDIDATE", "WATCH") and setup_quality in ("ACTIONABLE", "FORMING"):
                setup_quality = "INCOMPLETE"
            if status == "ENTRY_CANDIDATE":
                status = "WATCH"
                reason = "Stale replay context (no live context): вход заблокирован."
            invalidation_level = None

        near_level = cls._is_near_level(price, sel_level if sel_level else primary_live_level)

        ladder = cls._decision_ladder(
            status=status,
            setup_type=setup_type,
            bias=bias,
            confidence=confidence,
            setup_quality=setup_quality,
            missing_conditions=missing_conditions,
            fields=fields,
            has_execution_window=has_execution_window,
            expansion_ready=expansion_ready,
            cluster_ready=cluster_ready,
            near_level=near_level,
        )

        return {
            "manual_status": cls._enum(status, cls.STATUSES, "AVOID"),
            "manual_setup_type": cls._enum(setup_type, cls.SETUP_TYPES, "NONE"),
            "manual_bias": cls._enum(bias, cls.BIASES, "NEUTRAL"),
            "manual_confidence": cls._enum(confidence, cls.CONFIDENCES, "LOW"),
            "setup_quality": cls._enum(setup_quality, cls.SETUP_QUALITIES, "INCOMPLETE"),
            "missing_conditions": missing_conditions,
            "manual_reason": reason,
            "confirmation_needed": confirmation_needed,
            "invalidation_condition": invalidation_condition,
            "invalidation_level": invalidation_level,
            "avoid_reason": avoid_reason,
            "decision_ladder": ladder,
            "disclaimer": (
                "ENTRY_CANDIDATE — не сигнал покупки/продажи; это означает лишь, что трейдер "
                "может искать подтверждение цены на графике."
            ),
            # NEW fields exposed for payload
            "selected_setup_level": sel_level,
            "selected_setup_side": sel_side,
            "selected_setup_level_source": sel_source,
            "selected_setup_level_distance_pct": sel_dist,
            "selected_setup_level_result": sel_result,
            "selected_setup_level_type": sel_type,
            "setup_side_required": "SUPPORT" if "SUPPORT" in setup_type else ("RESISTANCE" if "RESISTANCE" in setup_type else None)
        }
"""
    
    parts = re.split(r'    @classmethod\n    def classify\(cls, mos: Dict\[str, Any\]\) -> Dict\[str, Any\]:', content)
    header = parts[0]
    remainder = parts[1]
    
    parts2 = re.split(r'    @classmethod\n    def source_fields\(cls, mos: Dict\[str, Any\]\) -> Dict\[str, Any\]:', remainder)
    footer = '    @classmethod\n    def source_fields(cls, mos: Dict[str, Any]) -> Dict[str, Any]:' + parts2[1]

    with open('backend/engine/manual_setup_classifier.py', 'w', encoding='utf-8') as f:
        f.write(header + new_classify + footer)

if __name__ == '__main__':
    main()
