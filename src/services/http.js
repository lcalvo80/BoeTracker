// src/services/http.js
import axios from "axios";

const BASE_URL =
  process.env.REACT_APP_API_BASE ||
  "https://boetracker-production-7205.up.railway.app/api";

export const api = axios.create({
  baseURL: BASE_URL,
  timeout: 15000,
  withCredentials: false,
});

// Log y normalización de errores
api.interceptors.response.use(
  (resp) => resp,
  (err) => {
    const url = err?.config?.url || "(unknown)";
    const msg =
      err?.response?.data?.error ||
      err?.response?.data?.detail ||
      err.message ||
      "Network error";
    console.error(`API error: ${msg} → ${url}`);
    return Promise.reject(err);
  }
);
