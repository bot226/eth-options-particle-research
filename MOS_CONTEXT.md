# MOS_CONTEXT.md

# MOS — Institutional Market Structure Intelligence System

## 1. Что такое MOS

MOS — Market Operating System для анализа структуры рынка ETH options.

MOS не предсказывает цену. MOS анализирует рыночную структуру: текущее состояние рынка, режим, переход режима, дилерское позиционирование, Gamma / GEX, IV / skew / term structure, ликвидность, synthetic flow pressure, вероятность расширения диапазона, качество среды для исполнения, события для replay, future_labels и основу для будущего Market Memory.

Главная цель MOS: понять, что рынок делает сейчас, во что он превращается, кто контролирует рынок, где структура хрупкая, где возможен выход из диапазона и когда среда становится пригодной для исполнения.

MOS должен быть не обычным crypto dashboard, а:

```text
institutional market structure intelligence terminal
+ historical replay laboratory
+ future Market Memory foundation
```

---

## 2. Главная философия

Ключевые принципы:

```text
Strict single-responsibility engines
READ-ONLY intelligence layers
Replay-first architecture
Market structure, not price prediction
Deterministic historical replay
Future probabilistic analysis support
Backend owns business logic
Frontend only renders
```

Фронтенд не должен считать режимы, confidence, squeeze risk, execution quality или делать бизнес-логику. Фронтенд должен отображать `market_state`, события, replay и визуальные слои.

---

## 3. Главный backend contract

Основной объект:

```python
market_state = {
    "timestamp": "...",
    "state_machine": {
        "current_state": "...",
        "previous_state": "...",
        "candidate_state": "...",
        "transition_state": "...",
        "transition_speed": "...",
        "state_persistence_sec": "...",
        "regime_stability": "..."
    },
    "gamma": {},
    "volatility": {},
    "skew": {},
    "liquidity": {},
    "flow": {},
    "meta": {},
    "scenario": {},
    "execution": {},
    "events": []
}
```

Дополнительный контейнер:

```python
"advanced_intelligence": {
    "status": "experimental",
    "phase_1": {
        "gamma_surface": {},
        "liquidity_voids": {},
        "dealer_hedging": {},
        "regime_transition": {}
    },
    "phase_2": {
        "term_structure": {},
        "synthetic_orderflow": {},
        "breakout_timing": {},
        "execution_timing": {}
    }
}
```

`advanced_intelligence` — READ-ONLY слой. Он считает, объясняет и оценивает, но не должен напрямую менять `current_state`, `bias`, `execution_quality` или trading logic.

---

## 4. State Machine

Формальная модель состояний:

```python
class MarketState(Enum):
    PINNING = "PINNING"
    COMPRESSION = "COMPRESSION"
    TRANSITION = "TRANSITION"
    BREAKOUT_SETUP = "BREAKOUT_SETUP"
    HEDGE_CHASE = "HEDGE_CHASE"
    EXPANSION = "EXPANSION"
    SHORT_SQUEEZE = "SHORT_SQUEEZE"
    LONG_LIQUIDATION = "LONG_LIQUIDATION"
    EXHAUSTION = "EXHAUSTION"
    REBALANCE = "REBALANCE"
    PANIC = "PANIC"
```

`UNKNOWN` может оставаться для совместимости, но активно ставиться не должен.

Правило:

```text
UNKNOWN → TRANSITION
```

Начальное состояние:

```python
_current_state = MarketState.TRANSITION
```

Смысл State Machine: не только что рынок делает, а во что он переходит.

---

## 5. Основные движки MOS

Базовые движки:

```text
gamma_engine.py
volatility_engine.py
skew_engine.py
liquidity_engine.py
flow_engine.py
meta_state_engine.py
scenario_engine.py
execution_engine.py
state_engine.py
narrative_engine.py
```

`state_engine.py` — только оркестратор. Он не должен считать сырые метрики, содержать огромные эвристики или становиться god file. Он должен собирать outputs движков, поддерживать временную память, вести State Machine, создавать events и строить unified `market_state`.

---

## 6. Temporal Memory

```python
market_state_history = deque(maxlen=500)
```

Нужна для state persistence, transition velocity, regime deterioration, volatility velocity, skew acceleration, gamma instability, pressure buildup, phase_context и market_phase_hash.

Главная идея:

```text
transition важнее snapshot.
```

---

## 7. Market Intelligence / Narrative Layer

Верхний блок Dashboard:

```text
MARKET INTELLIGENCE SUMMARY / Рыночный анализ
```

Он должен синтезировать данные всех движков в человеческий текст.

Плохой стиль:

```text
POSITIVE_GAMMA
BALANCED
PINNED_TO_STRIKE
NONE
```

Хороший стиль:

```text
Дилеры подавляют волатильность.
Рынок удерживается возле ключевых страйков.
Вероятность выхода из диапазона снижена.
```

Narrative должен объяснять почему:

```text
Риск расширения растёт из-за ослабления гамма-поддержки дилеров,
роста скорости IV и появления пустоты ликвидности выше рынка.
```

Пять вопросов Market Intelligence:

```text
Кто контролирует рынок?
Волатильность расширяется или подавлена?
Может ли цена выйти из диапазона?
Структура устойчива или хрупкая?
Среда пригодна для исполнения?
```

---

## 8. Dashboard panels

```text
1. Market Intelligence Summary
2. State Machine
3. Dealer Positioning / Gamma
4. Volatility Regime
5. Flow Pressure
6. Meta State
7. Scenario Engine
8. Execution Summary
```

Dashboard отвечает: что происходит прямо сейчас. Deep analytics — отдельные вкладки.

---

## 9. Volatility Regime

Поля блока:

```text
STATE
ATM IV
TERM STRUCTURE
IV VELOCITY
EXPANSION RISK
EVENT RISK
```

Важно: `IV velocity` важнее просто IV.

Если `iv_velocity = 0.0` во всех snapshots — это критический баг, потому что ломаются `VOLATILITY_EXPANSION`, `EXPANSION_CONFIRMING`, `STRUCTURE_UNSTABLE`, `EXECUTION_WINDOW_OPEN`, `signal_cluster_score` и `expansion_probability`.

---

## 10. Gamma / Dealer logic

Для crypto options используется crypto-native GEX:

```text
GEX = gamma × OI × spot
```

Не использовать equity-style формулу:

```text
gamma × OI × spot² × 0.01
```

GEX нужно агрегировать по expiries с DTE weighting:

```text
1 / sqrt(DTE)
```

При multi-exchange aggregation OI и GEX суммируются, а interpretive metrics взвешиваются.

---

## 11. Pinning / Call Wall / Put Wall

Pinning означает:

```text
цена удерживается возле крупного страйка,
positive gamma подавляет волатильность,
дилеры гасят импульсы.
```

Но `POSITIVE_GAMMA` само по себе не равно `PINNING`.

Правильная логика:

```text
POSITIVE_GAMMA + near wall + low IV velocity + weak flow + low expansion risk = PINNING
POSITIVE_GAMMA + gamma weakening + rising IV/flow + rising transition pressure = TRANSITION
```

---

## 12. Flow Pressure

Пока нет настоящего footprint / tape / orderflow.

Правильно использовать:

```text
Flow Pressure
Synthetic Flow Pressure
Расчётное давление потока
Синтетическое давление
```

Текущая шкала:

```text
synthetic_flow_pressure_scale = "-100_to_100_neutral_0"
```

Где:

```text
-100 = сильное sell pressure
0 = neutral
+100 = сильное buy pressure
```

---

## 13. Execution Summary

Execution не должен быть retail-сигналом.

Нельзя:

```text
BUY NOW
SELL NOW
```

ExecutionTimingEngine состояния:

```text
WAIT
STRUCTURE_UNSTABLE
EXPANSION_CONFIRMING
HEDGE_CHASE_STARTING
EXECUTION_WINDOW_OPEN
```

ExecutionTimingEngine — READ-ONLY observer.

Запрещено:

```text
execution_timing_state -> current_state
```

Разрешено:

```text
raw structural metrics -> current_state
current_state -> execution context
```

---

## 14. Research Layer

Research Layer — отдельная вкладка:

```text
frontend/src/pages/research.js
```

Принцип UI:

```text
PRICE ABOVE
INTELLIGENCE BELOW
```

Основные слои:

```text
ETH Price Chart
Expansion Probability
Gamma Slope
Liquidity Void Score
Dealer Hedging Pressure
Synthetic Flow Pressure
IV Velocity
Execution Timing State
Regime Transition Probability
Term Structure State
```

Event markers:

```text
REGIME_CHANGE
EXECUTION_WINDOW_OPEN
VOID_DETECTED
GAMMA_COLLAPSE
FLOW_SURGE
VOLATILITY_EXPANSION
PINNING_BREAK
STRUCTURE_UNSTABLE
```

Для core replay использовать `lightweight-charts`.

---

## 15. Базы данных

Две базы:

```text
history.db
mos_research.db
```

`history.db` — медленная структурная база, интервал примерно 5 минут.

`mos_research.db` — быстрая исследовательская база, интервал примерно 15–20 секунд.

Смысл:

```text
history.db = что было на рынке
mos_research.db = что думала система в тот момент
```

---

## 16. Обязательные поля snapshots

```text
snapshot_id
snapshot_sequence_id
timestamp_utc
schema_version
code_version
research_schema_version
engine_patch_version
spot_price
current_state
previous_state
candidate_state
transition_state
regime_duration_sec
global_confidence
data_quality
data_quality_reason
active_sources
net_gex
gamma_regime
call_wall
put_wall
atm_iv
iv_velocity
term_structure_state
oi_total
volume_total
gamma_slope
gamma_acceleration
gamma_slope_state
gamma_acceleration_state
liquidity_void_score
dealer_hedging_pressure
synthetic_flow_pressure
synthetic_flow_pressure_scale
expansion_probability
compression_failure_risk
execution_timing_state
breakout_window
signal_cluster_score
market_phase_hash
exclude_from_analysis
exclude_reason
```

---

## 17. Events pipeline

События:

```text
REGIME_CHANGE
BREAKOUT_ALERT
FLOW_SURGE
VOID_DETECTED
VOID_INTENSIFYING
VOID_CLEARED
GAMMA_WEAKENING
GAMMA_COLLAPSE
EXECUTION_WINDOW_OPEN
VOLATILITY_EXPANSION
PINNING_BREAK
STRUCTURE_UNSTABLE
```

События должны иметь:

```text
event_payload_json
previous_state
current_state
previous_execution_timing_state
current_execution_timing_state
snapshot_sequence_id > 0
```

---

## 18. Market Phase Hash

Hash должен быть deterministic and stable.

Не включать:

```text
timestamp
spot price
random floats
raw volatile values
```

Включать:

```text
current_state
phase_context
gamma_slope_state
gamma_acceleration_state
execution_timing_state
vol_bucket
flow_bucket
void_bucket
expansion_bucket
dealer_hedging_pressure
```

---

## 19. Phase Context

Не добавлять новые значения в `MarketState enum`.

Использовать отдельный `phase_context`:

```text
NORMAL_PINNING
PINNING_AFTER_IMPULSE
PINNING_WEAKENING
POST_IMPULSE_COMPRESSION
RANGE_STABILIZATION
COMPRESSION_AFTER_SELL_OFF
TRANSITION_PRESSURE
DOWNTREND_COMPRESSION
SELL_PRESSURE_COMPRESSION
WEAK_RECOVERY_FAILURE
RANGE_COMPRESSION
LIQUIDITY_SWEEP_DOWN
RANGE_RECLAIM
UPSIDE_EXPANSION
POST_EXPANSION_CONSOLIDATION
```

`phase_context` должен попадать в:

```text
state-debug
market_phase_hash
event_payload_json
```

---

## 20. Текущий главный приоритет

Последний известный регресс v12:

```text
iv_velocity = 0.0 во всех snapshots
current_state почти весь PINNING
execution_timing_state = WAIT 100%
FLOW_SURGE отсутствует
VOLATILITY_EXPANSION отсутствует
STRUCTURE_UNSTABLE отсутствует
EXECUTION_WINDOW_OPEN отсутствует
signal_cluster_score слабый
expansion_probability слабая
```

Главный следующий fix:

```text
v13 regression fix:
1. восстановить iv_velocity;
2. проверить State Machine pinning stickiness;
3. проверить execution-debug и WAIT 100%;
4. проверить flow-debug на импульсе;
5. проверить phase_context_debug;
6. не менять рабочие схемы и future_labels.
```

---

## 21. Главный рабочий принцип

Нельзя копить длинную историю, если:

```text
iv_velocity = 0.0 во всех snapshots
current_state = PINNING 98–99%
execution_timing_state = WAIT 100%
active_sources = []
data_quality = 0.0
future_labels пустые
events пустые или однобокие
liquidity_void_score залипает
signal_cluster_score залипает
```

Если критерии не выполнены:

```text
не копить историю,
сначала исправлять сбор / логику.
```
