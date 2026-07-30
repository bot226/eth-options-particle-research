/**
 * Phase 2: Institutional Knowledge Center (MOS Help Tab)
 * Bloomberg-style dense rendering of the ultimate market structure help system.
 * Contains 13 analytical sub-sections, unified module definitions, decision framework, and interactive glossary.
 */

export class HelpPage {
  constructor() {
    this.container = null;
    this.isInitialized = false;
    this.activeSection = 'overview';
  }

  init(container) {
    this.container = container;
    this.container.innerHTML = `
      <div class="help-page-layout">
        <!-- LEFT SIDEBAR MENU -->
        <aside class="help-sidebar">
          <div class="help-sidebar-header">
            <span class="sidebar-icon">📖</span> КНИГА ЗНАНИЙ MOS
          </div>
          <nav class="help-nav-list">
            <button class="help-nav-item active" data-sec="overview">📌 ВВЕДЕНИЕ & ОБЗОР</button>
            <button class="help-nav-item" data-sec="decision_framework">🧭 КАК ЧИТАТЬ СИСТЕМУ</button>
            <div class="help-nav-category">АНАЛИТИЧЕСКИЕ МОДУЛИ</div>
            <button class="help-nav-item" data-sec="market_intelligence">🧠 0. РЫНОЧНЫЙ АНАЛИЗ</button>
            <button class="help-nav-item" data-sec="state_machine">⚙️ 1. МОДЕЛЬ СОСТОЯНИЙ</button>
            <button class="help-nav-item" data-sec="gamma">🧲 2. МОДУЛЬ GAMMA (GEX)</button>
            <button class="help-nav-item" data-sec="volatility">📈 3. РЕЖИМ ВОЛАТИЛЬНОСТИ</button>
            <button class="help-nav-item" data-sec="skew">⚖️ 4. МОДУЛЬ SKEW</button>
            <button class="help-nav-item" data-sec="liquidity_flow">💧 5. ПОТОКИ & ЛИКВИДНОСТЬ</button>
            <button class="help-nav-item" data-sec="meta_state">🧠 6. МЕТА-СОСТОЯНИЕ</button>
            <button class="help-nav-item" data-sec="scenarios">🎯 7. МОДУЛЬ СЦЕНАРИЕВ</button>
            <button class="help-nav-item" data-sec="execution">⚡ 8. ИСПОЛНЕНИЕ (EXECUTION)</button>
            <div class="help-nav-category">СИСТЕМНЫЕ СВЕДЕНИЯ</div>
            <button class="help-nav-item text-orange" data-sec="limitations">⚠️ ОГРАНИЧЕНИЯ СИСТЕМЫ</button>
            <button class="help-nav-item" data-sec="glossary">🗂️ СЛОВАРЬ ТЕРМИНОВ</button>
          </nav>
        </aside>

        <!-- RIGHT CONTENT AREA -->
        <section class="help-content-wrapper">
          <div class="help-content-inner" id="help-content-area">
            <!-- Content will be rendered dynamically -->
          </div>
        </section>
      </div>
    `;

    // Add event listeners to navigation buttons
    this.container.querySelectorAll('.help-nav-item').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const targetSection = e.currentTarget.getAttribute('data-sec');
        this.switchSection(targetSection);
      });
    });

    this.isInitialized = true;
    this.renderActiveSection();
  }

  update(state) {
    if (!this.isInitialized) return;
    // HelpPage mostly contains static/educational content, but we can dynamicize if needed.
  }

  switchSection(sectionId) {
    this.activeSection = sectionId;
    
    // Update menu state
    this.container.querySelectorAll('.help-nav-item').forEach(btn => {
      if (btn.getAttribute('data-sec') === sectionId) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });

    this.renderActiveSection();
    
    // Smooth scroll content area to top
    const contentArea = this.container.querySelector('.help-content-wrapper');
    if (contentArea) contentArea.scrollTop = 0;
  }

  renderActiveSection() {
    const contentArea = document.getElementById('help-content-area');
    if (!contentArea) return;

    let html = '';

    switch (this.activeSection) {
      case 'overview':
        html = `
          <div class="help-section-header">
            <span class="title-icon">📌</span>
            <div class="title-text">
              <h2>Введение в Options Market Operating System (MOS)</h2>
              <span class="subtitle">ФИЛОСОФИЯ И СТРУКТУРА ТЕРМИНАЛА</span>
            </div>
          </div>
          
          <div class="help-rich-content">
            <p class="lead-text">
              <strong>Options MOS</strong> — это не стандартный технический индикатор или торговый бот. Это комплексная институциональная терминальная система, предназначенная для раскрытия скрытой структуры рынка криптовалют через анализ рисков, обязательств и потоков маркетмейкеров (дилеров).
            </p>

            <div class="help-grid-col2">
              <div class="help-card-panel">
                <h3>Философия Системы</h3>
                <p>Опционный рынок — это место, где концентрируется умный капитал. Крупные фонды, майнеры и маркетмейкеры используют опционы для хеджирования миллиардных портфелей и направленных ставок. Изменения в открытом интересе опционов и динамика цен заставляют дилеров совершать регулярные, математически предсказуемые операции с базовым активом (BTC) для выравнивания своих рисков (дельта-хеджирование).</p>
                <p><strong>MOS раскрывает эту невидимую механику</strong>, позволяя вам понять, в какой среде находится рынок и куда дует институциональный ветер.</p>
              </div>

              <div class="help-card-panel border-cyan">
                <h3>Аналогия с Bloomberg Terminal</h3>
                <p>Веб-интерфейс дашборда спроектирован по стандартам профессиональных десков маркетмейкинга:</p>
                <ul>
                  <li><span class="text-cyan">Плотная сетка метрик</span> — минимум графического мусора, максимум числовых данных.</li>
                  <li><span class="text-cyan">Кросс-анализ</span> — модули работают не изолированно, а обогащают выводы друг друга.</li>
                  <li><span class="text-cyan">Быстрые ответы</span> — терминал за секунду отвечает на главные вопросы трейдера.</li>
                </ul>
              </div>
            </div>

            <div class="help-note">
              <h4>💡 Ключевой инсайт</h4>
              <p>Спотовый рынок (цена BTC) постоянно испытывает 'гравитационное притяжение' со стороны рынка опционов. Пиннинг к страйкам, взрывные шорт-сквизы из-за короткой гаммы дилеров, резкие развороты от опционных стен — все эти события происходят по строгим математическим законам. MOS автоматизирует эти расчеты в реальном времени.</p>
            </div>
          </div>
        `;
        break;

      case 'decision_framework':
        html = `
          <div class="help-section-header">
            <span class="title-icon">🧭</span>
            <div class="title-text">
              <h2>Как читать систему: Институциональный алгоритм</h2>
              <span class="subtitle">ПОСЛЕДОВАТЕЛЬНОСТЬ ИНТЕРПРЕТАЦИИ И ПРИНЯТИЯ РЕШЕНИЙ</span>
            </div>
          </div>

          <div class="help-rich-content">
            <p class="lead-text">
              Самая частая ошибка начинающего трейдера — искать случайные точки входа по одному сигналу. Торговля через MOS построена на оценке <strong>состояния среды</strong>. Ниже описан строгий пошаговый алгоритм чтения терминала, используемый профессиональными десками.
            </p>

            <div class="help-timeline">
              <div class="timeline-step">
                <span class="step-num">1</span>
                <div class="step-content">
                  <h4>Сначала читать Market Intelligence (Рыночный Анализ)</h4>
                  <p>Это верхний баннер. Он объединяет данные всех систем. Прочтите сводный аналитический вывод (Narrative) и посмотрите на 5 мгновенных ответов справа. Вы сразу поймете, кто контролирует рынок, какова стабильность структуры и качество исполнения ордеров на данный момент.</p>
                </div>
              </div>

              <div class="timeline-step">
                <span class="step-num">2</span>
                <div class="step-content">
                  <h4>Оценить фазу рынка в State Machine (Модель состояний)</h4>
                  <p>Проверьте текущий подтвержденный режим. Рынок заблокирован в пиннинге? Идет компрессия волатильности? Или активен режим сквиза/ликвидаций? Посмотрите на параметры стабильности режима ('regime_stability') и время удержания в секундах, чтобы понять долгосрочность фазы.</p>
                </div>
              </div>

              <div class="timeline-step">
                <span class="step-num">3</span>
                <div class="step-content">
                  <h4>Проанализировать Gamma-режим (Позиционирование дилеров)</h4>
                  <p>Определите, где находится цена BTC относительно **Gamma Flip**. 
                     <br>• Выше флипа (**Positive Gamma**) — дилеры стабилизируют цену (рыночный шум гасится).
                     <br>• Ниже флипа (**Negative Gamma**) — дилеры разгоняют волатильность (высокий риск резких импульсов).
                     <br>Оцените силу пиннинга и расстояние до стен **Call Wall** и **Put Wall**.</p>
                </div>
              </div>

              <div class="timeline-step">
                <span class="step-num">4</span>
                <div class="step-content">
                  <h4>Изучить Volatility Regime (Режим волатильности)</h4>
                  <p>Проверьте уровень подразумеваемой волатильности (**ATM IV**) и наклон временной структуры (**Term Structure Slope**). Положительный наклон (контанго) говорит о нормальном состоянии рынка, отрицательный (бэквордация) — о панике. Обратите внимание на почасовую скорость волатильности (**IV Velocity**).</p>
                </div>
              </div>

              <div class="timeline-step">
                <span class="step-num">5</span>
                <div class="step-content">
                  <h4>Оценить Flow & Liquidity (Потоки & Ликвидность)</h4>
                  <p>Проверьте баланс сил активных участников через показатель направленного давления сделок (**Flow Pressure**). Если давление > 50 — доминируют покупатели, если < 50 — продавцы. Оцените плотность стакана (статус ликвидности: нормальный или тонкий рынок).</p>
                </div>
              </div>

              <div class="timeline-step">
                <span class="step-num">6</span>
                <div class="step-content">
                  <h4>Изучить Meta-State (Внутреннюю структуру)</h4>
                  <p>Посмотрите на показатель доминирования дилеров (**Dealer Dominance Score**). Высокое значение означает флэт и запертость цены. Оцените хрупкость рынка — если она в статусе 'FRAGILE', будьте готовы к резкому выходу цены из коридора.</p>
                </div>
              </div>

              <div class="timeline-step">
                <span class="step-num">7</span>
                <div class="step-content">
                  <h4>Только потом принимать решение в Execution (Исполнение)</h4>
                  <p>Если все предыдущие шаги сошлись в единую картину, взгляните на панель исполнения. Проверьте направление смещения (**Bias**), качество торговой среды (**Execution Quality**), ценовой уровень отмены вашего сценария (**Invalidation Level**) и оценку соотношения математического ожидания риска к прибыли (R/R).</p>
                </div>
              </div>
            </div>

            <div class="help-warning-box text-orange">
              <h4>⚠️ Важное правило MOS</h4>
              <p>MOS никогда не ищет изолированных 'сигналов' или случайных пересечений линий. Система анализирует **состояние торговой среды**. Торговать против структуры, выявленной MOS (например, открывать сделки с близкими стопами во враждебной среде NEGATIVE_GAMMA с низкой ликвидностью) — математическое самоубийство.</p>
            </div>
          </div>
        `;
        break;

      case 'market_intelligence':
        html = this.renderModuleSection(
          '0. РЫНОЧНЫЙ АНАЛИЗ (Market Intelligence)',
          'cross-engine-synthesis',
          'Автоматический кросс-анализ и синтез данных со всех 8 расчетных модулей системы в единый текстовый вывод для быстрого принятия решений.',
          'Директива Narrative позволяет трейдеру за 3 секунды оценить расстановку сил на рынке, не тратя время на ручной расчет соотношений греков, волатильности и потоков. Это экономит время и страхует от человеческого фактора.',
          'Читайте левую текстовую часть для получения общего торгового контекста. Справа смотрите на пять экспресс-ответов. Обращайте внимание на совпадение показателей: например, если контроль находится у участников, а стабильность структуры хрупкая (\'FRAGILE\'), рынок готов к сильному движению.',
          [
            { title: 'Дилеры подавляют волатильность', desc: 'Возникает при POSITIVE_GAMMA и низкой волатильности. Идеально для торговли внутри диапазона.' },
            { title: 'Риск расширения волатильности растет', desc: 'Сжатие затухает, скорость IV растет. Накапливается импульс.' },
            { title: 'Активен режим PANIC / Стресс', desc: 'Экстремально высокая краткосрочная волатильность, бэквордация структуры, качество исполнения падает.' }
          ],
          'Ошибочно воспринимать текстовые выводы Narrative как прямые торговые команды "BUY" или "SELL". Интеллектуальный баннер описывает **состояние и жесткость структуры рынка**, а не конкретную точку входа.'
        );
        break;

      case 'state_machine':
        html = this.renderModuleSection(
          '1. МОДЕЛЬ СОСТОЯНИЙ (State Machine)',
          'temporal-memory-orchestrator',
          'Классификатор текущей фазы рынка на основе интегрального анализа греков, волатильности и ликвидности с использованием механизма временной фильтрации шума (Debounce).',
          'Рынок постоянно производит хаотичный ценовой шум. Без фильтрации (дебаунсинга) классификатор состояний переключался бы каждую секунду, сбивая трейдера с толку. Наша модель состояний требует удержания фазы минимум 3 шага подряд, отсекая ложные импульсы.',
          'Смотрите на ТЕКУЩИЙ РЕЖИМ. Если в поле КАНДИДАТ висит новое состояние, а статус ПЕРЕХОД в режиме "Ожидает подтверждения", это ранний сигнал о готовящейся смене фазы рынка. Показатель СТАБИЛЬНОСТЬ снижается в моменты перелома тренда.',
          [
            { title: 'PINNING (Пиннинг)', desc: 'Цена зажата дилерами возле крупного опционного уровня.' },
            { title: 'COMPRESSION (Компрессия)', desc: 'Крайне узкий флэт, волатильность задушена, идет накопление.' },
            { title: 'HEDGE_CHASE / SQUEEZE', desc: 'Дилеры вынуждены агрессивно хеджироваться, разгоняя цену. Сильный направленный тренд.' }
          ],
          'Не пытайтесь торговать разворот тренда, когда модель находится в состоянии HEDGE_CHASE или SHORT_SQUEEZE. Это состояния лавинообразного движения, где маркетмейкеры сами выступают топливом для цены, выкупая/продавая актив по любым ценам.'
        );
        break;

      case 'gamma':
        html = `
          ${this.renderModuleSection(
            '2. МОДУЛЬ GAMMA (Gamma Engine / GEX)',
            'dealer-exposure-map',
            'Расчет суммарной гамма-экспозиции дилеров (GEX) и ключевых ценовых барьеров на основе открытого интереса опционов Call и Put.',
            'Дилеры обязаны динамически хеджировать свой опционный портфель на спотовом/фьючерсном рынках. Их сделки создают мощные барьеры поддержки/сопротивления и влияют на волатильность цены BTC.',
            '• **Чистый GEX > 0 (Positive Gamma)** — дилеры стабилизируют рынок. Покупайте вблизи стен на отскок.<br>• **Чистый GEX < 0 (Negative Gamma)** — дилеры разгоняют волатильность. Повышается риск импульсных прорывов.<br>• **Gamma Flip** — критический уровень перелома характера рынка.',
            [
              { title: 'POSITIVE_GAMMA', desc: 'Эффект дилеров: стабилизация цены базового актива. Волатильность падает.' },
              { title: 'NEGATIVE_GAMMA', desc: 'Эффект дилеров: дестабилизация цены. Волатильность и проскальзывания растут.' },
              { title: 'HIGH PINNING', desc: 'Сила пиннинга > 70%. Цена буквально привязана к ключевому опционному страйку.' }
            ],
            'Ни в коем случае не воспринимайте Call Wall или Put Wall как гарантированную, непробиваемую преграду. При переходе рынка в зону Negative Gamma и росте волатильности эти уровни могут быть пробиты, что приведет к катастрофическому импульсу (сквизу), так как пробой заставит дилеров закрывать позиции.'
          )}

          <div class="help-rich-content" style="margin-top: 20px;">
            <div class="help-combo-box">
              <h4>🧲 Комбинация: Сильный Пиннинг (Compression Combo)</h4>
              <div class="combo-flow">
                <span class="combo-node text-green">Positive Gamma</span>
                <span class="combo-arrow">+</span>
                <span class="combo-node text-cyan">IV Compression</span>
                <span class="combo-arrow">+</span>
                <span class="combo-node text-purple">High Pinning (>70%)</span>
              </div>
              <p class="combo-desc"><strong>Результат:</strong> Полное подавление волатильности. Цена BTC намертво зажимается в узком диапазоне вблизи страйка с крупной концентрацией гаммы. Торговля на пробой строго запрещена, работаем только лимитными ордерами внутрь диапазона.</p>
            </div>
          </div>
        `;
        break;

      case 'volatility':
        html = `
          ${this.renderModuleSection(
            '3. РЕЖИМ ВОЛАТИЛЬНОСТИ (Volatility Regime)',
            'implied-volatility-structure',
            'Анализ подразумеваемой волатильности (ATM IV), почасовой скорости ее изменения (IV Velocity) и наклона временной структуры опционов.',
            'Волатильность определяет стоимость опционов. Всплески и падения IV отражают реальные страхи и ожидания институциональных инвесторов перед сильными движениями.',
            'Следите за **IV Velocity**. Скорость выше +0.5 говорит о паническом выкупе опционов (накануне сильного движения). Обращайте внимание на временную структуру: **Контанго** (норма) — рынок спокоен. **Бэквордация** (инверсия) — краткосрочная волатильность выше долгосрочной, на рынке острый кризис или паника.',
            [
              { title: 'COMPRESSION (Компрессия)', desc: 'Экстремально низкая волатильность. Идеально для покупки опционов/страдлов в ожидании прорыва.' },
              { title: 'EXPANSION (Экспансия)', desc: 'Быстрое расширение IV. Опционы дорожают, ценовые колебания BTC растут.' },
              { title: 'PANIC (Паника)', desc: 'Бэквордация структуры. Краткосрочный стресс. Высокий риск обвала цены BTC.' }
            ],
            'Ошибочно считать, что высокая волатильность (IV) всегда означает падение цены BTC. IV растет как при сильных обвалах, так и при взрывных шорт-сквизах вверх. Волатильность указывает на **скорость изменения цены**, а не на ее конечное направление.'
          )}

          <div class="help-rich-content" style="margin-top: 20px;">
            <div class="help-combo-box">
              <h4>💥 Комбинация: Риск Сквиза (Squeeze Combo)</h4>
              <div class="combo-flow">
                <span class="combo-node text-red">Negative Gamma</span>
                <span class="combo-arrow">+</span>
                <span class="combo-node text-orange">IV Expansion</span>
                <span class="combo-arrow">+</span>
                <span class="combo-node text-green">Directional Flow</span>
              </div>
              <p class="combo-desc"><strong>Результат:</strong> Экстремальный риск шорт-сквиза или лонг-ликвидаций. Любое небольшое движение цены BTC заставляет дилеров лавинообразно совершать сделки в направлении движения для хеджирования короткой гаммы, разгоняя цену еще сильнее.</p>
            </div>
          </div>
        `;
        break;

      case 'skew':
        html = this.renderModuleSection(
          '4. МОДУЛЬ SKEW (Skew Engine)',
          'risk-reversal-skew-analysis',
          'Измерение асимметрии цен между опционами Call и Put с дельтой 25% для оценки баланса рыночных ожиданий и страхов.',
          'Skew (скос волатильности) показывает, за какую защиту инвесторы готовы переплачивать. Если Put-опционы дороже, рынок боится падения. Если Call-опционы дороже — рынок охвачен FOMO и ставит на рост.',
          '• **25D Skew > +2.0% (Put-skew)** — страх падения, доминирует спрос на защиту. Медвежий уклон.<br>• **25D Skew < -2.0% (Call-skew)** — бычья эйфория, скупают право на покупку. Бычий уклон.<br>• **Около 0% (Сбалансированный)** — нейтральное состояние рынка.',
          [
            { title: 'BALANCED (Сбалансированный)', desc: 'Нейтральные настроения, симметрия цен Call и Put опционов.' },
            { title: 'CALL_SKEW / Бычий', desc: 'Call-опционы существенно дороже Put. Агрессивное ожидание роста базового актива.' },
            { title: 'PUT_SKEW / Медвежий', desc: 'Put-опционы дороже Call. Крупный капитал активно хеджирует риски снижения.' }
          ],
          'Не торгуйте слепо по направлению сильного перекоса Skew. Экстремально отрицательный Skew (например, < -8%) часто указывает на финальную стадию бычьей эйфории (FOMO), когда рынок перегрет и близок к локальному истощению тренда.'
        );
        break;

      case 'liquidity_flow':
        html = this.renderModuleSection(
          '5. ПОТОКИ & ЛИКВИДНОСТЬ (Liquidity & Flow)',
          'order-flow-pressure-and-open-interest',
          'Анализ открытого интереса (OI), соотношения объемов торгов (Put/Call Ratio) и расчет индекса направленного давления сделок (Flow Pressure).',
          'Ликвидность и объемы сделок — это топливо для любого движения цены BTC. flow-анализ позволяет заглянуть внутрь торговой активности и понять истинную агрессивность покупателей и продавцов.',
          '• **Flow Pressure > 50** — доминирует поток покупателей (рыночные покупки по Ask).<br>• **Flow Pressure < 50** — доминирует поток продавцов (рыночные продажи по Bid).<br>• **Ликвидность в статусе THIN** — стакан пустой. Избегайте входа крупным объемом рыночными ордерами.',
          [
            { title: 'THIN (Тонкий рынок)', desc: 'Низкая плотность стакана, расширенные спреды. Риск сильных мгновенных проскальзываний.' },
            { title: 'NORMAL / ADEQUATE', desc: 'Достаточная ликвидность, комфортные условия для совершения сделок.' },
            { title: 'BULLISH / BEARISH FLOW', desc: 'Устойчивое направленное давление потока сделок участников рынка.' }
          ],
          'Показатель **Flow Pressure** на дашборде рассчитывается по сделкам на Bybit и является оценочной эвристикой, а не полным институциональным футпринтом всего мирового рынка. Не используйте его как единственный грааль для входа в сделку.'
        );
        break;

      case 'meta_state':
        html = this.renderModuleSection(
          '6. МЕТА-СОСТОЯНИЕ (Meta-State Engine)',
          'market-fragility-and-control-regime',
          'Оценка общей хрупкости (Fragility) рыночной структуры опционов и расчет индекса доминирования дилеров (Dealer Dominance Score).',
          'Позволяет оценить, насколько текущая структура рынка устойчива к неожиданным притокам объема и кто диктует условия: алгоритмы маркетмейкеров или направленные спекулянты.',
          '• **Dealer Dominance > 70%** — рынок зажат лимитами дилеров. Идеально для продажи волатильности.<br>• **Хрупкость FRAGILE** — структура нестабильна. Любая искра (крупный ордер) вызовет лавину цены.<br>• **Хрупкость ROBUST / STABLE** — устойчивый, стабильный рынок.',
          [
            { title: 'DEALER_CONTROLLED', desc: 'Рынок под полным контролем маркетмейкеров. Волатильность заблокирована.' },
            { title: 'PARTICIPANT_CONTROLLED', desc: 'Агрессивные игроки выкупают барьеры дилеров. Начинается направленный тренд.' },
            { title: 'FRAGILE (Хрупкий рынок)', desc: 'Структура крайне уязвима перед сильными направленными движениями.' }
          ],
          'Не путайте хрупкость (FRAGILE) с обязательным падением цены. Хрупкий рынок означает, что **у дилеров нет запаса прочности для сдерживания цены**, и рынок готов к сильному импульсу в ЛЮБУЮ сторону (как вверх, так и вниз).'
        );
        break;

      case 'scenarios':
        html = this.renderModuleSection(
          '7. МОДУЛЬ СЦЕНАРИЕВ (Scenario Module)',
          'predictive-structural-paths',
          'Вероятностное моделирование будущей траектории цены BTC на основе распределения рисков сквиза и прорыва опционных стен.',
          'Позволяет заранее подготовиться к наиболее вероятному поведению рынка (флэт у страйка, волатильные качели внутри стен или направленный импульс на пробой).',
          'Смотрите на АКТИВНЫЙ СЦЕНАРИЙ и его ВЕРОЯТНОСТЬ. Если вероятность сценария SQUEEZE_RISK_ELEVATED превышает 60%, это серьезный сигнал закрыть контртрендовые позиции и подготовиться к сильному импульсу.',
          [
            { title: 'PINNED_TO_STRIKE', desc: 'Сценарий низковолатильного удержания цены BTC около ключевого страйка.' },
            { title: 'VOLATILE_RANGE', desc: 'Размашистые колебания цены BTC строго в пределах границ Call Wall и Put Wall.' },
            { title: 'SQUEEZE_RISK_ELEVATED', desc: 'Высокий риск резкого безоткатного импульса (шорт-сквиз / лонг-сквиз).' }
          ],
          'Расчетная вероятность сценария — это математическое ожидание на основе текущей статической опционной структуры. Она не учитывает появление неожиданных внешних фундаментальных новостей (черных лебедей), которые мгновенно ломают любую опционную структуру.'
        );
        break;

      case 'execution':
        html = this.renderModuleSection(
          '8. ИСПОЛНЕНИЕ (Execution Module)',
          'tactical-execution-parameters',
          'Оценка качества торговой среды в момент совершения сделки, расчет уровня отмены сценария и математического ожидания (Risk/Reward).',
          'Помогает трейдеру тактически правильно зайти в рынок: выбрать тип ордера, безопасный объем позиции и рассчитать точный ценовой уровень отмены сценария.',
          '• **Execution Quality: HIGH** — идеальная среда для работы крупным объемом. Минимальные спреды.<br>• **Execution Quality: LOW** — опасная среда, риск проскальзываний. Уменьшайте объем, ставьте более широкие стоп-лоссы.<br>• **Invalidation Level** — используйте этот уровень (обычно Gamma Flip) как отметку для установки стоп-лосса.',
          [
            { title: 'NEUTRAL / BULLISH / BEARISH BIAS', desc: 'Вектор вероятного тактического смещения цены базового актива.' },
            { title: 'HIGH QUALITY', desc: 'Благоприятная среда. Низкие риски проскальзываний при исполнении ордеров.' },
            { title: 'LOW QUALITY', desc: 'Враждебная среда. Тонкие стаканы, высокий шум, опасность каскадов.' }
          ],
          'Показатель Invalidation Level (Уровень отмены) — это не магическая точка, от которой цена всегда разворачивается. Это **ценовой рубеж перелома структуры рынка**. Если цена пробивает его и закрепляется, ваш торговый сценарий теряет математическое преимущество.'
        );
        break;

      case 'limitations':
        html = `
          <div class="help-section-header">
            <span class="title-icon">⚠️</span>
            <div class="title-text">
              <h2>Ограничения системы Options MOS</h2>
              <span class="subtitle">КРИТИЧЕСКИ ВАЖНЫЕ СВЕДЕНИЯ ДЛЯ РЕАЛИСТИЧНЫХ ОЖИДАНИЙ</span>
            </div>
          </div>

          <div class="help-rich-content">
            <p class="lead-text">
              Для честной институциональной работы и правильного риск-менеджмента трейдер обязан четко понимать ограничения математических моделей и эвристик, заложенных в терминал.
            </p>

            <div class="help-grid-col2" style="margin-top: 20px;">
              <div class="help-card-panel border-orange">
                <h3>1. Оценка Flow Pressure</h3>
                <p>Показатель <strong>Flow Pressure</strong> является расчетной производной оценкой активности покупателей/продавцов на основе сделок Bybit. Это <strong>не полноценный ордер-флоу (footprint)</strong> всех мировых криптобирж. Он не гарантирует появление крупных рыночных заявок на других спотовых площадках (Binance, Coinbase).</p>
              </div>

              <div class="help-card-panel border-orange">
                <h3>2. Оценка Execution Quality</h3>
                <p>Параметр <strong>Execution Quality</strong> (Качество исполнения) оценивает текущую опционную и волатильную конъюнктуру рынка (спреды, ликвидность, гамма-режим). Это <strong>не гарантия исполнения вашего личного ордера</strong> без проскальзываний брокера или биржи Bybit.</p>
              </div>

              <div class="help-card-panel">
                <h3>3. Локальность данных</h3>
                <p>MOS рассчитывает структуру рынка на основе открытого интереса и книги опционов <strong>биржи Bybit</strong>. Несмотря на высокую корреляцию, глобальная структура рынка (с учетом CME и Deribit) может иметь локальные отличия в уровнях опционных стен.</p>
              </div>

              <div class="help-card-panel">
                <h3>4. Скорость изменения структуры</h3>
                <p>В периоды экстремального новостного фона (Black Swans) опционная структура, показатели Gamma и IV могут перестраиваться за секунды. Исторические эвристики и расчетные модели сценариев в такие моменты могут запаздывать.</p>
              </div>
            </div>

            <div class="help-warning-box text-orange" style="margin-top: 20px;">
              <h4>⚠️ Дисклеймер: Не является финансовой рекомендацией</h4>
              <p>Вся аналитическая информация, генерируемая терминалом Options MOS, носит исключительно информационно-образовательный характер и является математической интерпретацией опционных данных Bybit. Система не выдает прямых сигналов к покупке или продаже активов. Трейдер несет полную персональную ответственность за свои торговые риски.</p>
            </div>
          </div>
        `;
        break;

      case 'glossary':
        html = `
          <div class="help-section-header">
            <span class="title-icon">🗂️</span>
            <div class="title-text">
              <h2>Словарь терминов Institutional Options MOS</h2>
              <span class="subtitle">ФОРМАТ: ТЕРМИН → ПРОСТОЕ ОБЪЯСНЕНИЕ → ПОЧЕМУ ВАЖНО</span>
            </div>
          </div>

          <div class="help-rich-content">
            <table class="help-glossary-table">
              <thead>
                <tr>
                  <th>Термин</th>
                  <th>Простое объяснение</th>
                  <th>Почему важно</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td class="term-name">Gamma (Гамма)</td>
                  <td>Скорость изменения Дельты опциона при изменении цены базового актива на $1.</td>
                  <td>Показывает, насколько агрессивно дилерам придется совершать сделки для хеджирования портфеля.</td>
                </tr>
                <tr>
                  <td class="term-name">GEX (Gamma Exposure)</td>
                  <td>Суммарный объем гаммы опционов, удерживаемый маркетмейкерами (дилерами).</td>
                  <td>Определяет, будут ли действия дилеров успокаивать рынок или разгонять волатильность.</td>
                </tr>
                <tr>
                  <td class="term-name">Gamma Flip</td>
                  <td>Ценовой уровень, на котором чистая гамма дилеров переходит через ноль.</td>
                  <td>Рубеж между спокойным рынком (выше флипа) и хаотичным, волатильным (ниже флипа).</td>
                </tr>
                <tr>
                  <td class="term-name">IV (Implied Volatility)</td>
                  <td>Подразумеваемая волатильность опционов, закладываемая рынком в их стоимость.</td>
                  <td>Отражает страх и ожидания инвесторов относительно будущего торгового диапазона.</td>
                </tr>
                <tr>
                  <td class="term-name">ATM IV (At-The-Money IV)</td>
                  <td>Волатильность опционов со страйком, равным текущей спотовой цене BTC.</td>
                  <td>Является "базовым" эталоном стоимости волатильности на рынке опционов.</td>
                </tr>
                <tr>
                  <td class="term-name">Skew (Скос волатильности)</td>
                  <td>Разница в волатильности (и стоимости) между опционами Call и Put.</td>
                  <td>Показывает, чего больше боится крупный капитал — падения цены или упущенного роста (FOMO).</td>
                </tr>
                <tr>
                  <td class="term-name">Call Wall / Put Wall</td>
                  <td>Уровни максимальной концентрации гаммы опционов Call (вверху) и Put (внизу).</td>
                  <td>Работают как мощнейшие невидимые уровни сопротивления и поддержки.</td>
                </tr>
                <tr>
                  <td class="term-name">Pinning (Пиннинг)</td>
                  <td>Феномен "примагничивания" цены базового актива к крупному страйку опционов.</td>
                  <td>Удерживает цену BTC в узком флэте перед экспирацией, сглаживая колебания.</td>
                </tr>
                <tr>
                  <td class="term-name">Compression (Компрессия)</td>
                  <td>Период падения волатильности до экстремально низких значений.</td>
                  <td>"Сжатие пружины" перед мощным, взрывным выходом цены из диапазона.</td>
                </tr>
                <tr>
                  <td class="term-name">Expansion (Экспансия)</td>
                  <td>Процесс резкого расширения волатильности и увеличения диапазонов колебаний.</td>
                  <td>Указывает на развитие мощного импульсного тренда.</td>
                </tr>
                <tr>
                  <td class="term-name">Flow Pressure</td>
                  <td>Индекс агрессивности рыночных покупателей опционов Bybit по Ask и Bid.</td>
                  <td>Позволяет выявить скрытые направленные объемы сделок институциональных игроков.</td>
                </tr>
                <tr>
                  <td class="term-name">Dealer Hedging</td>
                  <td>Процесс автоматического дельта-хеджирования рисков маркетмейкеров.</td>
                  <td>Создает невидимые барьеры или лавинообразные ускорения цены на спотовом рынке.</td>
                </tr>
                <tr>
                  <td class="term-name">Fragility (Хрупкость)</td>
                  <td>Показатель уязвимости текущей структуры опционного рынка перед притоком объемов.</td>
                  <td>В статусе FRAGILE рынок готов к сильным движениям от любого внешнего триггера.</td>
                </tr>
                <tr>
                  <td class="term-name">Execution Quality</td>
                  <td>Интегральная оценка благоприятности текущей рыночной среды для совершения сделок.</td>
                  <td>Предостерегает от входа крупным объемом при тонких стаканах и диких спредах.</td>
                </tr>
                <tr>
                  <td class="term-name">Liquidity Void</td>
                  <td>Мгновенное исчезновение лимитных заявок в стакане ("вакуум ликвидности").</td>
                  <td>Приводит к огромным ценовым проскальзываниям и "шпилькам" на графиках.</td>
                </tr>
                <tr>
                  <td class="term-name">Squeeze (Сквиз)</td>
                  <td>Стремительное безоткатное движение цены из-за каскадного закрытия позиций.</td>
                  <td>Период максимальных убытков контртрендовых трейдеров и максимальной прибыли трендовых.</td>
                </tr>
                <tr>
                  <td class="term-name">Regime Transition</td>
                  <td>Процесс смены одного устойчивого рыночного режима на другой.</td>
                  <td>Ключевой момент для перестройки торговой тактики и перезапуска алгоритмов.</td>
                </tr>
              </tbody>
            </table>
          </div>
        `;
        break;
    }

    contentArea.innerHTML = html;
  }

  /**
   * Helper method to render standardized module layout for sections 3 to 11
   */
  renderModuleSection(title, keySymbol, whatShows, whyImportant, howToRead, typicalStates, errors) {
    const statesHtml = typicalStates.map(state => `
      <div class="help-card">
        <div class="help-card-tag">${state.title}</div>
        <p class="help-card-text">${state.desc}</p>
      </div>
    `).join('');

    return `
      <div class="help-section-header">
        <span class="title-icon">⚙️</span>
        <div class="title-text">
          <h2>${title}</h2>
          <span class="subtitle">СИСТЕМНЫЙ СИМВОЛ: ${keySymbol.toUpperCase()}</span>
        </div>
      </div>

      <div class="help-rich-content">
        <div class="help-module-grid">
          <!-- ЧТО ПОКАЗЫВАЕТ -->
          <div class="help-block-item">
            <span class="block-num">A</span>
            <div class="block-content">
              <h3>Что показывает</h3>
              <p>${whatShows}</p>
            </div>
          </div>

          <!-- ПОЧЕМУ ВАЖНО -->
          <div class="help-block-item">
            <span class="block-num">B</span>
            <div class="block-content">
              <h3>Почему важно</h3>
              <p>${whyImportant}</p>
            </div>
          </div>

          <!-- КАК ЧИТАТЬ -->
          <div class="help-block-item">
            <span class="block-num">C</span>
            <div class="block-content">
              <h3>Как читать</h3>
              <p>${howToRead}</p>
            </div>
          </div>
        </div>

        <!-- ТИПИЧНЫЕ СОСТОЯНИЯ -->
        <div class="help-states-container">
          <h3>Типичные режимы и состояния</h3>
          <div class="help-states-grid">
            ${statesHtml}
          </div>
        </div>

        <!-- ОШИБКИ ИНТЕРПРЕТАЦИИ -->
        <div class="help-warning-box text-red" style="margin-top: 20px;">
          <h4>❌ Типичные ошибки интерпретации</h4>
          <p>${errors}</p>
        </div>
      </div>
    `;
  }
}
