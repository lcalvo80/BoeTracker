// src/services/boeService.js
import { api } from "./http";

/**
 * ============================
 * LISTADO CON FILTROS
 * ============================
 * params esperados (todos opcionales):
 * - page, limit
 * - q_adv, identificador, control
 * - seccion (CSV de códigos), departamento (CSV), epigrafe (CSV)
 * - fecha (YYYY-MM-DD)  |  fecha_desde, fecha_hasta (rango)
 * - sort_by, sort_dir
 */
export const getItems = (params) => api.get("/items", { params });

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
 * Uso en UI:
 *   const { departamentos, secciones, epigrafes } = await getFilterOptions();
 *   setOptions({
 *     departamentos: departamentos.data ?? [],
 *     secciones: secciones.data ?? [],
 *     epigrafes: epigrafes.data ?? [],
 *   })
 */
export const getFilterOptions = async () => {
  const [deps, secs, epis] = await Promise.allSettled([
    api.get("/items/departamentos"),
    api.get("/items/secciones"),
    api.get("/items/epigrafes"),
  ]);

  const ok = (e) => e.status === "fulfilled";
  const safe = (e, fallback) => (ok(e) ? e.value : fallback);

  return {
    departamentos: safe(deps, { data: [] }),
    secciones: safe(secs, { data: [] }),
    epigrafes: safe(epis, { data: [] }),
  };
};

/**
 * (Compat) Si en alguna parte aún esperas array:
 *   const { departamentos, epigrafes, secciones } = await getFilterOptions();
 *   // O bien:
 *   const [d, e, s] = await getFilterOptionsArray();
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
export const getItemById = (identificador) =>
  api.get(`/items/${encodeURIComponent(identificador)}`);

export const getResumen = (identificador) =>
  api.get(`/items/${encodeURIComponent(identificador)}/resumen`);

export const getImpacto = (identificador) =>
  api.get(`/items/${encodeURIComponent(identificador)}/impacto`);

/**
 * ============================
 * REACCIONES
 * ============================
 */
export const likeItem = (identificador) =>
  api.post(`/items/${encodeURIComponent(identificador)}/like`);

export const dislikeItem = (identificador) =>
  api.post(`/items/${encodeURIComponent(identificador)}/dislike`);

/**
 * ============================
 * COMENTARIOS
 * ============================
 * Tolerante a backends sin /comments:
 * - Si falla, devuelve { data: { items: [], total: 0, page: 1, pages: 0 } }
 * - Si backend devuelve un array simple, normaliza a ese shape.
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
      return { data: { items: data, total: data.length, page: 1, pages: 1 } };
    }
    // Se espera { items, total, page, pages }
    return res;
  } catch (err) {
    console.warn("getComments fallback:", err?.response?.status || err?.message);
    return { data: { items: [], total: 0, page: 1, pages: 0 } };
  }
};

export const postComment = async (identificador, payload) => {
  try {
    // payload sugerido: { author, text }
    return await api.post(
      `/items/${encodeURIComponent(identificador)}/comments`,
      payload
    );
  } catch (err) {
    // Rechaza con un objeto "amigable" para UI
    return Promise.reject(
      err?.response?.data || { error: "No se pudo enviar el comentario" }
    );
  }
};
