// src/services/WebSocketClient.js

const isProd = process.env.NODE_ENV === "production";
const RAW_WS_BASE = (isProd
  ? process.env.REACT_APP_WS_BASE_URL
  : process.env.REACT_APP_WS_BASE_URL_DEV
) || "";

/** Normaliza base y convierte http(s) -> ws(s) */
function normalizeBase(base) {
  if (!base) return "";
  let b = base.trim().replace(/\/+$/, "");
  if (b.startsWith("http://"))  b = "ws://"  + b.slice("http://".length);
  if (b.startsWith("https://")) b = "wss://" + b.slice("https://".length);
  return b;
}

/** Une base y path con un solo slash (sin forzar barra final) */
function joinUrl(base, path = "") {
  const b = normalizeBase(base);
  const p = path ? (path.startsWith("/") ? path : `/${path}`) : "";
  return `${b}${p}`;
}

export class WebSocketClient {
  /**
   * @param {object} opts
   * @param {string} [opts.path="/ws"]
   * @param {string} [opts.base=RAW_WS_BASE]
   * @param {string} [opts.token]
   * @param {() => (string|Promise<string>)} [opts.tokenProvider]  proveedor de token opcional
   * @param {number} [opts.pingInterval=25000]
   * @param {number} [opts.pongTimeout=8000]   ms sin pong tras un ping => close & reconnect
   * @param {number} [opts.maxBackoff=15000]
   * @param {number} [opts.queueMax=500]       máximo de mensajes en cola
   * @param {boolean} [opts.autoJson=true]     intenta JSON.parse en onmessage
   * @param {(status: 'connecting'|'open'|'closed'|'error') => void} [opts.onStatus]
   * @param {(ev: MessageEvent) => void} [opts.onRawMessage] callback crudo (antes de parseo)
   */
  constructor({
    path = "/ws",
    base = RAW_WS_BASE,
    token,
    tokenProvider,
    pingInterval = 25_000,
    pongTimeout = 8_000,
    maxBackoff = 15_000,
    queueMax = 500,
    autoJson = true,
    onStatus,
    onRawMessage,
  } = {}) {
    this.base = base;
    this.path = path;
    this.token = token;
    this.tokenProvider = tokenProvider;
    this.pingInterval = pingInterval;
    this.pongTimeout = pongTimeout;
    this.maxBackoff = maxBackoff;
    this.queueMax = queueMax;
    this.autoJson = autoJson;

    this.onStatus = onStatus;
    this.onRawMessage = onRawMessage;

    this.socket = null;
    this.subscribers = new Set();
    this.queue = [];
    this.retries = 0;
    this._closing = false;
    this._pingTimer = null;
    this._pongTimer = null;
    this._reconnectTimer = null;

    if (!this.base) {
      console.warn("[WS] Deshabilitado: falta REACT_APP_WS_BASE_URL / _DEV");
      return;
    }

    this._connect();

    if (typeof document !== "undefined" && document.addEventListener) {
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) this._stopHeartbeat();
        else this._startHeartbeat();
      });
    }
    if (typeof window !== "undefined" && window.addEventListener) {
      window.addEventListener("online", () => this._maybeReconnectSoon());
      window.addEventListener("offline", () => this._stopHeartbeat());
    }
  }

  _emitStatus(s) {
    try { this.onStatus && this.onStatus(s); } catch (e) { /* noop */ }
  }

  async _buildUrl() {
    const baseUrl = joinUrl(this.base, this.path || "/ws");
    let tk = this.token;
    if (!tk && this.tokenProvider) {
      try { tk = await this.tokenProvider(); } catch (e) { console.warn("[WS] tokenProvider error:", e); }
    }
    if (!tk) return baseUrl;
    const sep = baseUrl.includes("?") ? "&" : "?";
    return `${baseUrl}${sep}token=${encodeURIComponent(tk)}`;
  }

  async _connect() {
    if (!this.base) return;

    const url = await this._buildUrl();
    this._closing = false;
    this._emitStatus("connecting");

    try {
      this.socket = new WebSocket(url);
    } catch (e) {
      console.error("[WS] URL inválida:", url, e);
      this._scheduleReconnect();
      return;
    }

    this.socket.onopen = () => {
      console.log("[WS] Conectado:", url);
      this.retries = 0;
      this._emitStatus("open");
      // Vaciar cola
      while (this.queue.length && this.socket?.readyState === WebSocket.OPEN) {
        this.socket.send(this.queue.shift());
      }
      this._startHeartbeat();
    };

    this.socket.onmessage = (evt) => {
      if (this.onRawMessage) {
        try { this.onRawMessage(evt); } catch (e) { console.error("[WS] onRawMessage error:", e); }
      }
      // Detección de pong
      try {
        const raw = evt.data;
        let payload = raw;
        if (this.autoJson && typeof raw === "string") {
          try { payload = JSON.parse(raw); } catch { /* keep string */ }
        }
        // Considera pong si viene {type:'pong'} o string 'pong'
        const isPong = (payload && typeof payload === "object" && payload.type === "pong") || (payload === "pong");
        if (isPong) this._clearPongTimeout();

        // Notifica a subs
        const dataToSend = this.autoJson ? payload : raw;
        this.subscribers.forEach((h) => { try { h(dataToSend); } catch (e) { console.error("[WS] Handler error:", e); } });
      } catch (e) {
        console.error("[WS] onmessage error:", e);
      }
    };

    this.socket.onerror = (e) => {
      this._emitStatus("error");
      console.error("[WS] Error:", e);
    };

    this.socket.onclose = (e) => {
      this._stopHeartbeat();
      this._emitStatus("closed");
      if (this._closing) {
        console.log("[WS] Cerrado por el cliente.");
        return;
      }
      this._scheduleReconnect();
    };
  }

  _scheduleReconnect() {
    // backoff exponencial con jitter (full jitter)
    const base = Math.min(300 * (2 ** this.retries), this.maxBackoff);
    const delay = Math.floor(Math.random() * base);
    this.retries += 1;
    console.warn(`[WS] Desconectado. Reintentando en ~${delay}ms...`);

    clearTimeout(this._reconnectTimer);
    this._reconnectTimer = setTimeout(() => this._connect(), delay);
  }

  _maybeReconnectSoon() {
    // Si vuelve la red, intenta reconectar antes
    if (!this._closing && (!this.socket || this.socket.readyState !== WebSocket.OPEN)) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = setTimeout(() => this._connect(), 200);
    }
  }

  _startHeartbeat() {
    this._stopHeartbeat();
    if (!this.pingInterval) return;
    this._pingTimer = setInterval(() => {
      if (this.socket?.readyState === WebSocket.OPEN) {
        try {
          this.socket.send(JSON.stringify({ type: "ping", ts: Date.now() }));
          this._armPongTimeout();
        } catch (e) {
          console.debug("[WS] Ping falló:", e);
        }
      }
    }, this.pingInterval);
  }

  _armPongTimeout() {
    this._clearPongTimeout();
    if (!this.pongTimeout) return;
    this._pongTimer = setTimeout(() => {
      console.warn("[WS] Pong timeout. Forzando reconexión.");
      try { this.socket?.close(); } catch {}
      // onclose programará la reconexión
    }, this.pongTimeout);
  }

  _clearPongTimeout() {
    if (this._pongTimer) {
      clearTimeout(this._pongTimer);
      this._pongTimer = null;
    }
  }

  _stopHeartbeat() {
    if (this._pingTimer) {
      clearInterval(this._pingTimer);
      this._pingTimer = null;
    }
    this._clearPongTimeout();
  }

  /**
   * Envía un payload (objeto o string). Si no está OPEN, lo encola (con límite).
   */
  send(payload) {
    const data = typeof payload === "string" ? payload : JSON.stringify(payload);
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(data);
    } else {
      if (this.queue.length >= this.queueMax) {
        // Política: drop oldest
        this.queue.shift();
      }
      this.queue.push(data);
    }
  }

  /**
   * Permite actualizar el token y reabrir el socket con el nuevo valor.
   */
  async updateToken(nextToken) {
    this.token = nextToken ?? this.token;
    if (this.socket?.readyState === WebSocket.OPEN) {
      // Cierra para renegociar URL con el token nuevo
      try { this.socket.close(); } catch {}
    } else {
      this._maybeReconnectSoon();
    }
  }

  onMessage(handler) {
    this.subscribers.add(handler);
    return () => this.offMessage(handler);
  }
  offMessage(handler) {
    this.subscribers.delete(handler);
  }

  /** Cierra la conexión y cancela reconexiones. */
  close() {
    this._closing = true;
    this._stopHeartbeat();
    clearTimeout(this._reconnectTimer);
    if (this.socket && this.socket.readyState !== WebSocket.CLOSED) {
      try { this.socket.close(); } catch {}
    }
    this.socket = null;
  }
}
