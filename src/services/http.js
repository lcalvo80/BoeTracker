// src/services/http.js
import axios from "axios";

// Si NO hay REACT_APP_API_BASE_URL, usamos '/api' (same-origin detrás de reverse proxy)
const API = (process.env.REACT_APP_API_BASE_URL || "/api").replace(/\/$/, "");

export const api = axios.create({
  baseURL: API,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
  // withCredentials: true, // descomenta si usas cookies/sesión cross-site
});

/** Crea un AbortController para cancelación desde el FE (axios v1 soporta `signal`) */
export const createAbortController = () => new AbortController();

api.interceptors.response.use(
  (response) => response,
  (err) => {
    // Ignora logs ruidosos si la petición fue cancelada
    if (err?.name === "CanceledError" || err?.code === "ERR_CANCELED" || err?.name === "AbortError") {
      return Promise.reject(err);
    }
    const url = err?.config?.url || "";
    const status = err?.response?.status;
    console.error(`API error: ${status || err.message} → ${url}`);
    return Promise.reject(err);
  }
);

// Helpers que devuelven directamente `data` y aceptan `config` (incluye `signal`)
export const get   = (path, config)           => api.get(path, config).then((r) => r.data);
export const post  = (path, data, config)     => api.post(path, data, config).then((r) => r.data);
export const del   = (path, config)           => api.delete(path, config).then((r) => r.data);
export const put   = (path, data, config)     => api.put(path, data, config).then((r) => r.data);
export const patch = (path, data, config)     => api.patch(path, data, config).then((r) => r.data);

export default api;
