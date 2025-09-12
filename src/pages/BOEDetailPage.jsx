// src/pages/BOEDetailPage.jsx
import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import api from "../services/http";

/**
 * BOEDetailPage.jsx — UI alineada con mockups + backend /api/items
 * - Detalle, resumen, impacto, comentarios, likes/dislikes
 * - Acordeones, chips, botón PDF rojo, copiar
 * - Inflado base64+gzip para content/summary/html/epigrafe/títulos/impacto
 */

// =====================
// Utils: base64 + gzip
// =====================
let pakoRef = null;
async function getPako() {
  if (!pakoRef) pakoRef = (await import("pako")).default;
  return pakoRef;
}
function isProbablyBase64(s) {
  if (typeof s !== "string") return false;
  if (s.length < 8) return false;
  if (s.length % 4 !== 0) return false;
  return /^[A-Za-z0-9+/]+={0,2}$/.test(s);
}
function peekBase64Bytes(s, n = 2) {
  try {
    if (typeof window !== "undefined" && typeof atob === "function") {
      const chunk = atob(s.slice(0, 4 * Math.ceil(n / 3)));
      const out = new Uint8Array(chunk.length);
      for (let i = 0; i < chunk.length; i++) out[i] = chunk.charCodeAt(i);
      return out.slice(0, n);
    } else if (typeof Buffer !== "undefined") {
      return Buffer.from(s, "base64").subarray(0, n);
    }
  } catch {}
  return new Uint8Array(0);
}
function isProbablyBase64Gzip(s) {
  if (!isProbablyBase64(s)) return false;
  const head = peekBase64Bytes(s, 2);
  return head.length >= 2 && head[0] === 0x1f && head[1] === 0x8b;
}
function decodeBase64ToUint8(s) {
  if (typeof window !== "undefined" && typeof atob === "function") {
    const b = atob(s);
    const out = new Uint8Array(b.length);
    for (let i = 0; i < b.length; i++) out[i] = b.charCodeAt(i);
    return out;
  }
  return new Uint8Array(Buffer.from(s, "base64"));
}
const maybeInflateBase64Gzip = async (s) => {
  try {
    if (!s || typeof s !== "string") return s;
    if (!isProbablyBase64Gzip(s)) return s;
    const bytes = decodeBase64ToUint8(s);
    const pako = await getPako();
    return pako.ungzip(bytes, { to: "string" }) || s;
  } catch {
    return s;
  }
};

// Detecta si un string parece HTML para decidir render
const looksLikeHTML = (s) => typeof s === "string" && /<\/?[a-z][\s\S]*>/i.test(s);

// =====================
// Fetch helpers (backend /api/items)
// =====================
async function fetchDetail(id, signal) {
  const { data } = await api.get(`/api/items/${encodeURIComponent(id)}`, { signal });
  return data;
}
async function fetchResumen(id, signal) {
  try {
    const { data } = await api.get(`/api/items/${encodeURIComponent(id)}/resumen`, { signal });
    return data?.resumen ?? null;
  } catch { return null; }
}
async function fetchImpacto(id, signal) {
  try {
    const { data } = await api.get(`/api/items/${encodeURIComponent(id)}/impacto`, { signal });
    return data?.impacto ?? null;
  } catch { return null; }
}
async function fetchComments(id, page = 1, limit = 10, signal) {
  const { data } = await api.get(`/api/items/${encodeURIComponent(id)}/comments`, {
    params: { page, limit },
    signal,
  });
  return data || { items: [], page: 1, pages: 0, total: 0, limit };
}
async function postComment(id, payload) {
  const { data } = await api.post(`/api/items/${encodeURIComponent(id)}/comments`, payload);
  return data;
}
async function likeItem(id) {
  const { data } = await api.post(`/api/items/${encodeURIComponent(id)}/like`);
  return data;
}
async function dislikeItem(id) {
  const { data } = await api.post(`/api/items/${encodeURIComponent(id)}/dislike`);
  return data;
}

// =====================
// Vista principal
// =====================
export default function BOEDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [detail, setDetail] = useState(null);
  const [inflated, setInflated] = useState(null);
  const [summary, setSummary] = useState(null);
  const [impacto, setImpacto] = useState(null);

  const [likes, setLikes] = useState(null);
  const [dislikes, setDislikes] = useState(null);

  const [comments, setComments] = useState([]);
  const [cPage, setCPage] = useState(1);
  const [cPages, setCPages] = useState(0);
  const [cTotal, setCTotal] = useState(0);
  const [cLimit] = useState(10);
  const [addingComment, setAddingComment] = useState(false);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const controllerRef = useRef(null);

  // Permite override por query param ?endpoint=https://...
  const explicitEndpoint = searchParams.get("endpoint");
  const useExplicit = explicitEndpoint && /^https?:\/\//i.test(explicitEndpoint);

  // -------- Carga de detalle --------
  useEffect(() => {
    if (!id) return;
    controllerRef.current?.abort?.();
    const ac = new AbortController();
    controllerRef.current = ac;

    (async () => {
      setLoading(true);
      setError(null);
      try {
        let raw;
        if (useExplicit) {
          const { data } = await api.get(explicitEndpoint, { signal: ac.signal, baseURL: "" });
          raw = data;
        } else {
          raw = await fetchDetail(id, ac.signal);
        }

        // Normaliza nombres al shape del FE
        const norm = {
          identificador: raw.identificador ?? id,
          title: raw.titulo ?? raw.title ?? "",
          summary: raw.resumen ?? raw.summary ?? null,
          content: raw.contenido ?? raw.content ?? null,
          html: raw.html ?? null,
          epigrafe: raw.epigrafe ?? null,
          section: raw.seccion_nombre || raw.seccion || raw.seccion_codigo || null,
          departamento: raw.departamento_nombre || raw.departamento || raw.departamento_codigo || null,
          control: raw.control ?? null,
          created_at: raw.created_at ?? raw.fecha ?? null,
          url_pdf: raw.url_pdf ?? null,
          sourceUrl: raw.sourceUrl ?? raw.url_boe ?? null,
          metadata: raw.metadata || {
            departamento_codigo: raw.departamento_codigo,
            seccion_codigo: raw.seccion_codigo,
            epigrafe: raw.epigrafe,
            control: raw.control,
          },
          likes: raw.likes ?? null,
          dislikes: raw.dislikes ?? null,
          full_title: raw.titulo_completo ?? null,
          titulo_completo: raw.titulo_completo ?? null,
        };

        // Infla campos que puedan venir base64+gzip
        const toInflateKeys = ["content", "summary", "html", "epigrafe", "full_title", "titulo_completo"];
        for (const k of toInflateKeys) {
          if (typeof norm[k] === "string") {
            norm[k] = await maybeInflateBase64Gzip(norm[k]);
          }
        }

        setDetail(norm);
        setInflated(norm);
        setLikes(norm.likes);
        setDislikes(norm.dislikes);
      } catch (err) {
        if (err?.name !== "AbortError") setError(err);
      } finally {
        setLoading(false);
      }
    })();

    return () => ac.abort();
  }, [id, explicitEndpoint, useExplicit]);

  // -------- Carga de resumen & impacto (lazy) --------
  useEffect(() => {
    if (!id || useExplicit) return; // si llaman a un endpoint externo, no asumimos rutas derivadas
    const ac = new AbortController();
    (async () => {
      const [r, imp] = await Promise.allSettled([
        fetchResumen(id, ac.signal),
        fetchImpacto(id, ac.signal),
      ]);
      if (r.status === "fulfilled") {
        let val = r.value;
        if (typeof val === "string") val = await maybeInflateBase64Gzip(val);
        setSummary(val ?? null);
      }
      if (imp.status === "fulfilled") {
        let ival = imp.value;
        if (typeof ival === "string") ival = await maybeInflateBase64Gzip(ival);
        setImpacto(ival ?? null);
      }
    })();
    return () => ac.abort();
  }, [id, useExplicit]);

  // -------- Carga de comentarios --------
  const loadComments = useCallback(async (page = 1) => {
    if (!id || useExplicit) return;
    const ac = new AbortController();
    const data = await fetchComments(id, page, cLimit, ac.signal);
    setComments(data.items || []);
    setCPage(data.page || 1);
    setCPages(data.pages || 0);
    setCTotal(data.total || 0);
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, useExplicit, cLimit]);
  useEffect(() => {
    loadComments(1);
  }, [loadComments]);

  const handleBack = useCallback(() => {
    if (window.history.length > 1) navigate(-1);
    else navigate("/", { replace: true });
  }, [navigate]);

  const handleLike = useCallback(async () => {
    if (!id || useExplicit) return;
    try {
      const res = await likeItem(id);
      if (typeof res?.likes === "number") setLikes(res.likes);
      else setLikes((v) => (v ?? 0) + 1);
    } catch {}
  }, [id, useExplicit]);

  const handleDislike = useCallback(async () => {
    if (!id || useExplicit) return;
    try {
      const res = await dislikeItem(id);
      if (typeof res?.dislikes === "number") setDislikes(res.dislikes);
      else setDislikes((v) => (v ?? 0) + 1);
    } catch {}
  }, [id, useExplicit]);

  // ------- Comentarios: form -------
  const [commentAuthor, setCommentAuthor] = useState("");
  const [commentText, setCommentText] = useState("");
  const onSubmitComment = useCallback(async (e) => {
    e.preventDefault();
    if (!id || useExplicit) return;
    const text = commentText.trim();
    if (!text) return;
    setAddingComment(true);
    try {
      const created = await postComment(id, { text, author: commentAuthor.trim() || undefined });
      setCommentText("");
      setCommentAuthor("");
      // prepend optimista
      setComments((prev) => [created, ...prev]);
      setCTotal((t) => t + 1);
    } catch {
      // no-op
    } finally {
      setAddingComment(false);
    }
  }, [id, useExplicit, commentText, commentAuthor]);

  if (loading) {
    return (
      <main role="main" className="mx-auto max-w-5xl p-4 md:p-6">
        <div className="animate-pulse space-y-4" aria-busy>
          <div className="h-5 w-24 rounded bg-gray-200" />
          <div className="h-8 w-3/4 rounded bg-gray-200" />
          <div className="h-6 w-1/2 rounded bg-gray-200" />
          <div className="h-72 w-full rounded bg-gray-200" />
        </div>
      </main>
    );
  }
  if (error) {
    return (
      <main role="main" className="mx-auto max-w-4xl p-4 md:p-6">
        <button onClick={handleBack} className="text-sm text-blue-600 hover:underline">← Volver</button>
        <div className="mt-6 rounded-2xl border border-red-200 bg-red-50 p-4 text-red-800">
          <h1 className="text-lg font-semibold">No se pudo cargar el documento</h1>
          <p className="mt-1 text-sm">{String(error.message || error)}</p>
          <div className="mt-3 text-xs opacity-75">ID: {id}</div>
        </div>
      </main>
    );
  }
  if (!inflated) return null;

  const {
    title,
    section,
    departamento,
    epigrafe,
    control,
    created_at,
    url_pdf,
    sourceUrl,
    metadata,
    html,
    content,
    full_title,
    titulo_completo,
    identificador,
  } = inflated;

  const displayDate = created_at
    ? new Date(created_at).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "2-digit" })
    : null;

  const completeTitle = (full_title || titulo_completo || title || "").trim();
  const showCompleteTitleBlock = completeTitle && completeTitle !== (title || "").trim();

  // Chips estilo mockup
  const chips = [
    section ? { k: "Sección", v: section } : null,
    departamento ? { k: "Departamento", v: departamento } : null,
    epigrafe ? { k: "Epígrafe", v: epigrafe } : null,
    identificador ? { k: "Identificador", v: identificador } : null,
    control ? { k: "Control", v: control } : null,
    displayDate ? { k: "Fecha publicación", v: displayDate } : null,
  ].filter(Boolean);

  // Impacto: si llega JSON string -> parse
  let impactoNode = null;
  if (impacto) {
    let parsed = impacto;
    if (typeof parsed === "string") {
      try { parsed = JSON.parse(parsed); } catch {}
    }
    if (parsed && typeof parsed === "object") {
      const entries = Object.entries(parsed);
      impactoNode = (
        <div className="space-y-3">
          {entries.map(([k, v]) => (
            <div key={k} className="rounded-xl border p-4">
              <h3 className="text-sm font-medium text-gray-700">{String(k)}</h3>
              {Array.isArray(v) ? (
                <ul className="mt-2 list-disc pl-5 text-sm text-gray-900">
                  {v.map((x, i) => <li key={i} className="break-words">{String(x)}</li>)}
                </ul>
              ) : (
                <p className="mt-2 text-sm text-gray-900 whitespace-pre-wrap break-words">{String(v)}</p>
              )}
            </div>
          ))}
        </div>
      );
    } else {
      impactoNode = (
        <p className="whitespace-pre-wrap text-gray-800">{String(impacto)}</p>
      );
    }
  }

  return (
    <main role="main" className="mx-auto max-w-5xl p-4 md:p-6">
      {/* Top bar: volver + votos + PDF rojo */}
      <div className="mb-3 flex items-center justify-between">
        <button
          onClick={handleBack}
          className="inline-flex items-center gap-1 rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50"
        >
          <span aria-hidden>←</span> Volver atrás
        </button>
        <div className="flex items-center gap-2">
          <button onClick={handleLike} className="rounded-xl border px-3 py-1.5 text-sm">
            👍 <span className="tabular-nums">{likes ?? 0}</span>
          </button>
          <button onClick={handleDislike} className="rounded-xl border px-3 py-1.5 text-sm">
            👎 <span className="tabular-nums">{dislikes ?? 0}</span>
          </button>
          {url_pdf && (
            <a
              href={url_pdf}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-xl bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700"
            >
              Ver PDF
            </a>
          )}
        </div>
      </div>

      {/* Cabecera + chips */}
      <div className="rounded-2xl border bg-white p-5 shadow-sm">
        <h1 className="text-2xl font-semibold leading-snug text-gray-900">
          {title || "Documento BOE"}
        </h1>
        <div className="mt-4 flex flex-wrap gap-2">
          {chips.map(({ k, v }, i) => (
            <span key={i} className="inline-flex items-center gap-2 rounded-full border bg-gray-50 px-3 py-1 text-xs">
              <span className="text-gray-500">{k}:</span>
              <span className="font-medium text-gray-900">{String(v)}</span>
            </span>
          ))}
        </div>

        {showCompleteTitleBlock && (
          <div className="mt-4 rounded-xl bg-gray-50 p-4">
            <h2 className="mb-1 text-sm font-medium text-gray-700">Título completo</h2>
            <p className="text-gray-900 whitespace-pre-wrap">{completeTitle}</p>
          </div>
        )}
      </div>

      {/* Resumen */}
      {(summary || inflated.summary) && (
        <section className="mt-5 rounded-2xl border bg-white p-5 shadow-sm">
          <h2 className="text-base font-semibold text-gray-900">Resumen</h2>
          <p className="mt-2 whitespace-pre-wrap text-gray-800">{summary ?? inflated.summary}</p>
        </section>
      )}

      {/* Contenido */}
      <section className="mt-5 rounded-2xl border bg-white p-5 shadow-sm">
        <h2 className="text-base font-semibold text-gray-900">Contenido</h2>
        <article className="prose mt-3 max-w-none">
          {html
            ? <div dangerouslySetInnerHTML={{ __html: html }} />
            : looksLikeHTML(content)
              ? <div dangerouslySetInnerHTML={{ __html: content }} />
              : <pre className="whitespace-pre-wrap break-words text-[0.98rem] leading-relaxed text-gray-900">{content}</pre>
          }
        </article>
      </section>

      {/* Impacto (si existe) */}
      {impactoNode && (
        <section className="mt-5 rounded-2xl border bg-white p-5 shadow-sm">
          <h2 className="text-base font-semibold text-gray-900">Informe de Impacto</h2>
          <div className="mt-3">{impactoNode}</div>
        </section>
      )}

      {/* Metadatos (fallbacks) */}
      {metadata && Object.keys(metadata).filter(k => metadata[k] != null && metadata[k] !== "").length > 0 && (
        <section className="mt-5 rounded-2xl border bg-white p-5 shadow-sm">
          <h2 className="text-base font-semibold text-gray-900">Metadatos</h2>
          <dl className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {Object.entries(metadata).map(([k, v]) => (
              <div key={k} className="rounded-xl border p-3 text-sm">
                <dt className="text-gray-500">{k}</dt>
                <dd className="mt-1 break-words text-gray-900">
                  {Array.isArray(v) ? v.join(", ") : String(v)}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      {/* Comentarios */}
      {!useExplicit && (
        <section className="mt-6 rounded-2xl border bg-white p-5 shadow-sm">
          <h2 className="text-base font-semibold text-gray-900">Comentarios ({cTotal})</h2>

          {/* Formulario */}
          <form onSubmit={onSubmitComment} className="mt-3 grid gap-2 md:grid-cols-3">
            <input
              type="text"
              placeholder="Autor (opcional)"
              value={commentAuthor}
              onChange={(e) => setCommentAuthor(e.target.value)}
              className="rounded-xl border px-3 py-2 text-sm md:col-span-1"
            />
            <input
              type="text"
              placeholder="Escribe un comentario…"
              value={commentText}
              onChange={(e) => setCommentText(e.target.value)}
              className="rounded-xl border px-3 py-2 text-sm md:col-span-2"
              required
            />
            <div className="md:col-span-3">
              <button
                type="submit"
                disabled={addingComment}
                className="rounded-xl bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {addingComment ? "Enviando…" : "Publicar"}
              </button>
            </div>
          </form>

          {/* Lista */}
          <div className="mt-4 space-y-3">
            {comments.length === 0 ? (
              <p className="text-sm text-gray-600">Aún no hay comentarios.</p>
            ) : (
              comments.map((c) => (
                <div key={c.id} className="rounded-xl border p-3">
                  <div className="text-xs text-gray-500">
                    {c.author || "Anónimo"} · {c.created_at ? new Date(c.created_at).toLocaleString() : ""}
                  </div>
                  <p className="mt-1 text-sm text-gray-900 whitespace-pre-wrap">{c.text || c.content}</p>
                </div>
              ))
            )}
          </div>

          {/* Paginación */}
          {cPages > 1 && (
            <div className="mt-4 flex items-center gap-2">
              <button
                onClick={() => loadComments(Math.max(1, cPage - 1))}
                disabled={cPage <= 1}
                className="rounded-xl border px-3 py-1.5 text-sm disabled:opacity-50"
              >
                ← Anteriores
              </button>
              <div className="text-xs text-gray-600">Página {cPage} / {cPages}</div>
              <button
                onClick={() => loadComments(Math.min(cPages, cPage + 1))}
                disabled={cPage >= cPages}
                className="rounded-xl border px-3 py-1.5 text-sm disabled:opacity-50"
              >
                Siguientes →
              </button>
            </div>
          )}
        </section>
      )}

      {/* Acciones finales */}
      <div className="mt-6 flex flex-wrap items-center gap-2">
        <CopyButton text={html || content || ""} />
        {url_pdf && (
          <a href={url_pdf} target="_blank" rel="noreferrer" className="rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50">
            Ver PDF
          </a>
        )}
        {sourceUrl && (
          <a href={sourceUrl} target="_blank" rel="noreferrer" className="rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50">
            Abrir en BOE
          </a>
        )}
      </div>
    </main>
  );
}

// ===============
// UI helpers
// ===============
function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1000);
    } catch {}
  }, [text]);
  return (
    <button
      onClick={onCopy}
      aria-live="polite"
      className="rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50"
    >
      {copied ? "Copiado ✓" : "Copiar"}
    </button>
  );
}
