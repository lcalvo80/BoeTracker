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

/**
 * Lista de items con filtros.
 * @param {Object} params
 * @returns {Promise<any>}
 */
export async function getItems(params = {}) {
  const finalParams = mapFilters(params);
  const { data } = await api.get("/items", { params: finalParams });
  return data;
}

/**
 * Detalle de un item por ID.
 * @param {string|number} id
 * @returns {Promise<any>}
 */
export async function getItemById(id) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("getItemById requiere un id válido.");
  }
  const { data } = await api.get(`/items/${id}`);
  return data;
}

/**
 * Resumen de un item por ID.
 * Intenta /items/:id/resumen; si no existe, usa /items/:id y
 * devuelve data.resumen || data.summary si están presentes.
 * @param {string|number} id
 * @returns {Promise<any>}
 */
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

/**
 * Impacto de un item por ID.
 * Intenta /items/:id/impacto; si no existe, usa /items/:id y
 * devuelve data.impacto || data.impact si están presentes.
 * @param {string|number} id
 * @returns {Promise<any>}
 */
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

/* ===== Export organizado para satisfacer ESLint (no anonymous default) ===== */

export const boeService = {
  getItems,
  getItemById,
  getResumen,
  getImpacto,
};

export default boeService;
