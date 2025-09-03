
// src/services/http.js
import axios from "axios";

// Si NO hay REACT_APP_API_BASE_URL, usamos '/api' (same-origin detrás de reverse proxy)
const API = (process.env.REACT_APP_API_BASE_URL || "/api").replace(/\/$/, "");

export const api = axios.create({
  baseURL: API,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
  // Si usas cookies/sesión cross-site, descomenta:
  // withCredentials: true,
});

api.interceptors.response.use(
  (response) => response,
  (err) => {
    const url = err?.config?.url || "";
    const status = err?.response?.status;
    console.error(`API error: ${status || err.message} → ${url}`);
    return Promise.reject(err);
  }
);

export const get = (path, config) => api.get(path, config).then((r) => r.data);
export const post = (path, data, config) => api.post(path, data, config).then((r) => r.data);
export default api;
