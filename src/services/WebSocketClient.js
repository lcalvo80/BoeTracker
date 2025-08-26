// src/services/WebSocketClient.js
const isProd = process.env.NODE_ENV === "production";

// Usa variable de entorno para el backend WS. No añadas :8080 en producción.
const WS_BASE =
  (isProd
    ? process.env.REACT_APP_WS_BASE_URL   // ej: wss://boetracker-production-7205.up.railway.app
    : process.env.REACT_APP_WS_BASE_URL_DEV // ej: ws://localhost:8001
  ) || "";

export class WebSocketClient {
  constructor(path = "/ws") {
    if (!WS_BASE) {
      console.warn("WS deshabilitado: falta REACT_APP_WS_BASE_URL");
      this.socket = null;
      return;
    }
    const url = `${WS_BASE}${path}`;
    this.socket = new WebSocket(url);
    this.socket.onopen = () => console.log("WS connected:", url);
    this.socket.onclose = () => console.log("WS closed");
    this.socket.onerror = (e) => console.error("WS error:", e);
  }

  send(payload) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload));
    }
  }

  onMessage(handler) {
    if (!this.socket) return;
    this.socket.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        handler(data);
      } catch {
        handler(evt.data);
      }
    };
  }

  close() {
    if (this.socket) this.socket.close();
  }
}
