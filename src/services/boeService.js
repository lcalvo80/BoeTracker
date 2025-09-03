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

/** Lista de items con filtros (acepta `config`, ej. { signal }). */
export async function getItems(params = {}, config = {}) {
  const finalParams = mapFilters(params);
  const { data } = await api.get("/items", { params: finalParams, ...config });
  return data;
}

/** Detalle de un item por ID. */
export async function getItemById(id, config = {}) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("getItemById requiere un id válido.");
  }
  const { data } = await api.get(`/items/${id}`, { ...config });
  return data;
}

/** Resumen de un item por ID. */
export async function getResumen(id, config = {}) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("getResumen requiere un id válido.");
  }

  try {
    const { data } = await api.get(`/items/${id}/resumen`, { ...config });
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  const detalle = await getItemById(id, config);
  if (detalle && (detalle.resumen ?? detalle.summary)) {
    return detalle.resumen ?? detalle.summary;
  }
  return { resumen: null };
}

/** Impacto de un item por ID. */
export async function getImpacto(id, config = {}) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("getImpacto requiere un id válido.");
  }

  try {
    const { data } = await api.get(`/items/${id}/impacto`, { ...config });
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  const detalle = await getItemById(id, config);
  if (detalle && (detalle.impacto ?? detalle.impact)) {
    return detalle.impacto ?? detalle.impact;
  }
  return { impacto: null };
}

/**
 * Comentarios de un item por ID.
 * Acepta paginación/filtros en `params` y `config` (ej. { signal }).
 */
export async function getComments(id, params = {}, config = {}) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("getComments requiere un id válido.");
  }

  try {
    const { data } = await api.get(`/items/${id}/comments`, { params, ...config });
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  try {
    const { data } = await api.get(`/items/${id}/comentarios`, { params, ...config });
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  const detalle = await getItemById(id, config);
  if (detalle && (detalle.comments ?? detalle.comentarios)) {
    return detalle.comments ?? detalle.comentarios;
  }
  return [];
}

/* =================== Escritura/acciones =================== */

/** Publica un comentario en un item. */
export async function postComment(id, text, extra = {}, config = {}) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("postComment requiere un id válido.");
  }
  if (!text || `${text}`.trim() === "") {
    throw new Error("postComment requiere un texto no vacío.");
  }

  const payload = { text, ...extra };

  try {
    const { data } = await api.post(`/items/${id}/comments`, payload, { ...config });
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  const { data } = await api.post(`/items/${id}/comentarios`, payload, { ...config });
  return data;
}

/** Marca like en un item. */
export async function likeItem(id, config = {}) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("likeItem requiere un id válido.");
  }

  try {
    const { data } = await api.post(`/items/${id}/like`, null, { ...config });
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  try {
    const { data } = await api.post(`/items/${id}/likes`, null, { ...config });
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  const { data } = await api.patch(`/items/${id}`, { like: true }, { ...config });
  return data;
}

/** Quita like / marca dislike en un item. */
export async function dislikeItem(id, config = {}) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("dislikeItem requiere un id válido.");
  }

  try {
    const { data } = await api.post(`/items/${id}/dislike`, null, { ...config });
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  try {
    const { data } = await api.delete(`/items/${id}/like`, { ...config });
    return data;
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }

  const { data } = await api.patch(`/items/${id}`, { like: false }, { ...config });
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
