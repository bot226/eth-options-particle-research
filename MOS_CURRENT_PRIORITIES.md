# MOS_CURRENT_PRIORITIES.md

# MOS — Текущие приоритеты разработки

## 1. Текущее состояние

MOS уже прошёл несколько этапов стабилизации Research Layer.

Что уже в целом работает:

```text
schema_version = 2.0
runtime versions пишутся
snapshot_id пишется
future_labels связаны через snapshot_id / snapshot_sequence_id
event_payload_json в основном работает
data_quality текстовый
active_sources заполняется
exclude_from_analysis работает
market_phase_hash работает
phase_context появился
warmup suppression частично работает
```

Последний тест v12 показал аналитический откат:

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
liquidity_void_score слабый
```

Главный вывод:

```text
Сейчас нельзя копить 3–7 дней.
Сначала нужно исправить v12 regression.
```

---

## 2. Главный текущий приоритет

Главная задача:

```text
v13 regression fix:
восстановить IV velocity и оживить upstream-сигналы.
```

Почему это первое:

Если `iv_velocity = 0.0`, то ломаются:

```text
VOLATILITY_EXPANSION
EXPANSION_CONFIRMING
STRUCTURE_UNSTABLE
EXECUTION_WINDOW_OPEN
signal_cluster_score
expansion_probability
transition pressure
phase_context UPSIDE_EXPANSION
```

Поэтому нельзя начинать с изменения thresholds. Сначала нужно восстановить цепочку:

```text
exchange/options data
→ volatility_engine
→ market_state.volatility
→ state_engine
→ research_logger
→ snapshots.iv_velocity
```

---

## 3. Приоритет №1 — IV Velocity

### Проблема

Последний тест:

```text
iv_velocity min = 0.0
iv_velocity avg = 0.0
iv_velocity max = 0.0
```

### Проверить

```text
1. Приходит ли atm_iv.
2. Меняется ли atm_iv во времени.
3. Сохраняется ли previous_atm_iv.
4. Есть ли rolling history для IV.
5. Считается ли iv_velocity после обновления истории или до.
6. Не сбрасывается ли history каждый snapshot.
7. Не обнуляется ли iv_velocity fallback-логикой.
8. Не теряется ли поле при сборке market_state.
9. Не переименовано ли поле после v12.
10. Не пишет ли ResearchLogger не то поле.
```

### Endpoint

```text
GET /api/research/volatility-debug/latest
```

Должен возвращать:

```json
{
  "atm_iv": 42.1,
  "previous_atm_iv": 41.8,
  "iv_delta": 0.3,
  "iv_velocity": 0.7,
  "iv_history_len": 42,
  "iv_history_last_values": [41.6, 41.8, 42.1],
  "calculation_mode": "rolling_delta",
  "fallback_used": false,
  "fallback_reason": null,
  "source": "volatility_engine",
  "written_to_market_state": true,
  "written_to_research_logger": true
}
```

### Acceptance

```sql
SELECT MIN(iv_velocity), AVG(iv_velocity), MAX(iv_velocity)
FROM snapshots
WHERE exclude_from_analysis = 0;

SELECT COUNT(*)
FROM snapshots
WHERE iv_velocity != 0
  AND exclude_from_analysis = 0;
```

Ожидаемо:

```text
iv_velocity не должен быть 0 во всех snapshots.
```

---

## 4. Приоритет №2 — State Machine pinning stickiness

### Проблема

Последний тест:

```text
PINNING: 98.9%
COMPRESSION: 0.9%
TRANSITION: 0.2%
```

Это откат к старой проблеме.

### Проверить

```text
pinning_score
transition_score
pinning_break_factors
normal_break
pressure_break
delta_break
candidate_state
candidate_persistence_updates
required_persistence
phase_context
hysteresis guard
PERSISTENCE_RULES
```

### Не делать

Не возвращать хаос `PINNING ↔ TRANSITION`. Не отменять hysteresis вслепую. Сначала понять, почему raw-state почти всегда выбирает `PINNING`.

### Нужен endpoint

```text
GET /api/research/state-debug/latest
GET /api/research/state-debug/summary?from=...&to=...
```

Summary должен показывать:

```text
state_distribution
avg_pinning_score
avg_transition_score
avg_pinning_break_factors
candidate_state_distribution
blocked_transitions
phase_context_distribution
why_not_transition
why_not_compression
```

### Acceptance

Не должно быть:

```text
PINNING 98–99%
```

если рынок имел range compression, sweep, reclaim, upside impulse, post-expansion consolidation.

---

## 5. Приоритет №3 — Execution WAIT 100%

### Проблема

```text
execution_timing_state = WAIT 100%
```

### Вероятная причина

Upstream:

```text
iv_velocity = 0
signal_cluster_score низкий
expansion_probability низкая
synthetic_flow_pressure слабый
liquidity_void_score низкий
```

### Не делать

Не снижать thresholds вслепую. Сначала восстановить `iv_velocity`.

### Endpoint

```text
GET /api/research/execution-debug/latest
```

Должен показывать scores, why_not и inputs.

### Acceptance

```sql
SELECT execution_timing_state, COUNT(*)
FROM snapshots
WHERE exclude_from_analysis = 0
GROUP BY execution_timing_state;
```

На активном рынке `WAIT` может преобладать, но не должен быть 100%.

---

## 6. Приоритет №4 — Flow too weak

### Проблема

```text
synthetic_flow_pressure min = 1.2
avg = 9.26
max = 17.4
```

Flow всегда слабый BUY. Нет SELL. Нет FLOW_SURGE.

### Не делать

Не менять формулу вслепую. Сначала смотреть debug.

### Endpoint

```text
GET /api/research/flow-debug/latest
GET /api/research/flow-debug/summary?from=...&to=...
```

Должен показывать:

```text
synthetic_flow_pressure
flow_scale
flow_intensity
price_velocity_input
oi_delta_input
iv_velocity_input
volume_acceleration_input
buying_components
selling_components
raw_buying_score
raw_selling_score
flow_momentum_score
reason
```

### Возможные причины

```text
price_velocity слишком сглажен
breakout_momentum не считается
recovery_momentum не считается
iv_velocity = 0 подавляет IV component
volume acceleration не используется
flow_mom clamp слишком узкий
flow formula использует старый baseline
```

### Acceptance

На рынке с реальным импульсом synthetic_flow_pressure должен иметь возможность кратковременно усиливаться выше 20–35.

---

## 7. Приоритет №5 — Volatility / Flow / Execution events

После восстановления upstream-метрик должны снова появиться:

```text
VOLATILITY_EXPANSION
FLOW_SURGE
STRUCTURE_UNSTABLE
EXECUTION_WINDOW_OPEN
EXPANSION_CONFIRMING
```

Не менять thresholds первым делом.

`VOLATILITY_EXPANSION` должен оставаться crossing-event:

```text
previous_iv_velocity < 2.0
current_iv_velocity >= 2.0
```

`FLOW_SURGE` должен оставаться crossing-event:

```text
previous_flow_intensity < 35
current_flow_intensity >= 35
```

`EXECUTION_WINDOW_OPEN` должен появляться только при входе в состояние:

```text
previous_execution_timing_state != EXECUTION_WINDOW_OPEN
current_execution_timing_state == EXECUTION_WINDOW_OPEN
```

---

## 8. Приоритет №6 — phase_context debug

### Проблема

Последний тест показал phase_context в payload, но почти нет:

```text
RANGE_RECLAIM
LIQUIDITY_SWEEP_DOWN
UPSIDE_EXPANSION
TRANSITION_PRESSURE
```

### Проверить priority order

Правильный порядок:

```text
1. RANGE_RECLAIM
2. LIQUIDITY_SWEEP_DOWN
3. UPSIDE_EXPANSION
4. POST_EXPANSION_CONSOLIDATION
5. RANGE_COMPRESSION
6. existing contexts
```

### UPSIDE_EXPANSION

Не должен зависеть только от `flow_mom > 0`.

```python
upside_expansion = (
    recent_return_5m > 0.3
    and expansion_probability >= 50
    and (
        flow_mom > 0
        or signal_cluster_score >= 50
        or iv_velocity >= 2.0
    )
)
```

Но сейчас `iv_velocity = 0`, `expansion_probability` низкая, `signal_cluster_score` низкий. Поэтому сначала чинить upstream.

### Endpoint

```text
GET /api/research/state-debug/latest
```

Должен показывать `phase_context_debug`.

---

## 9. Приоритет №7 — LiquidityVoidEngine

Текущая проблема:

```text
liquidity_void_score 20–40
max = 33.5
```

Но сейчас это не первый fix. Сначала восстановить:

```text
iv_velocity
flow
expansion_probability
state transitions
execution states
```

После этого снова смотреть void.

Нельзя поднимать score искусственно.

Проверить:

```text
GET /api/research/void-debug/latest
```

---

## 10. Приоритет №8 — Gamma classification sanity

Gamma events работают, payload есть.

Но проверить странность:

```text
gamma_slope положительный, а state = weakening
```

Это может быть нормально, если `weakening` означает относительное ухудшение по z-score.

Нужен debug:

```json
{
  "gamma_slope": 0.0202,
  "gamma_slope_state": "weakening",
  "classification_reason": "z_score_deterioration",
  "rolling_mean": 0.034,
  "rolling_std": 0.006,
  "z_score": -2.1
}
```

---

## 11. Что сейчас НЕ делать

Не начинать:

```text
Market Memory
ML
new dashboard redesign
multi-exchange redesign
frontend refactor
new indicators
new strategy layer
autotrading
```

Пока задача только:

```text
починить v12 regression.
```

---

## 12. Порядок выполнения

```text
1. Update version → v13.
2. Diagnose/fix iv_velocity.
3. Add/verify volatility-debug/latest.
4. Run short test 15–30 minutes.
5. If iv_velocity alive, check state distribution.
6. Diagnose pinning stickiness via state-debug.
7. Diagnose WAIT 100% via execution-debug.
8. Diagnose flow weakness via flow-debug.
9. Only then decide whether thresholds need adjustment.
10. Run clean 30–60 min test.
11. Upload db + wal + shm.
12. Analyze.
```

Не делать всё одним большим diff.

---

## 13. Acceptance SQL

```sql
PRAGMA integrity_check;
PRAGMA quick_check;

SELECT COUNT(*) FROM snapshots;
SELECT COUNT(*) FROM future_labels;
SELECT COUNT(*) FROM events;
SELECT COUNT(*) FROM bad_snapshots;

SELECT code_version, research_schema_version, engine_patch_version, COUNT(*)
FROM snapshots
GROUP BY code_version, research_schema_version, engine_patch_version;

SELECT current_state, COUNT(*)
FROM snapshots
WHERE exclude_from_analysis = 0
GROUP BY current_state;

SELECT execution_timing_state, COUNT(*)
FROM snapshots
WHERE exclude_from_analysis = 0
GROUP BY execution_timing_state;

SELECT MIN(iv_velocity), AVG(iv_velocity), MAX(iv_velocity)
FROM snapshots
WHERE exclude_from_analysis = 0;

SELECT COUNT(*)
FROM snapshots
WHERE iv_velocity != 0
  AND exclude_from_analysis = 0;

SELECT MIN(synthetic_flow_pressure), AVG(synthetic_flow_pressure), MAX(synthetic_flow_pressure)
FROM snapshots
WHERE exclude_from_analysis = 0;

SELECT MIN(expansion_probability), AVG(expansion_probability), MAX(expansion_probability)
FROM snapshots
WHERE exclude_from_analysis = 0;

SELECT MIN(signal_cluster_score), AVG(signal_cluster_score), MAX(signal_cluster_score)
FROM snapshots
WHERE exclude_from_analysis = 0;

SELECT MIN(liquidity_void_score), AVG(liquidity_void_score), MAX(liquidity_void_score)
FROM snapshots
WHERE exclude_from_analysis = 0;

SELECT event_type, COUNT(*)
FROM events
GROUP BY event_type
ORDER BY COUNT(*) DESC;

SELECT COUNT(*)
FROM events
WHERE event_payload_json IS NULL
   OR event_payload_json = '';

SELECT COUNT(*)
FROM events
WHERE snapshot_sequence_id < 5
  AND event_type IN ('FLOW_SURGE', 'VOLATILITY_EXPANSION', 'VOID_INTENSIFYING', 'VOID_DETECTED', 'VOID_CLEARED');

SELECT COUNT(DISTINCT market_phase_hash)
FROM snapshots
WHERE exclude_from_analysis = 0;
```

---

## 14. Критерии успеха v13

```text
iv_velocity больше не 0 во всех snapshots
current_state не PINNING 98–99%
execution_timing_state не WAIT 100% на активном рынке
expansion_probability снова имеет возможность выйти выше 40–50
signal_cluster_score снова имеет возможность выйти выше 40–50
synthetic_flow_pressure не зажат только в 1–17
FLOW_SURGE может появиться при реальном crossing
VOLATILITY_EXPANSION может появиться при реальном IV crossing
phase_context показывает не только RANGE_COMPRESSION / NORMAL_PINNING
event_payload_json заполнен
future_labels работают
warmup events на snapshot < 5 отсутствуют
```

---

## 15. Критерии неуспеха

```text
iv_velocity снова 0.0 во всех snapshots
PINNING снова 98–99%
WAIT снова 100%
expansion_probability max < 35 на активном рынке
signal_cluster_score max < 35 на активном рынке
synthetic_flow_pressure остаётся в узком 1–17 без объяснения
нет VOLATILITY_EXPANSION при росте IV
нет FLOW_SURGE при сильном импульсе
flow-debug / volatility-debug / execution-debug не объясняют причины
future_labels сломались
event_payload_json пустой
warmup events снова появились на snapshot 2
```

---

## 16. Когда можно копить историю

Копить 3–7 дней можно только если после короткого теста:

```text
iv_velocity работает
State Machine не залипает
Execution не WAIT 100% на активном рынке
events разнообразные
future_labels работают
payload заполнен
data_quality корректный
active_sources не пустые
```

Если нет — не копить длинную историю, сначала исправлять.

---

## 17. Финальный смысл

Сейчас главный вопрос не в Market Memory и не в новых фичах.

Главный вопрос:

```text
почему v12 потерял динамику.
```

Приоритет:

```text
iv_velocity → State Machine → Execution → Flow → Phase Context → Liquidity Void
```

Пока `iv_velocity = 0.0`, всё остальное будет выглядеть мёртвым.

Первая задача для Codex/исполнителя:

```text
Найди и исправь причину, почему iv_velocity всегда 0.0.
```
