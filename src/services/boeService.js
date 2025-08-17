import axios from "axios";

const API_BASE = process.env.REACT_APP_API_BASE || "http://localhost:5000/api";

// 📦 Obtener listado con filtros (paginado)
export const getItems = (params) =>
  axios.get(`${API_BASE}/items`, { params });

// 📊 Cargar filtros únicos desde el backend
export const getFilterOptions = () =>
  Promise.all([
    axios.get(`${API_BASE}/items/departamentos`),
    axios.get(`${API_BASE}/items/epigrafes`),
    axios.get(`${API_BASE}/items/secciones`),
  ]);

// 📘 Detalles de ítems
export const getItemById = (id) => axios.get(`${API_BASE}/items/${id}`);
export const getResumen = (id) => axios.get(`${API_BASE}/items/${id}/resumen`);
export const getImpacto = (id) => axios.get(`${API_BASE}/items/${id}/impacto`);

// 💬 Comentarios
export const getComments = (id) => axios.get(`${API_BASE}/comments/${id}`);
export const postComment = (payload) => axios.post(`${API_BASE}/comments`, payload);

// 👍 Votos
export const likeItem = (id) => axios.put(`${API_BASE}/items/${id}/like`);
export const dislikeItem = (id) => axios.put(`${API_BASE}/items/${id}/dislike`);
