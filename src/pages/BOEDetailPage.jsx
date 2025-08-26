import React, { useEffect, useMemo, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  getItemById,
  getResumen,
  getImpacto,
  getComments,
  postComment,
  likeItem,
  dislikeItem,
} from "../services/boeService";
import pako from "pako";
import Section from "../components/ui/Section";
import MetaChip from "../components/ui/MetaChip";

/* ========== Utils ========== */

const formatDateEsLong = (dateObj) =>
  new Intl.DateTimeFormat("es-ES", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "Europe/Madrid",
  }).format(dateObj);

const getPublishedDate = (item) => {
  if (item?.created_at) {
    const d = new Date(item.created_at);
    if (!isNaN(d)) return formatDateEsLong(d);
  }
  const day = item?.created_at_date || item?.fecha_publicacion;
  if (day && /^\d{4}-\d{2}-\d{2}$/.test(day)) {
    const [y, m, d] = day.split("-").map(Number);
    return formatDateEsLong(new Date(Date.UTC(y, m - 1, d)));
  }
  return "—";
};

const isProbablyBase64Gzip = (s = "") =>
  typeof s === "string" &&
  s.length > 8 &&
  (s.startsWith("H4sIA") || /^[A-Za-z0-9+/]+={0,2}$/.test(s));

const decodeBase64ToUint8 = (b64) => {
  const bin = atob(b64);
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
};

const maybeInflateBase64Gzip = (s) => {
  try {
    if (!isProbablyBase64Gzip(s)) return s;
    const bytes = decodeBase64ToUint8(s);
    return pako.ungzip(bytes, { to: "string" }) || s;
  } catch {
    return s;
  }
};

const normalizeTextBlock = (raw) => {
  if (raw == null) return "";
  if (typeof raw === "string") return raw;
  if (typeof raw === "object") {
    return (
      raw.resumen ||
      raw.impacto ||
      raw.texto ||
      raw.content ||
      raw.body ||
      JSON.stringify(raw)
    );
  }
  return String(raw);
};

const Skeleton = ({ className = "" }) => (
  <div
    className={`animate-pulse bg-gray-200 rounded ${className}`}
    role="status"
    aria-label="cargando..."
  />
);

const titleCaseFromKey = (key) =>
  String(key)
    .replace(/[_\-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (m) => m.toUpperCase());

const renderValue = (val) => {
  if (Array.isArray(val)) {
    return (
      <ul className="list-disc pl-6 text-gray-800">
        {val.map((v, i) => (
          <li key={i}>
            {typeof v === "string" ? v : typeof v === "number" ? String(v) : JSON.stringify(v)}
          </li>
        ))}
      </ul>
    );
  }
  if (typeof val === "object" && val !== null) {
    const entries = Object.entries(val);
    return (
      <div className="space-y-2">
        {entries.map(([k, v], i) => (
          <div key={i}>
            <div className="font-medium">{titleCaseFromKey(k)}</div>
            <div className="text-gray-800">
              {typeof v === "string" ? (
                <p className="whitespace-pre-line">{v}</p>
              ) : Array.isArray(v) ? (
                <ul className="list-disc pl-6 text-gray-800">
                  {v.map((x, j) => (
                    <li key={j}>{typeof x === "string" ? x : JSON.stringify(x)}</li>
                  ))}
                </ul>
              ) : (
                <pre className="bg-gray-50 p-2 rounded text-sm overflow-x-auto">
                  {JSON.stringify(v, null, 2)}
                </pre>
              )}
            </div>
          </div>
        ))}
      </div>
    );
  }
  return <p className="prose max-w-none text-gray-800 whitespace-pre-line">{String(val)}</p>;
};

/* ======= Resumen JSON → subsecciones ======= */
const renderResumen = (resumenStr) => {
  let parsed = null;
  try {
    parsed = JSON.parse(resumenStr);
  } catch {
    parsed = null;
  }

  if (!parsed || typeof parsed !== "object") {
    return (
      <article className="prose max-w-none text-gray-800 whitespace-pre-line">
        {resumenStr}
      </article>
    );
  }

  const ctx = parsed.context || parsed.Context || parsed.CONTEXTO;
  const keyChanges = parsed.key_changes || parsed["key_changes"] || parsed["key\\_changes"] || [];
  const keyDates =
    parsed.key_dates_events || parsed["key_dates_events"] || parsed["key\\_dates\\_events"] || [];
  const conclusion = parsed.conclusion || parsed.Conclusion || parsed.CONCLUSION;

  return (
    <div className="space-y-4">
      {ctx && (
        <Section title="Contexto" defaultOpen>
          {renderValue(ctx)}
        </Section>
      )}
      {Array.isArray(keyChanges) && keyChanges.length > 0 && (
        <Section title="Cambios clave" defaultOpen={false}>
          {renderValue(keyChanges)}
        </Section>
      )}
      {Array.isArray(keyDates) && keyDates.length > 0 && (
        <Section title="Fechas y eventos" defaultOpen={false}>
          {renderValue(keyDates)}
        </Section>
      )}
      {conclusion && (
        <Section title="Conclusión" defaultOpen={false}>
          {renderValue(conclusion)}
        </Section>
      )}
    </div>
  );
};

/* ======= Impacto JSON → subsecciones ======= */
const renderImpacto = (impactoStr) => {
  let parsed = null;
  try {
    parsed = JSON.parse(impactoStr);
  } catch {
    parsed = null;
  }

  if (!parsed || typeof parsed !== "object") {
    return (
      <article className="prose max-w-none text-gray-800 whitespace-pre-line">
        {impactoStr}
      </article>
    );
  }

  const aliases = {
    ambitos: ["ambitos", "ámbitos", "scope", "scopes"],
    costes: ["costes", "costos", "coste", "cost", "costs"],
    beneficios: ["beneficios", "benefits"],
    efectos: ["efectos", "effects", "impactos", "impacts"],
    cargas_administrativas: [
      "cargas_administrativas",
      "carga_administrativa",
      "administrative_burden",
    ],
    colectivos_afectados: [
      "colectivos_afectados",
      "partes_afectadas",
      "stakeholders",
      "affected_parties",
    ],
    indicadores_seguimiento: [
      "indicadores_seguimiento",
      "indicadores",
      "kpi",
      "kpis",
      "followup_indicators",
    ],
    riesgos: ["riesgos", "risks"],
    mitigaciones: ["mitigaciones", "mitigation", "mitigations"],
    calendario: ["calendario", "timeline", "cronograma", "schedule"],
    presupuesto: ["presupuesto", "budget", "financiacion", "financiación", "funding"],
    cumplimiento: ["cumplimiento", "compliance", "compatibilidad_normativa"],
  };

  const pick = (names) => {
    for (const n of names) {
      if (Object.prototype.hasOwnProperty.call(parsed, n)) return parsed[n];
      if (Object.prototype.hasOwnProperty.call(parsed, n.replace(/\\_/g, "_")))
        return parsed[n.replace(/\\_/g, "_")];
    }
    return undefined;
  };

  const sections = [
    ["Ámbitos", pick(aliases.ambitos)],
    ["Costes", pick(aliases.costes)],
    ["Beneficios", pick(aliases.beneficios)],
    ["Efectos", pick(aliases.efectos)],
    ["Cargas administrativas", pick(aliases.cargas_administrativas)],
    ["Colectivos afectados", pick(aliases.colectivos_afectados)],
    ["Indicadores de seguimiento", pick(aliases.indicadores_seguimiento)],
    ["Riesgos", pick(aliases.riesgos)],
    ["Mitigaciones", pick(aliases.mitigaciones)],
    ["Calendario", pick(aliases.calendario)],
    ["Presupuesto", pick(aliases.presupuesto)],
    ["Cumplimiento", pick(aliases.cumplimiento)],
  ];

  const knownKeys = new Set(Object.values(aliases).flat().map((k) => k.replace(/\\_/g, "_")));
  const extraEntries = Object.entries(parsed).filter(
    ([k]) => !knownKeys.has(k.replace(/\\_/g, "_"))
  );

  return (
    <div className="space-y-4">
      {sections.map(([title, val]) =>
        val == null || (Array.isArray(val) && val.length === 0) ? null : (
          <Section key={title} title={title} defaultOpen={false}>
            {renderValue(val)}
          </Section>
        )
      )}

      {extraEntries.length > 0 && (
        <Section title="Otros detalles" defaultOpen={false}>
          <div className="space-y-4">
            {extraEntries.map(([k, v]) => (
              <Section key={k} title={titleCaseFromKey(k)} defaultOpen={false}>
                {renderValue(v)}
              </Section>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
};

/* ========== Página ========== */

const BOEDetailPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const ident = useMemo(() => decodeURIComponent(id || ""), [id]);

  const [loading, setLoading] = useState(true);
  const [loadingComments, setLoadingComments] = useState(true);

  const [item, setItem] = useState(null);
  const [resumen, setResumen] = useState("");
  const [impacto, setImpacto] = useState("");
  const [error, setError] = useState("");

  const [likes, setLikes] = useState(null);
  const [dislikes, setDislikes] = useState(null);

  const [comments, setComments] = useState([]);
  const [commentsMeta, setCommentsMeta] = useState({ page: 1, pages: 0, total: 0 });
  const [commentForm, setCommentForm] = useState({ author: "", text: "" });
  const [commentError, setCommentError] = useState("");
  const [commentSending, setCommentSending] = useState(false);

  /* Cargar detalle + resumen/impacto */
  useEffect(() => {
    let isMounted = true;
    const load = async () => {
      setLoading(true);
      setError("");
      try {
        const itemRes = await getItemById(ident);
        const data = itemRes?.data || null;
        if (!data) throw new Error("No se encontró el elemento");
        if (!isMounted) return;

        setItem(data);
        setLikes(Number.isFinite(data?.likes) ? data.likes : 0);
        setDislikes(Number.isFinite(data?.dislikes) ? data.dislikes : 0);

        const [r, im] = await Promise.allSettled([getResumen(ident), getImpacto(ident)]);
        if (!isMounted) return;

        const resumenRaw = r.status === "fulfilled" ? r.value?.data?.resumen ?? r.value?.data : "";
        const impactoRaw = im.status === "fulfilled" ? im.value?.data?.impacto ?? im.value?.data : "";

        const resumenText = maybeInflateBase64Gzip(normalizeTextBlock(resumenRaw));
        const impactoText = maybeInflateBase64Gzip(normalizeTextBlock(impactoRaw));

        setResumen(resumenText);
        setImpacto(impactoText);
      } catch (e) {
        if (!isMounted) return;
        setError(
          e?.response?.data?.detail ||
            e?.response?.data?.error ||
            e?.message ||
            "No se pudo cargar el detalle."
        );
      } finally {
        if (isMounted) setLoading(false);
      }
    };
    if (ident) load();
    return () => {
      isMounted = false;
    };
  }, [ident]);

  /* Cargar comentarios */
  const fetchComments = useCallback(
    async (page = 1) => {
      setLoadingComments(true);
      setCommentError("");
      try {
        const { data } = await getComments(ident, { page, limit: 20 });
        const items = Array.isArray(data?.items) ? data.items : [];
        setComments(items);
        setCommentsMeta({
          page: Number.isFinite(data?.page) ? data.page : 1,
          pages: Number.isFinite(data?.pages) ? data.pages : 0,
          total: Number.isFinite(data?.total) ? data.total : items.length,
        });
      } catch (e) {
        setComments([]);
        setCommentsMeta({ page: 1, pages: 0, total: 0 });
        setCommentError(
          e?.response?.data?.detail ||
            e?.response?.data?.error ||
            "Los comentarios no están disponibles."
        );
      } finally {
        setLoadingComments(false);
      }
    },
    [ident]
  );

  useEffect(() => {
    if (ident) fetchComments(1);
  }, [ident, fetchComments]);

  /* Acciones */
  const onLike = async () => {
    setLikes((v) => (Number.isFinite(v) ? v + 1 : 1));
    try {
      const { data } = await likeItem(ident);
      if (Number.isFinite(data?.likes)) setLikes(data.likes);
    } catch {
      setLikes((v) => Math.max(0, (Number.isFinite(v) ? v : 1) - 1));
    }
  };

  const onDislike = async () => {
    setDislikes((v) => (Number.isFinite(v) ? v + 1 : 1));
    try {
      const { data } = await dislikeItem(ident);
      if (Number.isFinite(data?.dislikes)) setDislikes(data.dislikes);
    } catch {
      setDislikes((v) => Math.max(0, (Number.isFinite(v) ? v : 1) - 1));
    }
  };

  const submitComment = async (e) => {
    e.preventDefault();
    setCommentError("");
    setCommentSending(true);
    try {
      const payload = {
        author: commentForm.author?.trim() || "Anónimo",
        text: (commentForm.text || "").trim(),
      };
      if (!payload.text) {
        setCommentError("Escribe un comentario.");
        setCommentSending(false);
        return;
      }
      await postComment(ident, payload);
      setCommentForm({ author: "", text: "" });
      fetchComments(1);
    } catch (err) {
      setCommentError(
        err?.error || err?.detail || err?.message || "No se pudo enviar el comentario."
      );
    } finally {
      setCommentSending(false);
    }
  };

  /* Render */
  if (loading) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <Skeleton className="h-6 w-40 mb-4" />
        <Skeleton className="h-8 w-3/4 mb-2" />
        <Skeleton className="h-4 w-1/2 mb-6" />
        <Skeleton className="h-24 w-full mb-4" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (error || !item) {
    return (
      <div className="p-6 max-w-3xl mx-auto">
        <button onClick={() => navigate(-1)} className="text-sm text-blue-700 hover:underline mb-4">
          ← Volver
        </button>
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
          {error || "No se encontró el elemento."}
        </div>
      </div>
    );
  }

  const tituloPrincipal = item.titulo_resumen || item.titulo || "(Sin título)";
  const fechaPub = getPublishedDate(item);

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      <button onClick={() => navigate(-1)} className="text-sm text-blue-700 hover:underline">
        ← Volver
      </button>

      {/* Cabecera */}
      <header className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <h1 className="text-2xl font-bold text-gray-900">{tituloPrincipal}</h1>
        <p className="mt-1 text-sm font-medium text-gray-500">{item.identificador}</p>

        <div className="mt-4 flex flex-wrap gap-2">
          <MetaChip>Sección: {item.seccion_nombre || item.seccion_codigo || "—"}</MetaChip>
          <MetaChip>Departamento: {item.departamento_nombre || item.departamento_codigo || "—"}</MetaChip>
          <MetaChip>Epígrafe: {item.epigrafe || "—"}</MetaChip>
          <MetaChip>Fecha: {fechaPub}</MetaChip>
          {item.control && <MetaChip>Control: {item.control}</MetaChip>}
        </div>

        <div className="mt-5 flex items-center gap-2">
          <button
            onClick={onLike}
            className="inline-flex items-center gap-2 rounded-lg bg-green-600 px-3 py-1.5 text-white hover:bg-green-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-600/60"
          >
            👍 <span className="text-sm">Me interesa {Number.isFinite(likes) ? `(${likes})` : ""}</span>
          </button>
          <button
            onClick={onDislike}
            className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-3 py-1.5 text-white hover:bg-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-600/60"
          >
            👎 <span className="text-sm">No me interesa {Number.isFinite(dislikes) ? `(${dislikes})` : ""}</span>
          </button>
        </div>
      </header>

      {/* Resumen */}
      {resumen ? (
        <Section title="Resumen" defaultOpen={true}>
          {renderResumen(resumen)}
        </Section>
      ) : null}

      {/* Informe de impacto */}
      {impacto ? (
        <Section title="Informe de impacto" defaultOpen={false}>
          {renderImpacto(impacto)}
        </Section>
      ) : null}

      {/* Título completo */}
      {item.titulo ? (
        <Section title="Título completo" defaultOpen={false}>
          <article className="prose max-w-none text-gray-800 whitespace-pre-line">
            {item.titulo}
          </article>
        </Section>
      ) : null}

      {/* Comentarios */}
      <section className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Comentarios</h2>
          {!loadingComments && commentsMeta?.total > 0 ? (
            <span className="text-sm text-gray-500">{commentsMeta.total} comentario(s)</span>
          ) : null}
        </div>

        {loadingComments ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        ) : commentError ? (
          <div className="bg-yellow-50 border border-yellow-200 text-yellow-800 px-3 py-2 rounded">
            {commentError}
          </div>
        ) : comments.length === 0 ? (
          <p className="text-gray-500">No hay comentarios aún.</p>
        ) : (
          <ul className="space-y-3">
            {comments.map((c, idx) => (
              <li key={c.id || idx} className="rounded-xl border border-gray-100 bg-white p-4 shadow-sm">
                <div className="flex items-center justify-between text-sm">
                  <span className="font-semibold text-gray-800">{c.author || "Anónimo"}</span>
                  <time className="text-gray-500">
                    {c.created_at ? formatDateEsLong(new Date(c.created_at)) : ""}
                  </time>
                </div>
                <p className="mt-2 text-gray-800 whitespace-pre-line">
                  {c.text || c.content || c.comentario || ""}
                </p>
              </li>
            ))}
          </ul>
        )}

        {/* Paginación de comentarios */}
        {!loadingComments && commentsMeta.pages > 1 ? (
          <div className="flex gap-1 mt-3">
            <button
              disabled={commentsMeta.page <= 1}
              onClick={() => fetchComments(commentsMeta.page - 1)}
              className="px-2 py-1 text-sm border rounded disabled:opacity-40 hover:bg-gray-100"
              aria-label="Comentarios anteriores"
            >
              ←
            </button>
            {Array.from({ length: commentsMeta.pages }, (_, i) => i + 1)
              .filter(
                (p) =>
                  p === 1 || p === commentsMeta.pages || Math.abs(commentsMeta.page - p) <= 2
              )
              .map((p) => (
                <button
                  key={p}
                  onClick={() => fetchComments(p)}
                  className={`px-2 py-1 text-sm border rounded ${
                    p === commentsMeta.page ? "bg-blue-600 text-white" : "hover:bg-gray-100"
                  }`}
                  aria-current={p === commentsMeta.page ? "page" : undefined}
                >
                  {p}
                </button>
              ))}
            <button
              disabled={commentsMeta.page >= commentsMeta.pages}
              onClick={() => fetchComments(commentsMeta.page + 1)}
              className="px-2 py-1 text-sm border rounded disabled:opacity-40 hover:bg-gray-100"
              aria-label="Comentarios siguientes"
            >
              →
            </button>
          </div>
        ) : null}

        {/* Formulario de comentario */}
        <form onSubmit={submitComment} className="mt-5 space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="md:col-span-1">
              <label className="text-sm font-medium text-gray-700 mb-1 block">Autor</label>
              <input
                type="text"
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600/60"
                placeholder="Tu nombre (opcional)"
                value={commentForm.author}
                onChange={(e) => setCommentForm((p) => ({ ...p, author: e.target.value }))}
              />
            </div>
            <div className="md:col-span-2">
              <label className="text-sm font-medium text-gray-700 mb-1 block">Comentario</label>
              <textarea
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600/60 min-h-[96px]"
                placeholder="Escribe tu comentario…"
                value={commentForm.text}
                onChange={(e) => setCommentForm((p) => ({ ...p, text: e.target.value }))}
                aria-invalid={!!commentError}
              />
            </div>
          </div>
          {commentError && <div className="text-sm text-red-600">{commentError}</div>}
          <div>
            <button
              type="submit"
              disabled={commentSending}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {commentSending ? "Enviando…" : "Enviar comentario"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
};

export default BOEDetailPage;
