// src/services/boeService/index.js
import axios from "axios";

const API_BASE = process.env.REACT_APP_API_URL ?? "https://boetracker-production-7205.up.railway.app/api";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 15000,
});

// Interceptor de respuesta: normaliza errores
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const url = err.config?.url || "";
    const status = err.response?.status;
    const msg = err.response?.data?.detail || err.response?.data?.error || err.message;
    // Log compacto y una sola vez
    // eslint-disable-next-line no-console
    console.warn(`API error: ${status || "?"} → ${url} :: ${msg}`);
    return Promise.reject(err);
  }
);

// Helpers con tolerancia a errores
export async function getItemById(id) {
  return api.get(`/items/${encodeURIComponent(id)}`);
}

export async function getResumen(id) {
  try {
    return await api.get(`/items/${encodeURIComponent(id)}/resumen`);
  } catch (e) {
    // fallback: vacío
    return { data: { resumen: "" } };
  }
}

export async function getImpacto(id) {
  try {
    return await api.get(`/items/${encodeURIComponent(id)}/impacto`);
  } catch (e) {
    // 500/404/CORS ⇒ tratamos como vacío
    return { data: { impacto: "" } };
  }
}

export async function getComments(id, { page = 1, limit = 20 } = {}) {
  try {
    return await api.get(`/items/${encodeURIComponent(id)}/comments`, {
      params: { page, limit },
    });
  } catch (e) {
    // Si es 404 => lista vacía estable
    if (e?.response?.status === 404) {
      return { data: { items: [], page: 1, pages: 0, total: 0 } };
    }
    // Para CORS/Network, también devolvemos vacío para no romper el UI
    return { data: { items: [], page: 1, pages: 0, total: 0 } };
  }
}

export async function postComment(id, payload) {
  // Evitar post si no hay backend accesible
  try {
    return await api.post(`/items/${encodeURIComponent(id)}/comments`, payload);
  } catch (e) {
    // Re-propagamos error para mostrar mensaje en UI
    throw (e?.response?.data || { message: "No se pudo enviar el comentario." });
  }
}

export async function likeItem(id) {
  try {
    return await api.post(`/items/${encodeURIComponent(id)}/like`);
  } catch {
    return { data: { likes: undefined } };
  }
}

export async function dislikeItem(id) {
  try {
    return await api.post(`/items/${encodeURIComponent(id)}/dislike`);
  } catch {
    return { data: { dislikes: undefined } };
  }
}
