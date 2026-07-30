# MOS_CODEX_RULES.md

# MOS — Правила работы с Codex

## 1. Роль Codex

Codex — не автономный архитектор MOS.

Codex — кодовый помощник для маленьких проверяемых задач.

Он должен:

- читать код;
- находить причину конкретного бага;
- вносить минимальный diff;
- объяснять, что изменил;
- не трогать рабочие части без разрешения;
- давать план проверки.

Он не должен:

- переписывать архитектуру;
- делать большой refactor;
- менять несколько подсистем сразу;
- подгонять метрики под желаемый результат;
- добавлять trading logic;
- менять смысл MOS.

---

## 2. Главная философия работы

Одна задача = один баг = один маленький diff.

Плохо:

```text
Исправь MOS, чтобы всё работало.
```

Хорошо:

```text
Исправь только iv_velocity = 0.0.
Не трогай State Machine, ExecutionTimingEngine, ResearchLogger schema и future_labels.
```

---

## 3. Общие ограничения для Codex

Всегда соблюдать:

```text
Strict single-responsibility engines
READ-ONLY intelligence layers
Replay-first architecture
Backend owns business logic
Frontend only renders
```

Запрещено:

- превращать `state_engine.py` в god file;
- переносить бизнес-логику во frontend;
- менять `MarketState enum` без явного разрешения;
- менять schema snapshots без явного разрешения;
- менять flow scale;
- менять future_labels;
- удалять или пересоздавать таблицы;
- удалять WAL settings;
- подгонять thresholds без debug-доказательства;
- добавлять BUY/SELL сигналы.

---

## 4. Перед началом любой задачи

Codex должен сначала сделать READ-ONLY анализ.

Шаблон:

```text
Сначала ничего не изменяй.

Найди файлы и функции, связанные с задачей.
Опиши:
1. где считается метрика;
2. где она передаётся в market_state;
3. где она пишется в ResearchLogger;
4. где она используется в events / execution / state;
5. какие возможные причины бага;
6. какие файлы потребуется менять.

После этого остановись и предложи минимальный план.
```

---

## 5. Обязательный формат ответа Codex после изменения

После любого diff Codex должен ответить:

```text
1. Какие файлы изменены.
2. Какие функции изменены.
3. Почему изменены именно они.
4. Что НЕ трогалось.
5. Как проверить.
6. Какие SQL-запросы выполнить.
7. Какие риски остались.
```

---

## 6. Что нельзя трогать без отдельного разрешения

Нельзя трогать:

```text
future_labels
future_label_worker
ResearchLogger schema
MarketState enum
synthetic_flow_pressure_scale
snapshot_id logic
schema_version
research_schema_version
event_payload_json schema
SQLite WAL settings
frontend business logic
Market Memory
ML logic
autotrading
```

---

## 7. Файлы, которые Codex должен знать

Ключевые backend-файлы:

```text
backend/engine/state_engine.py
backend/engine/volatility_engine.py
backend/engine/gamma_surface_engine.py
backend/engine/liquidity_void_engine.py
backend/engine/synthetic_orderflow_engine.py
backend/engine/execution_timing_engine.py
backend/engine/dealer_hedging_engine.py
backend/engine/regime_transition_engine.py
backend/engine/research_logger.py
backend/routes/research.py
backend/engine/version.py
backend/config/version.py
backend/main.py
```

Codex должен определить, какой version file реально используется, и не создавать второй источник правды.

---

## 8. Первый режим работы Codex

Первый запрос Codex должен быть READ-ONLY.

```text
Ты работаешь с проектом MOS — Institutional Market Structure Intelligence System для BTC options.

Сейчас ничего не изменяй.

Твоя задача:
1. Проанализировать структуру backend.
2. Найти файлы:
   - state_engine.py
   - volatility_engine.py
   - synthetic_orderflow_engine.py
   - liquidity_void_engine.py
   - research_logger.py
   - routes/research.py
   - version.py
3. Объяснить, где считается:
   - iv_velocity
   - current_state
   - execution_timing_state
   - liquidity_void_score
   - synthetic_flow_pressure
   - event_payload_json
4. Найти возможные причины, почему в последнем тесте:
   - iv_velocity = 0.0 во всех snapshots
   - current_state почти весь PINNING
   - execution_timing_state = WAIT 100%
   - FLOW_SURGE / VOLATILITY_EXPANSION отсутствуют
5. Не менять файлы.
6. В конце дать список конкретных файлов и функций, которые нужно проверить.
```

---

## 9. Первая реальная задача для Codex

Начинать с:

```text
iv_velocity = 0.0 во всех snapshots
```

Промпт:

```text
Задача: найти и исправить причину, почему iv_velocity всегда 0.0 в snapshots.

Контекст:
В последнем тесте MOS:
- schema_version = 2.0
- code_version = research_fix_2026_05_20_v12
- snapshots = 665
- iv_velocity min/avg/max = 0.0
- execution_timing_state = WAIT 100%
- VOLATILITY_EXPANSION events отсутствуют

Ограничения:
- Не менять ResearchLogger schema.
- Не менять future_labels.
- Не менять MarketState enum.
- Не менять thresholds.
- Не переписывать VolatilityEngine полностью.
- Не менять ExecutionTimingEngine scoring.
- Не менять State Machine logic.

Сначала найти причину по цепочке:
exchange/options data → volatility_engine → market_state.volatility → state_engine → ResearchLogger → snapshots.iv_velocity.

Что нужно сделать:
1. Найти, где считается iv_velocity.
2. Проверить, обновляется ли история ATM IV.
3. Проверить, не сбрасывается ли history каждый snapshot.
4. Проверить, не считается ли velocity до обновления предыдущего значения.
5. Проверить, не теряется ли поле при сборке market_state.
6. Добавить debug endpoint:
   GET /api/research/volatility-debug/latest
7. Endpoint должен возвращать:
   atm_iv,
   previous_atm_iv,
   iv_delta,
   iv_velocity,
   iv_history_len,
   fallback_used,
   fallback_reason,
   source,
   written_to_market_state.
8. Внести минимальный fix.
9. Не менять другие движки.
10. Дать diff и краткое объяснение.
```

---

## 10. Порядок задач MOS для Codex

Текущий порядок:

```text
1. iv_velocity = 0.0
2. current_state снова PINNING 98–99%
3. execution_timing_state = WAIT 100%
4. synthetic_flow_pressure слишком слабый
5. FLOW_SURGE / VOLATILITY_EXPANSION отсутствуют
6. EXECUTION_WINDOW_OPEN event
7. phase_context debug
8. LiquidityVoidEngine / void-debug
```

Не делать все задачи сразу.

---

## 11. Правила для iv_velocity fix

Нельзя:

- снижать thresholds;
- подменять `iv_velocity` случайным значением;
- считать velocity от spot price;
- писать fake IV movement;
- менять execution scoring.

Нужно:

- найти источник `atm_iv`;
- проверить историю IV;
- проверить порядок обновления previous/current;
- проверить передачу в `market_state`;
- проверить запись в `snapshots.iv_velocity`.

Acceptance:

```sql
SELECT MIN(iv_velocity), AVG(iv_velocity), MAX(iv_velocity)
FROM snapshots
WHERE exclude_from_analysis = 0;

SELECT COUNT(*)
FROM snapshots
WHERE iv_velocity != 0
  AND exclude_from_analysis = 0;
```

---

## 12. Правила для State Machine fix

Если `PINNING = 98–99%`, нельзя сразу ломать PINNING.

Нужно сначала проверить:

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
```

Неуспех:

```text
PINNING 98–99%
COMPRESSION 98–99%
TRANSITION почти 0
```

Цель: режимы должны отражать реальные фазы рынка, а не быть искусственно распределёнными.

---

## 13. Правила для Execution fix

Если `WAIT = 100%`, нельзя сразу снижать пороги.

Сначала проверить upstream:

```text
iv_velocity
expansion_probability
signal_cluster_score
synthetic_flow_pressure
liquidity_void_score
dealer_hedging_pressure
gamma states
```

Endpoint:

```text
GET /api/research/execution-debug/latest
```

Должен объяснять `why_not`.

---

## 14. Правила для Flow fix

Если `synthetic_flow_pressure` слишком слабый, нельзя менять формулу вслепую.

Нужно смотреть:

```text
GET /api/research/flow-debug/latest
GET /api/research/flow-debug/summary?from=...&to=...
```

Проверить:

```text
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

`FLOW_SURGE` должен оставаться crossing-event:

```text
previous_flow_intensity < 35
current_flow_intensity >= 35
```

---

## 15. Правила для events

Events должны быть редкими, объяснимыми, с payload, с `snapshot_sequence_id > 0`, без startup artifacts.

Запрещено:

- спамить events каждый snapshot;
- создавать FLOW_SURGE на каждом `flow_intensity > 35`;
- создавать VOLATILITY_EXPANSION на каждом `iv_velocity > 2`;
- создавать events на первых snapshots без истории.

Warmup:

```text
snapshot_sequence_id < 5:
не создавать FLOW_SURGE, VOLATILITY_EXPANSION, VOID_INTENSIFYING, VOID_DETECTED, VOID_CLEARED
```

---

## 16. Правила для payload

Ключевые события должны иметь `event_payload_json`:

```text
REGIME_CHANGE
PINNING_BREAK
STRUCTURE_UNSTABLE
EXECUTION_WINDOW_OPEN
FLOW_SURGE
VOLATILITY_EXPANSION
GAMMA_WEAKENING
GAMMA_COLLAPSE
VOID_INTENSIFYING
VOID_DETECTED
VOID_CLEARED
```

Payload должен содержать reason, phase_context, previous/current state where relevant, previous/current execution state where relevant, crossing info where relevant, score breakdown where relevant.

---

## 17. Что делать после каждого Codex diff

```text
1. Обновить code_version / engine_patch_version.
2. Очистить только mos_research.db.
3. history.db оставить, если integrity ok.
4. Запустить backend.
5. Собрать 30–60 минут.
6. Выгрузить db + wal + shm.
7. Проверить SQL.
8. Не переходить к следующему fix, пока текущий не подтверждён.
```

---

## 18. Стандартный SQL после каждого теста

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

## 19. Запрещённые действия Codex

Codex не должен:

```text
переписывать весь backend
создавать новую архитектуру
менять schema без запроса
удалять таблицы
сбрасывать future_labels
менять flow scale
добавлять BUY/SELL торговые сигналы
переносить бизнес-логику во frontend
скрывать ошибки fallback-логикой
искусственно поднимать scores
делать много файлов за один раз без необходимости
```

---

## 20. Главный рабочий цикл

```text
Пользователь → формулирует проблему по базе.
ChatGPT → пишет точный prompt для Codex.
Codex → делает маленький diff.
Пользователь → запускает backend 30–60 минут.
Пользователь → загружает свежий комплект db + wal + shm.
ChatGPT → проверяет именно последние файлы.
Потом следующий маленький fix.
```

---

## 21. Финальное правило

Пока не выполнены критерии:

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

нельзя начинать:

```text
Market Memory
ML
долгую историю
новые dashboard-фичи
autotrading
```

Сначала стабилизация Research Layer.

---

## 22. GitHub сохранение изменений

В конце каждой задачи, где Codex менял файлы проекта, изменения должны быть сохранены на GitHub.

Project remote rule:

```text
For this MOS project, push only to:
https://github.com/eunjuamacher-lang/btc-gpt

Do not push this project to any other GitHub repository or remote.
Before pushing, verify that origin fetch/push points to eunjuamacher-lang/btc-gpt.
```

Правило:

```text
После успешной проверки:
1. показать git status;
2. проверить diff;
3. закоммитить только относящиеся к задаче файлы;
4. запушить ветку на GitHub;
5. если работа идёт не в main, создать draft PR или дать ссылку на PR;
6. в финальном ответе указать commit / branch / PR.
```

Если пользователь явно сказал не пушить, остановиться на локальном diff и написать, что push пропущен по указанию пользователя.

Если рабочее дерево смешанное и есть чужие/не относящиеся к задаче изменения, не добавлять их молча. Сначала явно перечислить scope commit-а.
