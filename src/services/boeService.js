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
 * Intenta primero /items/:id/resumen; si no existe, usa /items/:id y
 * devuelve data.resumen || data.summary si están presentes.
 * @param {string|number} id
 * @returns {Promise<any>}
 */
export async function getResumen(id) {
  if (id === undefined || id === null || `${id}`.trim() === "") {
    throw new Error("getResumen requiere un id válido.");
  }

  // 1) Endpoint específico si existe en el backend
  try {
    const { data } = await api.get(`/items/${id}/resumen`);
    return data;
  } catch (err) {
    // Si no es 404/endpoint inexistente, relanza
    const status = err?.response?.status;
    if (status && status !== 404) {
      throw err;
    }
  }

  // 2) Fallback: usar el detalle y extraer resumen
  const detalle = await getItemById(id);
  if (detalle && (detalle.resumen ?? detalle.summary)) {
    return detalle.resumen ?? detalle.summary;
  }

  // 3) Si no hay resumen, devuelve algo consistente
  return { resumen: null };
}

// (opcional) export por defecto para ambos estilos de import
export const boeService = {
  getItems,
  getItemById,
  getResumen,
};

export default boeService;