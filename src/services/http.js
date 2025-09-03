import axios from "axios";

// Base URL de la API, configurable vía variable de entorno.
// Ejemplo: https://boetracker-production-7205.up.railway.app/api
const API = (process.env.REACT_APP_API_BASE_URL || "").replace(/\/$/, "");

// Creamos la instancia principal de axios
export const api = axios.create({
  baseURL: API,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
});

// Interceptor de respuestas para logging y errores
api.interceptors.response.use(
  (response) => response,
  (err) => {
    const url = err?.config?.url || "";
    const status = err?.response?.status;
    console.error(`API error: ${status || err.message} → ${url}`);
    return Promise.reject(err);
  }
);

// Helpers para simplificar llamadas
export const get = (path, config) =>
  api.get(path, config).then((r) => r.data);

export const post = (path, data, config) =>
  api.post(path, data, config).then((r) => r.data);

// Export default también, para permitir `import api from './http'`
export default api;
