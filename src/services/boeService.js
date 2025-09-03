// src/services/boeService.js
import api from "./http"; // permite tanto `import api from` como `{ api }` (exportamos ambos en http.js)

/**
 * ============================
 * LISTADO CON FILTROS
 * ============================
 * @param {Object} params
 * @param {number} [params.page]
 * @param {number} [params.limit]
 * @param {string} [params.q_adv]
 * @param {string} [params.identificador]
 * @param {string} [params.control]
 * @param {string} [params.seccion]        CSV de códigos
 * @param {string} [params.departamento]   CSV de códigos
 * @param {string} [params.epigrafe]       CSV de códigos
 * @param {string} [params.fecha]          YYYY-MM-DD
 * @param {string} [params.fecha_desde]    YYYY-MM-DD
 * @param {string} [params.fecha_hasta]    YYYY-MM-DD
 * @param {string} [params.sort_by]
 * @param {('asc'|'desc')} [params.sort_dir]
 * @returns {Promise<any>} data del backend
 */
export const getItems = async (params = {}) => {
  const { data } = await api.get("/items", { params });
  return data;
};

/**
 * ============================
 * OPCIONES DE FILTRADO
 * ============================
 * Devuelve SIEMPRE un objeto:
 * {
 *   departamentos: { data: [{codigo, nombre}, ...] },
 *   secciones:     { data: [{codigo, nombre}, ...] },
 *   epigrafes:     { data: ["...", "..."] }
 * }
 */
export const getFilterOptions = async () => {
  const [deps, secs, epis] = await Promise.allSettled([
    api.get("/items/departamentos"),
    api.get("/items/secciones"),
    api.get("/items/epigrafes"),
  ]);

  const ok = (e) => e.status === "fulfilled";
  const toData = (e) => (ok(e) ? e.value?.data : undefined);

  // Normalizamos para garantizar { data: [] } en cada bloque
  const departamentos = toData(deps);
  const secciones = toData(secs);
  const epigrafes = toData(epis);

  return {
    departamentos: Array.isArray(departamentos)
      ? { data: departamentos }
      : departamentos && typeof departamentos === "object" && Array.isArray(departamentos.data)
      ? departamentos
      : { data: [] },

    secciones: Array.isArray(secciones)
      ? { data: secciones }
      : secciones && typeof secciones === "object" && Array.isArray(secciones.data)
      ? secciones
      : { data: [] },

    epigrafes: Array.isArray(epigrafes)
      ? { data: epigrafes }
      : epigrafes && typeof epigrafes === "object" && Array.isArray(epigrafes.data)
      ? epigrafes
      : { data: [] },
  };
};

/**
 * Compat: devuelve [departamentos, epigrafes, secciones] en ese orden.
 * Cada elemento con shape { data: [...] }
 */
export const getFilterOptionsArray = async () => {
  const { departamentos, secciones, epigrafes } = await getFilterOptions();
  return [departamentos, epigrafes, secciones];
};

/**
 * ============================
 * DETALLE
 * ============================
 */
export const getItemById = async (identificador) => {
  const { data } = await api.get(`/items/${encodeURIComponent(identificador)}`);
  return data;
};

export const getResumen = async (identificador) => {
  const { data } = await api.get(
    `/items/${encodeURIComponent(identificador)}/resumen`
  );
  return data;
};

export const getImpacto = async (identificador) => {
  const { data } = await api.get(
    `/items/${encodeURIComponent(identificador)}/impacto`
  );
  return data;
};

/**
 * ============================
 * REACCIONES
 * ============================
 */
export const likeItem = async (identificador) => {
  const { data } = await api.post(
    `/items/${encodeURIComponent(identificador)}/like`
  );
  return data;
};

export const dislikeItem = async (identificador) => {
  const { data } = await api.post(
    `/items/${encodeURIComponent(identificador)}/dislike`
  );
  return data;
};

/**
 * ============================
 * COMENTARIOS
 * ============================
 * Tolerante a backends sin /comments:
 * - Si falla, devuelve { items: [], total: 0, page: 1, pages: 0 }
 * - Si backend devuelve un array simple, normaliza a ese shape.
 * @returns {Promise<{items: any[], total: number, page: number, pages: number}>}
 */
export const getComments = async (
  identificador,
  params = { page: 1, limit: 20 }
) => {
  try {
    const res = await api.get(
      `/items/${encodeURIComponent(identificador)}/comments`,
      { params }
    );
    const data = res?.data;

    if (Array.isArray(data)) {
      return { items: data, total: data.length, page: 1, pages: 1 };
    }
    // Si viene { items, total, page, pages }
    if (
      data &&
      typeof data === "object" &&
      Array.isArray(data.items) &&
      typeof data.total === "number"
    ) {
      return data;
    }
    // Fallback suave si el shape no es el esperado
    return { items: [], total: 0, page: 1, pages: 0 };
  } catch (err) {
    console.warn("getComments fallback:", err?.response?.status || err?.message);
    return { items: [], total: 0, page: 1, pages: 0 };
  }
};

export const postComment = async (identificador, payload) => {
  try {
    // payload sugerido: { author, text }
    const { data } = await api.post(
      `/items/${encodeURIComponent(identificador)}/comments`,
      payload
    );
    return data;
  } catch (err) {
    // Rechaza con un objeto "amigable" para UI
    return Promise.reject(
      err?.response?.data || { error: "No se pudo enviar el comentario" }
    );
  }
};
