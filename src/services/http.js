// src/services/http.js
import axios from "axios";

/** Permite pasar:
 *  - REACT_APP_API_BASE_URL="https://boetracker-production-7205.up.railway.app"
 *  - o dejarlo vacío para usar "/api" (edge/proxy)
 */
const RAW = (process.env.REACT_APP_API_BASE_URL || "/api").replace(/\/+$/, "");

// Si RAW empieza por http(s), añade sufijo /api; si es relativo ("/api"), úsalo tal cual
const API = RAW.startsWith("http") ? `${RAW}/api` : RAW;

export const api = axios.create({
  baseURL: API,
  timeout: 15000,
  headers: { "Content-Type": "application/json", Accept: "application/json" },
});

export const get   = (path, cfg)           => api.get(path, cfg).then(r => r.data);
export const post  = (path, data, cfg)     => api.post(path, data, cfg).then(r => r.data);
export const del   = (path, cfg)           => api.delete(path, cfg).then(r => r.data);
export const put   = (path, data, cfg)     => api.put(path, data, cfg).then(r => r.data);
export const patch = (path, data, cfg)     => api.patch(path, data, cfg).then(r => r.data);

export default api;
