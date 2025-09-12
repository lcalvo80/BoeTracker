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

/** Normaliza las respuestas de filtros a un contrato estable en el frontend. */
function normalizeFilters(raw = {}) {
  const secciones =
    raw.secciones ??
    raw.sections ??
    raw.seccion ??
    raw.section ??
    [];
  const departamentos =
    raw.departamentos ??
    raw.agencias ??
    raw.departments ??
    raw.agencies ??
    [];
  const epigrafes =
    raw.epigrafes ??
    raw.epigrafe ??
    raw.topics ??
    raw.tags ??
    [];
  const years =
    raw.years ??
    raw.anios ??
    raw.años ??
    [];

  return { secciones, departamentos, epigrafes, years };
}

/* =================== Lectura =================== */

/** Lista de items con filtros (acepta `config`, ej. { signal }). */
export async function getItems(params = {}, config = {}) {
  const finalParams = mapFilters(params);
  const { data } = await api.get("/items", { params: finalParams, ...config });
  return data;
}

/** Detalle de un item por ID (colección items). */
export async function getItemById(id, config = {}) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("getItemById requiere un id válido.");
  }
  const { data } = await api.get(`/items/${id}`, { ...config });
  return data;
}

/** Detalle BOE por identificador oficial (endpoint dedicado). */
export async function getBoeById(id, config = {}) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("getBoeById requiere un id válido.");
  }
  const { data } = await api.get(`/boe/${encodeURIComponent(id)}`, { ...config });
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

/** ================== Filtros ================== */
export async function getFilterOptions(config = {}) {
  // 1) /filters
  try {
    const { data } = await api.get("/filters", { ...config });
    return normalizeFilters(data);
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }
  // 2) /filtros
  try {
    const { data } = await api.get("/filtros", { ...config });
    return normalizeFilters(data);
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }
  // 3) /meta/filters
  try {
    const { data } = await api.get("/meta/filters", { ...config });
    return normalizeFilters(data);
  } catch (err) {
    const status = err?.response?.status;
    if (status && status !== 404) throw err;
  }
  // 4) fallback vacío
  return normalizeFilters({
    secciones: [],
    departamentos: [],
    epigrafes: [],
    years: [],
  });
}

/* ========== Export organizado ========== */
export const boeService = {
  getItems,
  getItemById,
  getBoeById,
  getResumen,
  getImpacto,
  getComments,
  getFilterOptions,
};

export default boeService;
