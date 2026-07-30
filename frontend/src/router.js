/**
 * Hash-based Single Page Application (SPA) Router.
 * Управляет переключением страниц без перезагрузки и лагов.
 */
class HashRouter {
  constructor() {
    this.routes = {
      '': 'dashboard',
      '#/': 'dashboard',
      '#/heatmap': 'heatmap',
      '#/probability': 'probability',
      '#/iv-term-structure': 'term_structure',
      '#/gex': 'gex',
      '#/skew': 'skew',
      '#/signals': 'signals',
      '#/research': 'research',
      '#/manual-trading': 'manual_trading',
      '#/debug': 'debug',
      '#/help': 'help'
    };
    this.pages = {}; // Экземпляры страниц
    this.currentRoute = null;
  }

  /**
   * Регистрация страницы в роутере
   * @param {string} name — имя страницы (соответствует значению в this.routes)
   * @param {Object} pageInstance — объект страницы с методами init/update/resize
   */
  registerPage(name, pageInstance) {
    this.pages[name] = pageInstance;
  }

  init() {
    // Слушаем изменение хэша в URL
    window.addEventListener('hashchange', () => this.handleRouting());
    // Первоначальный роутинг при загрузке
    this.handleRouting();
  }

  handleRouting() {
    const hash = window.location.hash;
    
    // Проверяем валидность маршрута. Если неизвестный — редиректим на главную
    let pageName = this.routes[hash];
    if (!pageName) {
      window.location.hash = '#/';
      return;
    }

    this.currentRoute = hash;

    // Переключаем вкладки навигации в меню
    document.querySelectorAll('.nav-tab').forEach(tab => {
      const pageAttr = tab.getAttribute('data-page');
      if (pageAttr === pageName) {
        tab.classList.add('active');
      } else {
        tab.classList.remove('active');
      }
    });

    // Переключаем видимость контейнеров страниц в DOM
    document.querySelectorAll('.page-container').forEach(container => {
      const containerId = container.id;
      if (containerId === `page-${pageName}`) {
        container.classList.add('active');
      } else {
        container.classList.remove('active');
      }
    });

    // Оповещаем активированную страницу (важно для вызова resize у графиков Chart.js и Lightweight Charts)
    const activePage = this.pages[pageName];
    if (activePage) {
      if (typeof activePage.onActivate === 'function') {
        activePage.onActivate();
      } else if (typeof activePage.resizeCharts === 'function') {
        activePage.resizeCharts();
      }
    }
  }

  /**
   * Программный переход на другую страницу
   * @param {string} hash — хэш страницы, например '#/gex'
   */
  navigate(hash) {
    window.location.hash = hash;
  }
}

export const router = new HashRouter();
