// src/services/boeService.js
import { get } from "./http";

const DEV_DEBUG = process.env.NODE_ENV === "development";

/** Util: fecha a YYYY-MM-DD (zona Europe/Madrid) */
const toISO = (d) =>
  d instanceof Date && !isNaN(d)
    ? d.toLocaleDateString("sv-SE", { timeZone: "Europe/Madrid" })
    : null;

/**
 * Construye URLSearchParams cumpliendo el contrato del backend:
 *  - Arrays -> claves repetidas (?secciones=I&secciones=II)
 *  - Texto -> 'q' (no 'q_adv')
 *  - Fecha exacta -> duplica en fecha_desde/fecha_hasta
 */
export function buildSearchParams(filters = {}) {
  const p = new URLSearchParams();

  const appendMulti = (key, arr) => {
    (arr || []).forEach((v) => {
      const s = `${v ?? ""}`.trim();
      if (s) p.append(key, s);
    });
  };

  // Texto
  if (filters.q?.trim()) p.set("q", filters.q.trim());
  if (filters.identificador?.trim()) p.set("identificador", filters.identificador.trim());
  if (filters.control?.trim()) p.set("control", filters.control.trim());

  // Arrays (claves repetidas)
  appendMulti("secciones", filters.secciones);
  appendMulti("departamentos", filters.departamentos);
  appendMulti("epigrafes", filters.epigrafes);
  appendMulti("tags", filters.tags);
  appendMulti("ids", filters.ids);

  // Fechas
  if (filters.useRange) {
    const fd = toISO(filters.fecha_desde);
    const fh = toISO(filters.fecha_hasta);
    if (fd) p.set("fecha_desde", fd);
    if (fh) p.set("fecha_hasta", fh);
  } else {
    const f = toISO(filters.fecha);
    if (f) {
      // Fecha exacta -> mismo día en desde/hasta
      p.set("fecha_desde", f);
      p.set("fecha_hasta", f);
    }
  }

  // Paginación / orden
  p.set("page", String(filters.page ?? 1));
  p.set("limit", String(filters.limit ?? 12));
  p.set("sort_by", filters.sort_by || "created_at");
  p.set("sort_dir", filters.sort_dir || "desc");

  return p;
}

/** Listado con filtros */
export async function getItems(filters = {}) {
  const params = buildSearchParams(filters);
  const headers = DEV_DEBUG ? { "X-Debug-Filters": "1" } : undefined;
  // Usamos get(path + query) para controlar la serialización exacta
  return get(`/items?${params.toString()}`, { headers });
}

/** Catálogos: devolvemos arrays simples */
export async function getFilterOptions() {
  const [departamentos, secciones, epigrafes] = await Promise.all([
    get(`/items/departamentos`).catch(() => []),
    get(`/items/secciones`).catch(() => []),
    get(`/items/epigrafes`).catch(() => []),
  ]);
  return {
    departamentos: Array.isArray(departamentos) ? departamentos : [],
    secciones: Array.isArray(secciones) ? secciones : [],
    epigrafes: Array.isArray(epigrafes) ? epigrafes : [],
  };
}

/** Detalle */
export async function getItemById(identificador) {
  return get(`/items/${encodeURIComponent(identificador)}`);
}
export async function getResumen(identificador) {
  return get(`/items/${encodeURIComponent(identificador)}/resumen`);
}
export async function getImpacto(identificador) {
  return get(`/items/${encodeURIComponent(identificador)}/impacto`);
}

/** Reacciones */
export async function likeItem(identificador) {
  // si prefieres usar post de http.js, impórtalo también
  return get(`/items/${encodeURIComponent(identificador)}/like`, { method: "POST" });
}
export async function dislikeItem(identificador) {
  return get(`/items/${encodeURIComponent(identificador)}/dislike`, { method: "POST" });
}

/** Comentarios con fallback tolerante */
export async function getComments(identificador, params = { page: 1, limit: 20 }) {
  try {
    const search = new URLSearchParams({ page: String(params.page || 1), limit: String(params.limit || 20) });
    const data = await get(`/items/${encodeURIComponent(identificador)}/comments?${search.toString()}`);

    if (Array.isArray(data)) {
      return { items: data, total: data.length, page: 1, pages: 1 };
    }
    if (data && typeof data === "object" && Array.isArray(data.items) && typeof data.total === "number") {
      return data;
    }
    return { items: [], total: 0, page: 1, pages: 0 };
  } catch (err) {
    console.warn("getComments fallback:", err?.response?.status || err?.message);
    return { items: [], total: 0, page: 1, pages: 0 };
  }
}

export async function postComment(identificador, payload) {
  try {
    // fetch con POST simple usando http.js sería mejor; aquí usamos fetch directo por simplicidad
    const res = await fetch(`/api/items/${encodeURIComponent(identificador)}/comments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    if (!res.ok) throw await res.json().catch(() => ({}));
    return res.json();
  } catch (err) {
    return Promise.reject(err || { error: "No se pudo enviar el comentario" });
  }
}