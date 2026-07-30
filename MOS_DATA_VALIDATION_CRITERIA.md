# MOS_DATA_VALIDATION_CRITERIA.md

# MOS — Критерии проверки корректности сбора данных

## 1. Цель проверки

Цель проверки данных — понять, можно ли использовать `mos_research.db` и `history.db` для replay-анализа, проверки сигналов, вероятностного анализа и будущего Market Memory Engine.

MOS не предсказывает цену. MOS анализирует структуру рынка, режимы, дилерское позиционирование, волатильность, ликвидность, давление потоков и качество среды для исполнения.

---

## 2. Главное правило проверки файлов

Всегда использовать только последний загруженный комплект файлов.

Перед анализом явно перечислить файлы:

```text
mos_research(N).db
mos_research(N).db-wal
mos_research(N).db-shm

history(N).db
history(N).db-wal
history(N).db-shm
```

Старые базы не использовать для расчётов. Старые результаты можно упоминать только как контекстное сравнение: что стало лучше или хуже относительно прошлого теста.

Если номера файлов не совпадают, обязательно отметить это:

```text
mos_research: db/wal/shm имеют разные номера
history: db/wal/shm имеют разные номера
```

SQLite может открыться, но для чистой проверки лучше копировать комплект строго с одинаковым номером.

---

## 3. Какие файлы нужны

Для Research Layer:

```text
mos_research.db
mos_research.db-wal
mos_research.db-shm
```

Для slow structural archive:

```text
history.db
history.db-wal
history.db-shm
```

Лучше копировать после остановки backend или после SQLite checkpoint.

Если база в WAL mode и скопирован только `.db`, часть свежих данных может отсутствовать.

---

## 4. Главные повторяющиеся симптомы

```text
нет exclude_from_analysis / exclude_reason
нет data_quality_reason
liquidity_void_score залипает
signal_cluster_score около 30
execution_timing_state только WAIT
STRUCTURE UNSTABLE вместо STRUCTURE_UNSTABLE
EXECUTION_WINDOW и EXECUTION_WINDOW_OPEN одновременно
events snapshot_sequence_id = 0
active_sources = []
data_quality = 0.0
current_state = UNKNOWN
iv_velocity = 0.0 во всех snapshots
current_state снова PINNING 98–99%
```

Перед анализом сигналов надо доказать:

```text
1. backend запущен с правильной версией кода
2. ResearchLogger пишет в правильную mos_research.db
3. схема базы актуальная
4. новые snapshots создаются новой версией кода
```

---

## 5. Runtime-диагностика

В snapshots должны быть поля:

```text
code_version
research_schema_version
engine_patch_version
```

Проверка:

```sql
SELECT code_version, research_schema_version, engine_patch_version, COUNT(*)
FROM snapshots
GROUP BY code_version, research_schema_version, engine_patch_version;
```

Если этих полей нет или версия старая — данные нельзя считать валидными для новой проверки.

Endpoint:

```text
GET /api/research/diagnostics
```

Должен возвращать версии, пути DB, cwd, количество snapshots, latest snapshot timestamp, integrity_check, wal_mode, schema_valid, missing_columns и tables.

---

## 6. Обязательные таблицы

Проверка:

```sql
SELECT name FROM sqlite_master WHERE type='table';
```

Ожидаемые таблицы:

```text
snapshots
events
future_labels
bad_snapshots
bookmarks
event_fingerprint_cache
```

Минимально обязательные:

```text
snapshots
events
future_labels
bad_snapshots
```

---

## 7. Обязательные поля snapshots

Проверка:

```sql
PRAGMA table_info(snapshots);
```

Ожидаемые поля:

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

Если нет `exclude_from_analysis`, `exclude_reason`, `data_quality_reason`, `code_version` — схема не финальная.

---

## 8. Количество snapshots

Ожидаемо:

```text
1 час ≈ 180–240 snapshots
30 минут ≈ 90–120 snapshots
8 часов ≈ 1440–1920 snapshots
```

Проверка:

```sql
SELECT COUNT(*) FROM snapshots;
```

Если сильно меньше — проверить остановки backend. Если сильно больше — проверить, не пишется ли несколько логгеров параллельно.

---

## 9. current_state

Не должно быть `UNKNOWN`.

Правило:

```text
UNKNOWN → TRANSITION
```

Проверка:

```sql
SELECT current_state, COUNT(*)
FROM snapshots
WHERE exclude_from_analysis = 0
GROUP BY current_state;
```

Антисимптомы:

```text
PINNING 98–99% без объяснения
COMPRESSION 98–99% без объяснения
TRANSITION почти 0 на активном рынке
```

---

## 10. active_sources

Не должно быть пустым.

Ожидаемо:

```text
["bybit"]
["deribit", "bybit"]
```

Проверка:

```sql
SELECT active_sources, COUNT(*)
FROM snapshots
GROUP BY active_sources;
```

Если много `[]` — проблема с ExchangeHealthEngine / fallback.

---

## 11. data_quality

Должен быть текстом:

```text
GOOD
DEGRADED
PARTIAL
CRITICAL
```

Не должно быть `0.0`, `0`, `NULL`.

Проверка:

```sql
SELECT data_quality, COUNT(*)
FROM snapshots
GROUP BY data_quality;
```

Для `GOOD` желательно:

```text
data_quality_reason = "ok"
```

Проверка:

```sql
SELECT data_quality_reason, COUNT(*)
FROM snapshots
GROUP BY data_quality_reason
ORDER BY COUNT(*) DESC;
```

---

## 12. Нулевые и плохие значения

Критические значения:

```text
spot_price <= 0
oi_total <= 0
NaN / inf
invalid timestamp
corrupted gamma values
```

Они должны давать:

```text
exclude_from_analysis = 1
data_quality = CRITICAL
exclude_reason заполнен
```

Проверка:

```sql
SELECT COUNT(*) FROM snapshots WHERE spot_price <= 0;
SELECT COUNT(*) FROM snapshots WHERE oi_total <= 0;
SELECT COUNT(*) FROM snapshots WHERE atm_iv <= 0;
SELECT COUNT(*) FROM snapshots WHERE gamma_slope = 0 AND gamma_acceleration = 0;

SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis = 1;

SELECT exclude_reason, COUNT(*)
FROM snapshots
WHERE exclude_from_analysis = 1
GROUP BY exclude_reason;
```

---

## 13. iv_velocity

Критически важная метрика.

Проверка:

```sql
SELECT MIN(iv_velocity), AVG(iv_velocity), MAX(iv_velocity)
FROM snapshots
WHERE exclude_from_analysis = 0;

SELECT COUNT(*)
FROM snapshots
WHERE iv_velocity != 0
  AND exclude_from_analysis = 0;
```

Критический баг:

```text
iv_velocity min = 0
iv_velocity avg = 0
iv_velocity max = 0
```

Если `iv_velocity = 0.0` во всех snapshots, ломаются `VOLATILITY_EXPANSION`, `EXPANSION_CONFIRMING`, `STRUCTURE_UNSTABLE`, `EXECUTION_WINDOW_OPEN`, `signal_cluster_score`, `expansion_probability`.

Нужен endpoint:

```text
GET /api/research/volatility-debug/latest
```

Он должен показывать `atm_iv`, `previous_atm_iv`, `iv_delta`, `iv_velocity`, `iv_history_len`, `fallback_used`, `fallback_reason`, `source`, `written_to_market_state`, `written_to_research_logger`.

---

## 14. liquidity_void_score

Шкала 0–100.

Интерпретация:

```text
0–20    значимой пустоты нет
20–40   слабая структурная уязвимость
40–60   умеренная пустота
60–80   значимый коридор ускорения
80–100  экстремальная хрупкость
```

Проверка:

```sql
SELECT MIN(liquidity_void_score), AVG(liquidity_void_score), MAX(liquidity_void_score)
FROM snapshots
WHERE exclude_from_analysis = 0;
```

Проблемы:

```text
avg ~95 и p25 ~97 → метрика залипла на 100
max 3 → старая логика счётчика void-зон, не score 0–100
всё 20–40 → шкала слишком узкая
max < 50 на длинной истории → слабая диагностика void
```

Не поднимать score искусственно. Нужен `void-debug`, который объясняет низкий или высокий score.

---

## 15. signal_cluster_score

Шкала 0–100.

Проверка:

```sql
SELECT MIN(signal_cluster_score), AVG(signal_cluster_score), MAX(signal_cluster_score)
FROM snapshots
WHERE exclude_from_analysis = 0;
```

Проблемы:

```text
почти всегда 30 → залипший компонент
max 30 → score не использует все компоненты
max < 35 на активном рынке → upstream metrics слабые
```

---

## 16. execution_timing_state

Допустимые значения:

```text
WAIT
STRUCTURE_UNSTABLE
EXPANSION_CONFIRMING
HEDGE_CHASE_STARTING
EXECUTION_WINDOW_OPEN
```

Проверка:

```sql
SELECT execution_timing_state, COUNT(*)
FROM snapshots
WHERE exclude_from_analysis = 0
GROUP BY execution_timing_state;
```

Проблемы:

```text
WAIT 100% → engine слишком строгий или upstream inputs сломаны
STRUCTURE UNSTABLE → неправильный формат
EXECUTION_WINDOW → старый дубль
```

Endpoint:

```text
GET /api/research/execution-debug/latest
```

Должен объяснять `why_not`.

---

## 17. synthetic_flow_pressure

Шкала:

```text
-100 to +100
0 = neutral
```

Проверка:

```sql
SELECT MIN(synthetic_flow_pressure), AVG(synthetic_flow_pressure), MAX(synthetic_flow_pressure)
FROM snapshots
WHERE exclude_from_analysis = 0;
```

Проблемы:

```text
всегда 1–17 → слишком слабый flow
всегда sell-side → проверить bias
всегда buy-side → проверить bias
нет FLOW_SURGE на сильном импульсе → проверить flow-debug
```

Endpoint:

```text
GET /api/research/flow-debug/latest
```

---

## 18. Events

Проверка:

```sql
SELECT event_type, COUNT(*)
FROM events
GROUP BY event_type
ORDER BY COUNT(*) DESC;
```

Ожидаемые типы:

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

Проверка связей:

```sql
SELECT COUNT(*)
FROM events
WHERE snapshot_sequence_id IS NULL
   OR snapshot_sequence_id <= 0;
```

Проверка payload:

```sql
SELECT COUNT(*)
FROM events
WHERE event_payload_json IS NULL
   OR event_payload_json = '';
```

Проверка warmup:

```sql
SELECT timestamp_utc, snapshot_sequence_id, event_type, event_payload_json
FROM events
WHERE snapshot_sequence_id < 5
ORDER BY snapshot_sequence_id;
```

На первых snapshots не должно быть `FLOW_SURGE`, `VOLATILITY_EXPANSION`, `VOID_INTENSIFYING`, `VOID_DETECTED`, `VOID_CLEARED`.

---

## 19. Future labels

Проверка:

```sql
SELECT COUNT(*) FROM future_labels;

SELECT
  COUNT(future_return_5m),
  COUNT(future_return_15m),
  COUNT(future_return_30m),
  COUNT(future_max_up_30m),
  COUNT(future_max_down_30m),
  COUNT(future_realized_vol_30m),
  COUNT(future_range_30m),
  COUNT(future_breakout_strength),
  COUNT(snapshot_id),
  COUNT(snapshot_sequence_id)
FROM future_labels;
```

Проверка связи:

```sql
SELECT COUNT(*)
FROM future_labels fl
LEFT JOIN snapshots s ON fl.snapshot_id = s.snapshot_id
WHERE s.snapshot_id IS NULL;
```

Ожидаемо 0.

---

## 20. Оценочная шкала

```text
Сбор данных: 0–10
Схема Research Layer: 0–10
Replay пригодность: 0–10
Future labels: 0–10
Events quality: 0–10
Signal dynamics: 0–10
Probabilistic readiness: 0–10
Market Memory readiness: 0–10
```

---

## 21. Минимум для проверки, что сбор работает

```text
snapshots > 100
spot_price корректный
current_state не UNKNOWN
data_quality текстовый
active_sources не []
future_labels появляются после 30 минут
events появляются
runtime versions заполнены
```

---

## 22. Минимум для первичного replay

```text
snapshots > 500
future_labels > 300
events > 10
liquidity_void_score не залипает
signal_cluster_score не залипает
execution_timing_state не только WAIT
event snapshot_sequence_id > 0
exclude_from_analysis работает
iv_velocity не 0 во всех snapshots
```

---

## 23. Минимум для вероятностного анализа

```text
3–7 дней чистой истории
несколько разных current_state
future_labels заполнены
bad/excluded snapshots объяснимы
events разнообразные
liquidity_void_score распределён
signal_cluster_score распределён
execution_timing_state распределён
нет UNKNOWN
нет data_quality = 0.0
нет active_sources = []
iv_velocity работает
```

---

## 24. Для Market Memory

```text
2–4 недели истории
стабильная схема
schema_version/code_version сохранены
market_phase_hash стабилен
future_labels заполнены
события нормализованы
режимы разнообразны
phase_context сохранён
event_payload_json заполнен
```

---

## 25. После чистой проверки

Если после 30–60 минут schema актуальная, versions правильные, current_state не UNKNOWN, active_sources не [], data_quality текстовый, exclude_from_analysis работает, iv_velocity работает, liquidity_void_score и signal_cluster_score не залипают, execution_timing_state не только WAIT на активном рынке, events нормализованы и future_labels появляются — можно копить 3–7 дней.

Если критерии не выполнены:

```text
не копить длинную историю,
сначала исправлять сбор / логику.
```
