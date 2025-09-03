// src/services/boeService.js
import api from "./http";

/**
 * Adapta los filtros del frontend al contrato del backend.
 * - secciones[]      -> seccion_codigo (csv)
 * - departamentos[]  -> departamento_codigo (csv)
 * - epigrafes[]      -> epigrafe (csv)
 */
function mapFilters(params = {}) {
  const { secciones, departamentos, epigrafes, ...rest } = params;

  return {
    ...rest,
    ...(Array.isArray(secciones) && secciones.length > 0
      ? { seccion_codigo: secciones.join(",") }
      : {}),
    ...(Array.isArray(departamentos) && departamentos.length > 0
      ? { departamento_codigo: departamentos.join(",") }
      : {}),
    ...(Array.isArray(epigrafes) && epigrafes.length > 0
      ? { epigrafe: epigrafes.join(",") }
      : {}),
  };
}

/* =================== Lectura =================== */

/** Lista de items con filtros. */
export async function getItems(params = {}) {
  const finalParams = mapFilters(params);
  const { data } = await api.get("/items", { params: finalParams });
  return data;
}

/** Detalle de un item por ID. */
export async function getItemById(id) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("getItemById requiere un id válido.");
  }
  const { data } = await api.get(`/items/${id}`);
  return data;
}

/** Resumen de un item por ID. */
export async function getResumen(id) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("getResumen requiere un id válido.");
  }

  try {
    const { data } = await api.get(`/items/${id}/resumen`);
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  const detalle = await getItemById(id);
  if (detalle && (detalle.resumen ?? detalle.summary)) {
    return detalle.resumen ?? detalle.summary;
  }
  return { resumen: null };
}

/** Impacto de un item por ID. */
export async function getImpacto(id) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("getImpacto requiere un id válido.");
  }

  try {
    const { data } = await api.get(`/items/${id}/impacto`);
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  const detalle = await getItemById(id);
  if (detalle && (detalle.impacto ?? detalle.impact)) {
    return detalle.impacto ?? detalle.impact;
  }
  return { impacto: null };
}

/**
 * Comentarios de un item por ID.
 * Acepta paginación/filtros en `params` (p.ej. { page, pageSize }).
 */
export async function getComments(id, params = {}) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("getComments requiere un id válido.");
  }

  // /comments
  try {
    const { data } = await api.get(`/items/${id}/comments`, { params });
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  // /comentarios (alias)
  try {
    const { data } = await api.get(`/items/${id}/comentarios`, { params });
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  // Fallback: embebido en el detalle
  const detalle = await getItemById(id);
  if (detalle && (detalle.comments ?? detalle.comentarios)) {
    return detalle.comments ?? detalle.comentarios;
  }
  return [];
}

/* =================== Escritura/acciones =================== */

/**
 * Publica un comentario en un item.
 * Prioriza: POST /items/:id/comments; alias: /comentarios.
 * body esperado: { text } (puedes añadir campos extra en `extra`)
 */
export async function postComment(id, text, extra = {}) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("postComment requiere un id válido.");
  }
  if (!text || `${text}`.trim() === "") {
    throw new Error("postComment requiere un texto no vacío.");
  }

  const payload = { text, ...extra };

  try {
    const { data } = await api.post(`/items/${id}/comments`, payload);
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  // Alias español
  const { data } = await api.post(`/items/${id}/comentarios`, payload);
  return data;
}

/**
 * Marca like en un item.
 * Intenta POST /items/:id/like; alias: /likes (plural).
 * Fallback: PATCH /items/:id { like: true }.
 */
export async function likeItem(id) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("likeItem requiere un id válido.");
  }

  // Endpoint específico singular
  try {
    const { data } = await api.post(`/items/${id}/like`);
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  // Endpoint plural
  try {
    const { data } = await api.post(`/items/${id}/likes`);
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  // Fallback genérico
  const { data } = await api.patch(`/items/${id}`, { like: true });
  return data;
}

/**
 * Quita like / marca dislike en un item.
 * Intenta POST /items/:id/dislike; o DELETE /items/:id/like;
 * Fallback: PATCH /items/:id { like: false }.
 */
export async function dislikeItem(id) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("dislikeItem requiere un id válido.");
  }

  // Endpoint específico
  try {
    const { data } = await api.post(`/items/${id}/dislike`);
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  // Algunas APIs usan DELETE al recurso like
  try {
    const { data } = await api.delete(`/items/${id}/like`);
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  // Fallback genérico
  const { data } = await api.patch(`/items/${id}`, { like: false });
  return data;
}

/* ========== Export organizado (cumple ESLint: no anonymous default) ========== */

export const boeService = {
  getItems,
  getItemById,
  getResumen,
  getImpacto,
  getComments,
  postComment,
  likeItem,
  dislikeItem,
};

export default boeService;
