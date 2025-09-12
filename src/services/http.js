// src/services/http.js
import axios from "axios";

/**
 * BASE_URL soporta:
 *  - REACT_APP_API_BASE_URL = "https://<backend>.up.railway.app"  => genera baseURL "https://.../api"
 *  - (vacío) => usa "/api" (válido cuando tengas Edge Routes en el MISMO proyecto)
 */
const RAW = (process.env.REACT_APP_API_BASE_URL || "/api").replace(/\/+$/, "");
const API_BASE = RAW.startsWith("http") ? `${RAW}/api` : RAW;

export const api = axios.create({
  baseURL: API_BASE,
  headers: {
    "Content-Type": "application/json",
    Accept: "application/json",
  },
  timeout: 15000,
});

// Helpers que devuelven el .data directamente
export const get   = (path, cfg)         => api.get(path, cfg).then(r => r.data);
export const post  = (path, data, cfg)   => api.post(path, data, cfg).then(r => r.data);
export const put   = (path, data, cfg)   => api.put(path, data, cfg).then(r => r.data);
export const patch = (path, data, cfg)   => api.patch(path, data, cfg).then(r => r.data);
export const del   = (path, cfg)         => api.delete(path, cfg).then(r => r.data);

export default api;
