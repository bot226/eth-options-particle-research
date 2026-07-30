/**
 * Shared Data Store — Централизованный стейт-менеджер приложения.
 * Загружает данные один раз и раздаёт их всем страницам-подписчикам.
 */
import { fetchSnapshot, DashboardWS } from './api/client.js';

class DataStore {
  constructor() {
    this.state = {
      status: 'connecting', // 'connecting', 'connected', 'error', 'loading'
      spot: 0,
      spot_24h_change: 0,
      expiries: [],
      nearest_expiry: null,
      heatmap: [],
      heatmap_metadata: {},
      skew: null,
      term_structure: null,
      oi_by_expiry: [],
      probability: null,
      gex: null,
      oi_levels: [],
      signals: [],
      klines: [],
      summary: null,
      last_update: null,
      raw_data: null // Сохраняем полный ответ бэкенда для страницы DEBUG
    };
    this.listeners = [];
    this.pollTimer = null;
    this.ws = null;
    this.isInitialized = false;
  }

  /**
   * Подписка на обновление стейта
   * @param {Function} listener — функция-коллбэк, принимающая state
   * @returns {Function} — функция для отписки
   */
  subscribe(listener) {
    this.listeners.push(listener);
    // Сразу передаём текущий стейт новому подписчику для быстрой инициализации
    if (this.state.last_update) {
      try {
        listener(this.state);
      } catch (e) {
        console.error('Error during initial state notification:', e);
      }
    }
    return () => {
      this.listeners = this.listeners.filter(l => l !== listener);
    };
  }

  notify() {
    for (const listener of this.listeners) {
      try {
        listener(this.state);
      } catch (e) {
        console.error('Error in store listener:', e);
      }
    }
  }

  async pollData() {
    try {
      const data = await fetchSnapshot();
      if (data) {
        this.state.raw_data = data;
      }
      if (data && data.status === 'ok' && data.data) {
        this.state = {
          ...this.state,
          ...data.data,
          market_state: data.market_state,
          // Сохраняем статус подключения WebSocket
          status: this.state.status === 'error' ? 'connecting' : this.state.status,
          raw_data: data
        };
        this.notify();
      } else if (data && data.status === 'loading') {
        this.state.status = 'connecting';
        this.notify();
      } else {
        this.notify();
      }
    } catch (e) {
      console.error('Store polling error:', e);
      this.state.status = 'error';
      this.state.raw_data = { error: e.message, type: 'Store polling error' };
      this.notify();
    }
  }

  init() {
    if (this.isInitialized) return;
    this.isInitialized = true;

    // Первоначальный запрос данных
    this.pollData();

    // Настройка и подключение WebSocket
    this.ws = new DashboardWS(
      () => {
        // При получении сигнала об обновлении делаем быстрый запрос
        this.pollData();
      },
      (status) => {
        if (status === 'connected') {
          this.state.status = 'connected';
          // Немедленно обновляем данные при переподключении
          this.pollData();
        } else if (status === 'error') {
          this.state.status = 'error';
        } else if (status === 'disconnected') {
          this.state.status = 'reconnecting';
        } else {
          this.state.status = 'connecting';
        }
        this.notify();
      }
    );
    this.ws.connect();

    // Резервный поллинг каждые 15 секунд на случай падения WebSocket
    // (WS heartbeat каждые 20s, поллинг — страховка)
    this.pollTimer = setInterval(() => this.pollData(), 15000);
  }

  destroy() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.ws) {
      this.ws.disconnect();
      this.ws = null;
    }
    this.isInitialized = false;
  }
}

export const store = new DataStore();
