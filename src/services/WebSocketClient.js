// src/services/WebSocketClient.js

const isProd = process.env.NODE_ENV === "production";

// Base del WS desde env (sin puerto fijo en prod)
const RAW_WS_BASE = (isProd
  ? process.env.REACT_APP_WS_BASE_URL           // ej: wss://backend.up.railway.app
  : process.env.REACT_APP_WS_BASE_URL_DEV       // ej: ws://localhost:8001
) || "";

/**
 * Normaliza la base:
 * - recorta slashes finales
 * - convierte http(s) -> ws(s) si nos pasaron una URL HTTP por error
 */
function normalizeBase(base) {
  if (!base) return "";
  let b = base.trim().replace(/\/+$/, "");
  if (b.startsWith("http://"))  b = "ws://"  + b.slice("http://".length);
  if (b.startsWith("https://")) b = "wss://" + b.slice("https://".length);
  return b;
}

/**
 * Une base y path garantizando un único slash.
 */
function joinUrl(base, path) {
  const b = normalizeBase(base);
  const p = path ? (path.startsWith("/") ? path : `/${path}`) : "";
  return `${b}${p}`;
}

export class WebSocketClient {
  /**
   * @param {object} opts
   * @param {string} [opts.path="/ws"]  Ruta del WS en el backend
   * @param {string} [opts.base=RAW_WS_BASE]  Base absoluta del WS (wss://...); por defecto de env
   * @param {string} [opts.token]  Opcional: token JWT o similar; se envía como ?token=...
   * @param {number} [opts.pingInterval=25000]  ms entre heartbeats
   * @param {number} [opts.maxBackoff=15000]  backoff máximo en ms
   */
  constructor({
    path = "/ws",
    base = RAW_WS_BASE,
    token,
    pingInterval = 25_000,
    maxBackoff = 15_000,
  } = {}) {
    this.base = base;
    this.path = path;
    this.token = token;
    this.pingInterval = pingInterval;
    this.maxBackoff = maxBackoff;

    this.socket = null;
    this.subscribers = new Set();
    this.queue = [];
    this.retries = 0;
    this._closing = false;
    this._pingTimer = null;

    if (!this.base) {
      console.warn("[WS] Deshabilitado: falta REACT_APP_WS_BASE_URL / _DEV");
      return;
    }

    // Auto-connect
    this._connect();

    // Pausar heartbeat cuando la pestaña está oculta
    if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) this._stopHeartbeat();
        else this._startHeartbeat();
      });
    }
  }

  _buildUrl() {
    const baseUrl = joinUrl(this.base, this.path || "/ws");
    if (!this.token) return baseUrl;
    const sep = baseUrl.includes("?") ? "&" : "?";
    return `${baseUrl}${sep}token=${encodeURIComponent(this.token)}`;
  }

  _connect() {
    if (!this.base) return;

    const url = this._buildUrl();
    try {
      this.socket = new WebSocket(url);
    } catch (e) {
      console.error("[WS] URL inválida:", url, e);
      return;
    }

    this._closing = false;

    this.socket.onopen = () => {
      console.log("[WS] Conectado:", url);
      this.retries = 0;
      // Vaciar cola
      while (this.queue.length && this.socket?.readyState === WebSocket.OPEN) {
        this.socket.send(this.queue.shift());
      }
      this._startHeartbeat();
    };

    this.socket.onmessage = (evt) => {
      let payload = evt.data;
      try {
        payload = JSON.parse(evt.data);
      } catch {}
      this.subscribers.forEach((h) => {
        try { h(payload); } catch (e) { console.error("[WS] Handler error:", e); }
      });
    };

    this.socket.onerror = (e) => {
      console.error("[WS] Error:", e);
    };

    this.socket.onclose = (e) => {
      this._stopHeartbeat();
      if (this._closing) {
        console.log("[WS] Cerrado por el cliente.");
        return;
      }
      // Reintento exponencial
      const delay = Math.min(300 * 2 ** this.retries, this.maxBackoff);
      this.retries += 1;
      console.warn(`[WS] Desconectado. Reintentando en ${delay}ms...`);
      setTimeout(() => this._connect(), delay);
    };
  }

  _startHeartbeat() {
    this._stopHeartbeat();
    if (!this.pingInterval) return;
    this._pingTimer = setInterval(() => {
      if (this.socket?.readyState === WebSocket.OPEN) {
        try {
          // Algunos backends esperan "ping", otros ignoran; sirve para keep-alive
          this.socket.send(JSON.stringify({ type: "ping", ts: Date.now() }));
        } catch (e) {
          console.debug("[WS] Ping falló:", e);
        }
      }
    }, this.pingInterval);
  }

  _stopHeartbeat() {
    if (this._pingTimer) {
      clearInterval(this._pingTimer);
      this._pingTimer = null;
    }
  }

  /**
   * Envía un payload (objeto o string). Si no está OPEN, lo encola.
   */
  send(payload) {
    const data = typeof payload === "string" ? payload : JSON.stringify(payload);
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(data);
    } else {
      this.queue.push(data);
    }
  }

  /**
   * Suscríbete a mensajes entrantes.
   * @param {(data:any)=>void} handler
   * @returns {() => void} unsubscribe
   */
  onMessage(handler) {
    this.subscribers.add(handler);
    return () => this.offMessage(handler);
  }

  offMessage(handler) {
    this.subscribers.delete(handler);
  }

  /**
   * Cierra la conexión y cancela reconexiones.
   */
  close() {
    this._closing = true;
    this._stopHeartbeat();
    if (this.socket && this.socket.readyState !== WebSocket.CLOSED) {
      try { this.socket.close(); } catch {}
    }
    this.socket = null;
  }
}
