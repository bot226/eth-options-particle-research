"""Read-only manual trading context classifier for MOS.

ManualSetupClassifier consumes already-computed MOS fields and returns an
advisory context for human chart review. It does not create orders, mutate
engine state, or persist anything.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


class ManualSetupClassifier:
    """Classify the latest MOS snapshot into a manual trading context."""

    STATUSES = ("AVOID", "WATCH", "ENTRY_CANDIDATE")
    SETUP_TYPES = (
        "BREAKOUT_CONTINUATION_SETUP",
        "MID_RANGE_EXPANSION_SETUP",
        "SUPPORT_DEFENSE_REVERSAL_SETUP",
        "RESISTANCE_REJECTION_REVERSAL_SETUP",
        "FLOW_EXHAUSTION_REVERSAL_SETUP",
        "NO_ACTIONABLE_SETUP",
        "UNSTABLE_STRUCTURE_AVOID",
        "NO_TRADE_CHOP",
        "NONE",
    )
    BIASES = ("LONG", "SHORT", "NEUTRAL")
    CONFIDENCES = ("LOW", "MEDIUM", "HIGH")
    SETUP_QUALITIES = ("INCOMPLETE", "FORMING", "ACTIONABLE", "INVALIDATED")

    @classmethod
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
        
        # Default compat fields for older tests
        nearest_level = live_ctx.get("live_nearest_level") or fields.get("nearest_level")
        level_result = live_ctx.get("live_level_result") or fields.get("level_result")
        level_side = live_ctx.get("live_level_side") or fields.get("level_side")
        
        if not live_ctx and level_result and level_side:
            if level_side == "SUPPORT":
                live_support_result = level_result
                live_support_level = nearest_level
            elif level_side == "RESISTANCE":
                live_resistance_result = level_result
                live_resistance_level = nearest_level
                
        primary_live_level = live_ctx.get("primary_live_level")
        primary_live_side = live_ctx.get("primary_live_side")
        primary_live_result = live_ctx.get("primary_live_result")
        live_context_used = bool(live_ctx.get("live_context_used", False))
        
        # Default compat fields
        nearest_level = live_ctx.get("live_nearest_level") or fields.get("nearest_level")
        level_result = live_ctx.get("live_level_result") or fields.get("level_result")
        level_side = live_ctx.get("live_level_side") or fields.get("level_side")

        # ── Flow direction: prefer explicit field, fallback to synthetic_flow_pressure ──
        raw_short_term_flow_direction = fields.get("short_term_flow_direction", "")
        raw_sfp = (
            mos.get("synthetic_flow_pressure")
            if mos.get("synthetic_flow_pressure") is not None else
            (mos.get("_sfp") if mos.get("_sfp") is not None else fields.get("_sfp"))
        )
        
        WEAK_SFP_THRESHOLD = 5.0
        STRONG_SFP_THRESHOLD = 15.0
        
        flow_direction_source = ""
        derived_short_term_flow_direction = "NEUTRAL"
        derived_flow_strength = "NONE"
        
        if raw_sfp is not None:
            flow_direction_source = "synthetic_flow_pressure"
            sfp_val = float(raw_sfp)
            if sfp_val >= STRONG_SFP_THRESHOLD:
                derived_short_term_flow_direction = "BULLISH"
                derived_flow_strength = "STRONG"
            elif sfp_val >= WEAK_SFP_THRESHOLD:
                derived_short_term_flow_direction = "WEAK_BULLISH"
                derived_flow_strength = "WEAK"
            elif sfp_val <= -STRONG_SFP_THRESHOLD:
                derived_short_term_flow_direction = "BEARISH"
                derived_flow_strength = "STRONG"
            elif sfp_val <= -WEAK_SFP_THRESHOLD:
                derived_short_term_flow_direction = "WEAK_BEARISH"
                derived_flow_strength = "WEAK"
            else:
                derived_short_term_flow_direction = "NEUTRAL"
                derived_flow_strength = "NONE"
        else:
            flow_direction_source = "short_term_flow_direction"
            derived_short_term_flow_direction = raw_short_term_flow_direction.upper() if raw_short_term_flow_direction else "NEUTRAL"
            
        flow_direction = derived_short_term_flow_direction

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
        bullish_flow = flow_direction in ("BULLISH", "WEAK_BULLISH", "UP", "LONG", "BUYING", "POSITIVE")
        bearish_flow = flow_direction in ("BEARISH", "WEAK_BEARISH", "DOWN", "SHORT", "SELLING", "NEGATIVE")
        strong_bullish_flow = flow_direction in ("BULLISH", "UP", "LONG", "BUYING", "POSITIVE")
        strong_bearish_flow = flow_direction in ("BEARISH", "DOWN", "SHORT", "SELLING", "NEGATIVE")
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
        near_support_only = live_support_result in ("NEAR_SUPPORT", "AT_SUPPORT")
        resistance_rejected = live_resistance_result in (
            "RESISTANCE_REJECTION", "REJECTED", "REJECTION", "FAIL", "HOLD",
            "NEAR_RESISTANCE", "AT_RESISTANCE"
        )
        near_resistance_only = live_resistance_result in ("NEAR_RESISTANCE", "AT_RESISTANCE")
        
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
            
            invalidation_level = round(sel_level * 0.998, 1) if sel_level else None
            
            is_at_level = live_support_result == "AT_SUPPORT" or (sel_dist is not None and sel_dist <= 0.15)
            is_near_level = live_support_result == "NEAR_SUPPORT" or (sel_dist is not None and 0.15 < sel_dist <= 0.50)
            
            if strong_bullish_flow and has_execution_window and is_at_level and sel_level and invalidation_level:
                status = "ENTRY_CANDIDATE"
                setup_quality = "ACTIONABLE"
            elif (bullish_flow or is_near_level or is_at_level) and sel_level and invalidation_level:
                status = "WATCH"
                setup_quality = "FORMING"
            else:
                status = "WATCH"
                setup_quality = "INCOMPLETE"
                
            setup_type = "SUPPORT_DEFENSE_REVERSAL_SETUP"
            confidence = "HIGH" if strong_cluster and has_execution_window else "MEDIUM"
            if status == "ENTRY_CANDIDATE":
                reason = "Защита поддержки совпадает с сильным бычьим потоком и активным окном исполнения."
            elif setup_quality == "FORMING":
                reason = "Защита поддержки формируется (WATCH/FORMING)."
            else:
                reason = "Защита поддержки наблюдается, но flow или расстояние не подтверждают вход."
            confirmation_needed = "Искать подтверждение на графике: higher low, возврат или закрытие выше поддержки."
            invalidation_condition = "Защита поддержки не удалась или цена принимается ниже защищённого уровня."
            missing_conditions = cls._entry_missing_conditions("LONG", fields, bool(invalidation_level))
        elif resistance_rejected and cluster_ready:
            _set_selection("RESISTANCE")
            bias = "SHORT" if bearish_flow else "NEUTRAL"
            
            invalidation_level = round(sel_level * 1.002, 1) if sel_level else None
            
            is_at_level = live_resistance_result == "AT_RESISTANCE" or (sel_dist is not None and sel_dist <= 0.15)
            is_near_level = live_resistance_result == "NEAR_RESISTANCE" or (sel_dist is not None and 0.15 < sel_dist <= 0.50)
            
            if strong_bearish_flow and has_execution_window and is_at_level and sel_level and invalidation_level:
                status = "ENTRY_CANDIDATE"
                setup_quality = "ACTIONABLE"
            elif (bearish_flow or is_near_level or is_at_level) and sel_level and invalidation_level:
                status = "WATCH"
                setup_quality = "FORMING"
            else:
                status = "WATCH"
                setup_quality = "INCOMPLETE"
                
            setup_type = "RESISTANCE_REJECTION_REVERSAL_SETUP"
            confidence = "HIGH" if strong_cluster and has_execution_window else "MEDIUM"
            if status == "ENTRY_CANDIDATE":
                reason = "Отказ от сопротивления совпадает с сильным медвежьим потоком и активным окном исполнения."
            elif setup_quality == "FORMING":
                reason = "Отказ от сопротивления формируется (WATCH/FORMING)."
            else:
                reason = "Отказ от сопротивления наблюдается, но flow или расстояние не подтверждают вход."
            confirmation_needed = "Искать подтверждение на графике: lower high, фитиль отказа или закрытие ниже сопротивления."
            invalidation_condition = "Отказ от сопротивления не удался или цена принимается выше отвергнутого уровня."
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

        # ── Final Consistency Checks ──
        decision_blocker = "NONE"
        if status == "AVOID":
            if setup_type == "NO_TRADE_CHOP":
                decision_blocker = "NO_ACTIONABLE_SETUP"
            elif setup_type == "UNSTABLE_STRUCTURE_AVOID":
                decision_blocker = "STRUCTURE_UNSTABLE"
            elif not has_execution_window:
                decision_blocker = "NO_ACTIVE_EXECUTION"
            elif not sel_level:
                decision_blocker = "NO_SELECTED_LEVEL"
            elif not invalidation_level:
                decision_blocker = "NO_INVALIDATION"
            else:
                decision_blocker = "FLOW_NOT_ALIGNED"
        elif status == "WATCH":
            if not has_execution_window:
                decision_blocker = "NO_ACTIVE_EXECUTION"
            elif setup_quality == "FORMING":
                decision_blocker = "PRICE_CONFIRMATION_PENDING"
            else:
                decision_blocker = "MISSING_CONDITIONS"

        # Consistency Rule #1: FORMING setup with active execution and aligned flow MUST be WATCH.
        flow_aligned = cls._flow_aligned_with_bias(flow_direction, bias)
        if setup_quality == "FORMING" and bias in ("LONG", "SHORT") and sel_level and invalidation_level and flow_aligned and has_execution_window and not missing_conditions:
            if status == "AVOID":
                status = "WATCH"
                decision_blocker = "PRICE_CONFIRMATION_PENDING"

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

        candidate_side = bias if bias in ("LONG", "SHORT") else None
        no_side_reason = "unknown"

        if not candidate_side:
            current_price = cls._number(mos.get("price")) or 0.0
            if setup_type == "SUPPORT_DEFENSE_REVERSAL_SETUP":
                if sel_side != "SUPPORT":
                    no_side_reason = "selected_setup_side_not_support"
                elif not sel_level:
                    no_side_reason = "missing_selected_setup_level"
                elif not invalidation_level:
                    no_side_reason = "missing_invalidation_level"
                elif current_price > 0 and invalidation_level >= current_price:
                    no_side_reason = "invalidation_not_below_price"
                else:
                    candidate_side = "LONG"
                    no_side_reason = None
            elif setup_type == "RESISTANCE_REJECTION_REVERSAL_SETUP":
                if sel_side != "RESISTANCE":
                    no_side_reason = "selected_setup_side_not_resistance"
                elif not sel_level:
                    no_side_reason = "missing_selected_setup_level"
                elif not invalidation_level:
                    no_side_reason = "missing_invalidation_level"
                elif current_price > 0 and invalidation_level <= current_price:
                    no_side_reason = "invalidation_not_above_price"
                else:
                    candidate_side = "SHORT"
                    no_side_reason = None
            else:
                no_side_reason = "missing_selected_setup_side"
        else:
            no_side_reason = None

        # no_side_reason is added to entry_debug_json below        # ── Diagnostics generation ──
        entry_block_stage = "unknown"
        entry_block_reason = "unknown"
        
        if status == "ENTRY_CANDIDATE":
            entry_block_stage = None
            entry_block_reason = None
        else:
            if not sel_level:
                entry_block_stage = "level_context"
                entry_block_reason = "no_selected_level"
            elif setup_type == "NO_TRADE_CHOP" or setup_type == "NONE":
                entry_block_stage = "setup_detection"
                entry_block_reason = "market_chop" if setup_type == "NO_TRADE_CHOP" else "no_valid_setup"
            elif setup_type == "UNSTABLE_STRUCTURE_AVOID":
                entry_block_stage = "volatility_filter"
                entry_block_reason = "unstable_structure"
            elif setup_type == "NO_ACTIONABLE_SETUP" or setup_quality == "NOT_ACTIONABLE":
                entry_block_stage = "quality_score"
                entry_block_reason = "not_actionable"
            elif setup_quality == "INCOMPLETE" or (missing_conditions and "live_context_unavailable" in missing_conditions):
                entry_block_stage = "candle_confirmation"
                entry_block_reason = "incomplete_setup"
            elif not candidate_side:
                entry_block_stage = "side_selection"
                entry_block_reason = "no_side"
            elif not invalidation_level:
                entry_block_stage = "invalidation_distance"
                entry_block_reason = "missing_invalidation_level"
            elif not has_execution_window:
                entry_block_stage = "entry_trigger"
                entry_block_reason = "no_entry_trigger"
            elif not flow_aligned:
                entry_block_stage = "flow_alignment"
                if flow_direction == "NEUTRAL":
                    entry_block_reason = "flow_missing"
                elif derived_flow_strength == "WEAK":
                    entry_block_reason = "flow_too_weak"
                else:
                    entry_block_reason = "flow_not_aligned"
            elif setup_quality == "FORMING":
                entry_block_stage = "candle_confirmation"
                entry_block_reason = "candle_confirmation_missing"
            elif missing_conditions:
                entry_block_stage = "unknown"
                entry_block_reason = "incomplete_setup"
            else:
                entry_block_stage = "unknown"
                entry_block_reason = "unknown"

        latest_main_dir = mos.get("latest_main_context_direction", fields.get("latest_main_context_direction"))
        latest_main_age = mos.get("latest_main_context_age_sec", fields.get("latest_main_context_age_sec"))
        mc_ttl = 600

        if latest_main_dir in ("LONG", "SHORT"):
            if latest_main_age is not None and latest_main_age > mc_ttl:
                entry_main_context_alignment = "EXPIRED"
                entry_debug_json["main_context_age_sec"] = latest_main_age
                entry_debug_json["main_context_ttl_sec"] = mc_ttl
                entry_debug_json["main_context_direction"] = latest_main_dir
                entry_debug_json["main_context_confidence"] = mos.get("latest_main_context_confidence", fields.get("latest_main_context_confidence"))
                entry_debug_json["main_context_source"] = mos.get("latest_main_context_source", fields.get("latest_main_context_source"))
            elif candidate_side in ("LONG", "SHORT"):
                entry_main_context_alignment = "ALIGNED" if latest_main_dir == candidate_side else "CONFLICT"
            else:
                entry_main_context_alignment = "NOT_EVALUATED"
        else:
            entry_main_context_alignment = "MISSING"

        if flow_direction in ("NEUTRAL", ""):
            entry_flow_alignment = "MISSING"
        else:
            flow_b = cls._flow_bias(flow_direction)
            if flow_b == candidate_side and candidate_side in ("LONG", "SHORT"):
                entry_flow_alignment = "WEAK" if derived_flow_strength == "WEAK" else "ALIGNED"
            elif candidate_side in ("LONG", "SHORT"):
                entry_flow_alignment = "CONFLICT"
            else:
                entry_flow_alignment = "NOT_REQUIRED"

        entry_candle_confirmation = "UNKNOWN"
        if setup_quality == "FORMING":
            entry_candle_confirmation = "MISSING"
        elif setup_quality == "ACTIONABLE" or status == "ENTRY_CANDIDATE":
            entry_candle_confirmation = "CONFIRMED"

        entry_missing_conditions_json = list(missing_conditions)
        if entry_block_reason == "unknown":
            if "diagnostic_not_available" not in entry_missing_conditions_json:
                entry_missing_conditions_json.append("diagnostic_not_available")
        elif entry_block_reason and entry_block_reason != "unknown" and entry_block_reason not in entry_missing_conditions_json:
            entry_missing_conditions_json.append(entry_block_reason)

        entry_quality_components = {
            "level_quality": 0.5 if sel_level else 0.0,
            "distance_score": 0.5,
            "main_context_score": 1.0 if entry_main_context_alignment == "ALIGNED" else (0.0 if entry_main_context_alignment == "CONFLICT" else 0.5),
            "flow_score": 1.0 if entry_flow_alignment == "ALIGNED" else (0.5 if entry_flow_alignment == "WEAK" else 0.0),
            "candle_confirmation_score": 1.0 if entry_candle_confirmation == "CONFIRMED" else 0.0,
            "volatility_score": 0.5,
            "risk_reward_score": 0.5,
            "final_score": 0.5,
            "required_score": 0.65
        }
        
        entry_debug_json = {
            "flow_direction": flow_direction, 
            "sfp": raw_sfp,
            "raw_status": status,
            "raw_setup_type": setup_type,
            "raw_actionability": setup_quality,
            "raw_decision_blocker": decision_blocker,
            "no_side_reason": no_side_reason
        }

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
            "setup_side_required": "SUPPORT" if "SUPPORT" in setup_type else ("RESISTANCE" if "RESISTANCE" in setup_type else None),
            "decision_blocker": decision_blocker,
            # NEW Diagnostic Fields
            "entry_block_reason": entry_block_reason,
            "entry_block_stage": entry_block_stage,
            "entry_missing_conditions_json": entry_missing_conditions_json,
            "entry_quality_score": 0.5,
            "entry_quality_components_json": entry_quality_components,
            "entry_required_score": 0.65,
            "entry_setup_side": "LONG" if candidate_side == "LONG" else ("SHORT" if candidate_side == "SHORT" else ("SUPPORT" if "SUPPORT" in setup_type else ("RESISTANCE" if "RESISTANCE" in setup_type else None))),
            "entry_candidate_side": candidate_side,
            "entry_distance_to_level_pct": sel_dist,
            "entry_distance_to_trigger_pct": None,
            "entry_distance_to_invalidation_pct": None,
            "entry_main_context_direction": latest_main_dir,
            "entry_main_context_alignment": entry_main_context_alignment,
            "entry_main_context_confidence": mos.get("latest_main_context_confidence", fields.get("latest_main_context_confidence")),
            "entry_flow_alignment": entry_flow_alignment,
            "entry_candle_confirmation": entry_candle_confirmation,
            "entry_level_confirmation": "CONFIRMED" if sel_level else "MISSING",
            "entry_cooldown_active": 0,
            "entry_same_zone_guard_active": 0,
            "entry_previous_candidate_guard_active": 0,
            "entry_guard_reasons_json": [],
            "entry_debug_json": entry_debug_json,
        }
    @classmethod
    def source_fields(cls, mos: Dict[str, Any]) -> Dict[str, Any]:
        """Return the normalized MOS fields used by the classifier."""
        event = cls._latest_event(mos.get("events"))
        phase_1 = cls._get(mos, "advanced_intelligence", "phase_1") or {}
        phase_2 = cls._get(mos, "advanced_intelligence", "phase_2") or {}

        raw_short_term_flow_direction = (
            cls._field(mos, "short_term_flow_direction")
            or cls._get(phase_1, "short_term_flow_context", "features", "short_term_flow_direction")
            or cls._get(phase_1, "short_term_flow_context", "metrics", "short_term_flow_direction")
            or cls._get(mos, "flow", "signals", "flow_bias")
        ) or ""

        WEAK_SFP_THRESHOLD = 5.0
        STRONG_SFP_THRESHOLD = 15.0

        raw_sfp = mos.get("synthetic_flow_pressure")
        if raw_sfp is None:
            raw_sfp = cls._get(mos, "advanced_intelligence", "phase_1",
                               "short_term_flow_context", "metrics", "synthetic_flow_pressure")
                               
        flow_direction_source = ""
        derived_short_term_flow_direction = "NEUTRAL"
        derived_flow_strength = "NONE"
        
        if raw_sfp is not None:
            flow_direction_source = "synthetic_flow_pressure"
            sfp_val = float(raw_sfp)
            if sfp_val >= STRONG_SFP_THRESHOLD:
                derived_short_term_flow_direction = "BULLISH"
                derived_flow_strength = "STRONG"
            elif sfp_val >= WEAK_SFP_THRESHOLD:
                derived_short_term_flow_direction = "WEAK_BULLISH"
                derived_flow_strength = "WEAK"
            elif sfp_val <= -STRONG_SFP_THRESHOLD:
                derived_short_term_flow_direction = "BEARISH"
                derived_flow_strength = "STRONG"
            elif sfp_val <= -WEAK_SFP_THRESHOLD:
                derived_short_term_flow_direction = "WEAK_BEARISH"
                derived_flow_strength = "WEAK"
            else:
                derived_short_term_flow_direction = "NEUTRAL"
                derived_flow_strength = "NONE"
        else:
            flow_direction_source = "short_term_flow_direction"
            derived_short_term_flow_direction = raw_short_term_flow_direction.upper() if raw_short_term_flow_direction else "NEUTRAL"

        flow_direction = derived_short_term_flow_direction

        return {
            "event_type": cls._normalize(
                cls._field(mos, "event_type") or event.get("event_type") or event.get("type")
            ),
            "execution_timing_state": cls._normalize(
                cls._field(mos, "execution_timing_state")
                or cls._get(phase_2, "execution_timing", "features", "execution_state")
            ),
            "current_state": cls._normalize(
                cls._field(mos, "current_state") or cls._get(mos, "state_machine", "current_state")
            ),
            # Live level context (from _build_live_level_context — always real-time)
            "level_result": cls._normalize(
                mos.get("_live_level_result") or cls._field(mos, "level_result")
            ),
            "level_side": cls._normalize(
                mos.get("_live_level_side") or cls._field(mos, "level_side")
            ),
            "nearest_level": cls._number(
                mos.get("_live_nearest_level") or cls._field(mos, "nearest_level"), default=None
            ),
            "short_term_flow_direction": cls._normalize(flow_direction),
            "raw_synthetic_flow_pressure": raw_sfp,
            "raw_short_term_flow_direction": raw_short_term_flow_direction,
            "flow_direction_source": flow_direction_source,
            "flow_threshold_used": "15.0/5.0",
            "derived_short_term_flow_direction": derived_short_term_flow_direction,
            "derived_flow_strength": derived_flow_strength,
            "sfp_derived_flow":          bool(
                raw_sfp is not None and
                not (cls._field(mos, "short_term_flow_direction") or
                     cls._get(phase_1, "short_term_flow_context", "features", "short_term_flow_direction") or
                     cls._get(phase_1, "short_term_flow_context", "metrics", "short_term_flow_direction"))
            ),
            "gamma_slope_state": cls._normalize(
                cls._field(mos, "gamma_slope_state")
                or cls._get(phase_1, "gamma_surface", "metrics", "gamma_slope_state"),
                lower=True,
            ),
            "signal_cluster_score": cls._number(
                cls._field(mos, "signal_cluster_score")
                or cls._get(mos, "advanced_intelligence", "signal_cluster_score")
            ),
            "expansion_probability": cls._number(
                cls._field(mos, "expansion_probability")
                or cls._get(phase_1, "regime_transition", "metrics", "expansion_probability")
            ),
            "price": cls._number(cls._field(mos, "price") or mos.get("spot"), default=None),
            # Live context meta
            "live_level_result":     mos.get("_live_level_result"),
            "live_nearest_level":    mos.get("_live_nearest_level"),
            "live_level_side":       mos.get("_live_level_side"),
            "live_context_used":     mos.get("_live_context_used", False),
            "live_context_age_sec":  0,
            "live_context_ttl_sec":  300,
            # Live context full detail (from _live_level_ctx)
            "live_level_type":       mos.get("_live_level_ctx", {}).get("live_level_type"),
            "live_distance_pct":     mos.get("_live_level_ctx", {}).get("live_distance_pct"),
            "live_context_source":   mos.get("_live_level_ctx", {}).get("live_context_source"),
            "live_context_ts":       mos.get("_live_level_ctx", {}).get("live_context_ts"),
            "live_source_event_id":  mos.get("_live_level_ctx", {}).get("live_source_event_id"),
            "live_source_snapshot_id": mos.get("_live_level_ctx", {}).get("live_source_snapshot_id"),
            "live_source_snapshot_sequence_id": mos.get("_live_level_ctx", {}).get("live_source_snapshot_sequence_id"),
            # Replay reference (raw from event_level_reactions, never used for live decisions)
            "level_context_age_sec":    mos.get("_level_context_age_sec"),
            "level_context_stale":      mos.get("_level_context_stale", True),
            "level_context_used":       mos.get("_level_context_meta", {}).get("used_for_live_decision", False),
            "level_context_reaction_id": mos.get("_level_context_meta", {}).get("reaction_id"),
            "level_context_reaction_ts": mos.get("_level_context_meta", {}).get("reaction_ts"),
            "level_context_ttl_sec":    mos.get("_level_context_meta", {}).get("ttl_sec", 300),
            "raw_level_result":         mos.get("_level_context_meta", {}).get("raw_level_result"),
            "raw_nearest_level":        mos.get("_level_context_meta", {}).get("raw_nearest_level"),
            "raw_level_side":           mos.get("_level_context_meta", {}).get("raw_level_side"),
        }

    @staticmethod
    def _field(mos: Dict[str, Any], name: str) -> Any:
        if name in mos:
            return mos.get(name)
        return mos.get("latest", {}).get(name) if isinstance(mos.get("latest"), dict) else None

    @staticmethod
    def _get(source: Dict[str, Any], *path: str) -> Any:
        value: Any = source
        for key in path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value

    @staticmethod
    def _latest_event(events: Any) -> Dict[str, Any]:
        if isinstance(events, list) and events:
            latest = events[-1]
            return latest if isinstance(latest, dict) else {}
        return {}

    @staticmethod
    def _normalize(value: Any, lower: bool = False) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if lower:
            return text.lower()
        return text.upper().replace(" ", "_").replace("-", "_")

    @staticmethod
    def _number(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _enum(value: str, allowed: Iterable[str], default: str) -> str:
        return value if value in allowed else default

    @staticmethod
    def _is_near_level(price: Optional[float], nearest_level: Optional[float]) -> bool:
        if not price or not nearest_level or price <= 0:
            return False
        return abs(price - nearest_level) / price <= 0.0025

    @staticmethod
    def _flow_bias(flow_direction: str) -> str:
        if flow_direction in ("BULLISH", "UP", "LONG", "BUYING", "POSITIVE"):
            return "LONG"
        if flow_direction in ("BEARISH", "DOWN", "SHORT", "SELLING", "NEGATIVE"):
            return "SHORT"
        return "NEUTRAL"

    @classmethod
    def _flow_aligned_with_bias(cls, flow_direction: str, bias: str) -> bool:
        return cls._flow_bias(flow_direction) == bias and bias != "NEUTRAL"

    @classmethod
    def _entry_missing_conditions(
        cls,
        bias: str,
        fields: Dict[str, Any],
        has_invalidation_level: bool,
    ) -> list:
        missing = []
        level_result = fields.get("level_result")
        nearest_level = fields.get("nearest_level")
        confirmation_ready = bool(level_result)

        if not confirmation_ready:
            missing.append("no actionable level_result")
        if nearest_level is None or nearest_level <= 0:
            missing.append("no valid nearest_level")
        if not confirmation_ready:
            missing.append("confirmation logic is incomplete")
        if not has_invalidation_level:
            missing.append("no actionable invalidation level")
        if not cls._flow_aligned_with_bias(fields.get("short_term_flow_direction", ""), bias):
            missing.append("flow is not aligned with manual_bias")
        return missing

    @classmethod
    def _flow_exhaustion_missing_conditions(
        cls,
        flow_direction: str,
        valid_nearest_level: bool,
        confirmed_reversal_level_reaction: bool,
    ) -> list:
        missing = []
        if not confirmed_reversal_level_reaction:
            missing.append("no confirmed reversal level reaction")
        if cls._flow_bias(flow_direction) != "LONG":
            missing.append("flow has not flipped bullish")
        if not valid_nearest_level:
            missing.append("no valid nearest_level")
        missing.append("no actionable invalidation level")
        return missing

    @classmethod
    def _directional_bias(
        cls,
        level_side: str,
        flow_direction: str,
        price: Optional[float],
        nearest_level: Optional[float],
    ) -> str:
        flow_bias = cls._flow_bias(flow_direction)
        if flow_bias != "NEUTRAL":
            return flow_bias
        if price and nearest_level:
            return "LONG" if price > nearest_level else "SHORT"
        if level_side == "RESISTANCE":
            return "LONG"
        if level_side == "SUPPORT":
            return "SHORT"
        return "NEUTRAL"

    @staticmethod
    def _decision_ladder(
        status: str,
        setup_type: str,
        bias: str,
        confidence: str,
        setup_quality: str,
        missing_conditions: list,
        fields: Dict[str, Any],
        has_execution_window: bool,
        expansion_ready: bool,
        cluster_ready: bool,
        near_level: bool,
    ) -> Dict[str, Any]:
        current_state = fields.get("current_state", "")
        execution_state = fields.get("execution_timing_state", "")
        event_type = fields.get("event_type", "")
        level_result = fields.get("level_result", "")
        nearest_level = fields.get("nearest_level")
        flow_direction = fields.get("short_term_flow_direction", "")

        # Environment quality label based on current_state
        env_map = {
            "REBALANCE":       "ACTIVE",
            "TRANSITION":      "ACTIVE (переход)",
            "EXPANSION":       "EXPANSION",
            "BREAKOUT_SETUP":  "BREAKOUT",
            "HEDGE_CHASE":     "HEDGE_CHASE",
            "EXHAUSTION":      "EXHAUSTION",
            "COMPRESSION":     "LOW_ACTIVITY",
            "PINNING":         "PINNED",
        }
        environment_label = env_map.get(current_state, current_state or "—")

        return {
            "environment":        {"state": current_state or "—", "quality": environment_label},
            "execution":          execution_state or "—",
            "event":              event_type or None,
            "level_reaction":     level_result or None,
            "nearest_level":      nearest_level,
            "flow":               flow_direction or "—",
            "price_confirmation": bool(level_result and level_result not in ("NO_REACTION", "NONE")),
            "final_decision":     status,
            # kept for debug — not shown in main ladder UI
            "_debug": {
                "setup_type": setup_type,
                "bias": bias,
                "confidence": confidence,
                "setup_quality": setup_quality,
                "missing_conditions": missing_conditions,
                "execution_window": has_execution_window,
                "expansion_probability_ready": expansion_ready,
                "signal_cluster_score_ready": cluster_ready,
                "near_level": near_level,
            },
        }
