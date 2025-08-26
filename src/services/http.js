// src/services/http.js
import axios from "axios";

const API = (process.env.REACT_APP_API_BASE_URL || "").replace(/\/$/, "");

export const http = axios.create({
  baseURL: API, // ej: https://boetracker-production-7205.up.railway.app/api
  timeout: 15000,
});

http.interceptors.response.use(
  (r) => r,
  (err) => {
    const url = err?.config?.url || "";
    const status = err?.response?.status;
    console.error(`API error: ${status || err.message} → ${url}`);
    return Promise.reject(err);
  }
);

// helpers
export const get = (path, config) => http.get(path, config).then(r => r.data);
export const post = (path, data, config) => http.post(path, data, config).then(r => r.data);
