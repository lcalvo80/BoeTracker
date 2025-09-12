// src/pages/BOEDetailPage.jsx
import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import api from "../services/http";

/**
 * BOEDetailPage.jsx — UI: H1= titulo_resumen, subtítulo con título largo,
 * metadatos verticales, botón PDF, resumen dividido (contexto/fechas/conclusión),
 * resto igual (contenido, impacto, metadatos, comentarios).
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

const looksLikeHTML = (s) => typeof s === "string" && /<\/?[a-z][\s\S]*>/i.test(s);

// =====================
// API helpers (baseURL ya incluye /api)
// =====================
async function fetchDetail(id, signal) {
  const { data } = await api.get(`items/${encodeURIComponent(id)}`, { signal });
  return data;
}
async function fetchResumen(id, signal) {
  try {
    const { data } = await api.get(`items/${encodeURIComponent(id)}/resumen`, { signal });
    return data?.resumen ?? null;
  } catch { return null; }
}
async function fetchImpacto(id, signal) {
  try {
    const { data } = await api.get(`items/${encodeURIComponent(id)}/impacto`, { signal });
    return data?.impacto ?? null;
  } catch { return null; }
}
async function fetchComments(id, page = 1, limit = 10, signal) {
  try {
    const { data } = await api.get(`items/${encodeURIComponent(id)}/comments`, { params: { page, limit }, signal });
    return data || { items: [], page: 1, pages: 0, total: 0, limit };
  } catch {
    return { items: [], page: 1, pages: 0, total: 0, limit };
  }
}
async function postComment(id, payload) {
  const { data } = await api.post(`items/${encodeURIComponent(id)}/comments`, payload);
  return data;
}
async function likeItem(id) {
  const { data } = await api.post(`items/${encodeURIComponent(id)}/like`);
  return data;
}
async function dislikeItem(id) {
  const { data } = await api.post(`items/${encodeURIComponent(id)}/dislike`);
  return data;
}

// ===== Resumen helpers =====
function deriveSummaryParts(rawStruct, rawSummary) {
  // Prioriza objeto estructurado metadata.resumen { contexto, fechas, conclusion }
  const base = {
    contexto: null,
    fechas: null,     // array o string
    conclusion: null,
  };
  if (rawStruct && typeof rawStruct === "object") {
    return {
      contexto: rawStruct.contexto ?? null,
      fechas: rawStruct.fechas ?? null,
      conclusion: rawStruct.conclusion ?? null,
    };
  }
  if (typeof rawSummary === "string" && rawSummary.trim()) {
    return { ...base, contexto: rawSummary.trim() };
  }
  return base;
}

// =====================
// Vista principal
// =====================
export default function BOEDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [inflated, setInflated] = useState(null);
  const [summaryParts, setSummaryParts] = useState({ contexto: null, fechas: null, conclusion: null });
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

        // Normalización de nombres
        const norm = {
          identificador: raw.identificador ?? id,
          // Títulos
          titulo_resumen: raw.titulo_resumen ?? null,
          titulo: raw.titulo ?? raw.title ?? "",
          titulo_completo: raw.titulo_completo ?? null,
          // Texto/HTML
          summary: raw.resumen ?? raw.summary ?? null,
          content: raw.contenido ?? raw.content ?? null,
          html: raw.html ?? null,
          // Metas
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
        };

        // Infla campos que puedan venir base64+gzip
        const toInflateKeys = ["summary", "content", "html", "epigrafe", "titulo", "titulo_completo", "titulo_resumen"];
        for (const k of toInflateKeys) {
          if (typeof norm[k] === "string") {
            norm[k] = await maybeInflateBase64Gzip(norm[k]);
          }
        }

        setInflated(norm);
        setLikes(norm.likes);
        setDislikes(norm.dislikes);

        // Construye resumen dividido
        const structFromMeta = raw?.metadata?.resumen;
        const parts = deriveSummaryParts(structFromMeta, norm.summary);
        setSummaryParts(parts);
      } catch (err) {
        if (err?.name !== "AbortError") setError(err);
      } finally {
        setLoading(false);
      }
    })();

    return () => ac.abort();
  }, [id, explicitEndpoint, useExplicit]);

  // -------- Carga de impacto (lazy) --------
  useEffect(() => {
    if (!id || useExplicit) return;
    const ac = new AbortController();
    (async () => {
      try {
        let ival = await fetchImpacto(id, ac.signal);
        if (typeof ival === "string") ival = await maybeInflateBase64Gzip(ival);
        setImpacto(ival ?? null);
      } catch {}
    })();
    return () => ac.abort();
  }, [id, useExplicit]);

  // -------- Carga de comentarios --------
  const loadComments = useCallback(async (page = 1) => {
    if (!id || useExplicit) return;
    try {
      const data = await fetchComments(id, page, cLimit);
      setComments(data.items || []);
      setCPage(data.page || 1);
      setCPages(data.pages || 0);
      setCTotal(data.total || 0);
    } catch {
      setComments([]);
      setCPage(1);
      setCPages(0);
      setCTotal(0);
    }
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
      setComments((prev) => [created, ...prev]);
      setCTotal((t) => t + 1);
    } catch {} finally {
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
    titulo_resumen,
    titulo,
    titulo_completo,
    section,
    departamento,
    epigrafe,
    identificador,
    control,
    created_at,
    url_pdf,
    sourceUrl,
    metadata,
    html,
    content,
  } = inflated;

  const displayDate = created_at
    ? new Date(created_at).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "2-digit" })
    : null;

  const longTitle = (titulo_completo || titulo || "").trim();

  // Impacto: si llega JSON string -> parse
  let impactoNode = null;
  if (impacto) {
    let parsed = impacto;
    if (typeof parsed === "string") { try { parsed = JSON.parse(parsed); } catch {} }
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
      impactoNode = <p className="whitespace-pre-wrap text-gray-800">{String(impacto)}</p>;
    }
  }

  return (
    <main role="main" className="mx-auto max-w-5xl p-4 md:p-6">
      {/* Top bar: volver + votos */}
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
        </div>
      </div>

      {/* Cabecera: H1 = titulo_resumen, subtítulo = título largo */}
      <div className="rounded-2xl border bg-white p-5 shadow-sm">
        <h1 className="text-2xl font-bold leading-snug text-gray-900">
          {titulo_resumen || titulo || "Documento BOE"}
        </h1>

        {longTitle && (
          <p className="mt-2 text-gray-800 whitespace-pre-wrap">
            {longTitle}
          </p>
        )}

        {/* Metadatos en vertical en orden solicitado */}
        <dl className="mt-4 space-y-2 text-sm">
          {section && (
            <div>
              <dt className="text-gray-500">Sección</dt>
              <dd className="text-gray-900">{section}</dd>
            </div>
          )}
          {departamento && (
            <div>
              <dt className="text-gray-500">Departamento</dt>
              <dd className="text-gray-900">{departamento}</dd>
            </div>
          )}
          {epigrafe && (
            <div>
              <dt className="text-gray-500">Epígrafe</dt>
              <dd className="text-gray-900">{epigrafe}</dd>
            </div>
          )}
          {identificador && (
            <div>
              <dt className="text-gray-500">Identificador</dt>
              <dd className="text-gray-900 break-words">{identificador}</dd>
            </div>
          )}
          {control && (
            <div>
              <dt className="text-gray-500">Control</dt>
              <dd className="text-gray-900 break-words">{control}</dd>
            </div>
          )}
          {displayDate && (
            <div>
              <dt className="text-gray-500">Fecha publicación</dt>
              <dd className="text-gray-900">{displayDate}</dd>
            </div>
          )}
        </dl>

        {/* Botón PDF */}
        {url_pdf && (
          <div className="mt-4">
            <a
              href={url_pdf}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-xl bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700"
            >
              Ver PDF
            </a>
          </div>
        )}
      </div>

      {/* Resumen dividido */}
      {(summaryParts.contexto || summaryParts.fechas || summaryParts.conclusion) && (
        <section className="mt-5 rounded-2xl border bg-white p-5 shadow-sm">
          <h2 className="text-base font-semibold text-gray-900">Resumen</h2>

          {summaryParts.contexto && (
            <div className="mt-3">
              <h3 className="text-sm font-medium text-gray-700">Contexto</h3>
              <p className="mt-1 whitespace-pre-wrap text-gray-800">{summaryParts.contexto}</p>
            </div>
          )}

          {summaryParts.fechas && (
            <div className="mt-3">
              <h3 className="text-sm font-medium text-gray-700">Fechas clave</h3>
              {Array.isArray(summaryParts.fechas) ? (
                <ul className="mt-1 list-disc pl-5 text-gray-800">
                  {summaryParts.fechas.map((f, i) => <li key={i}>{String(f)}</li>)}
                </ul>
              ) : (
                <ul className="mt-1 list-disc pl-5 text-gray-800">
                  {String(summaryParts.fechas).split(/\r?\n|\u2022|-/).map((line, i) => {
                    const t = line.trim();
                    return t ? <li key={i}>{t}</li> : null;
                  })}
                </ul>
              )}
            </div>
          )}

          {summaryParts.conclusion && (
            <div className="mt-3">
              <h3 className="text-sm font-medium text-gray-700">Conclusión</h3>
              <p className="mt-1 whitespace-pre-wrap text-gray-800">{summaryParts.conclusion}</p>
            </div>
          )}
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
