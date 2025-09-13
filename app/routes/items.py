// src/pages/BOEDetailPage.jsx
import React, { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Disclosure } from "@headlessui/react";
import {
  ArrowLeftIcon,
  ChevronDownIcon,
  DocumentTextIcon,
  HandThumbUpIcon,
  HandThumbDownIcon,
  CalendarDaysIcon,
  LightBulbIcon,
  BookOpenIcon,
  UserGroupIcon,
  ArrowPathIcon,
  ExclamationTriangleIcon,
  SparklesIcon,
} from "@heroicons/react/24/outline";
import api from "../services/http";

/* ============================================================
   Utilidades: base64 + gzip (pako on-demand)
   ============================================================ */
let pakoRef = null;
async function getPako() {
  if (!pakoRef) pakoRef = (await import("pako")).default;
  return pakoRef;
}
function isProbablyBase64(s) {
  return (
    typeof s === "string" &&
    s.length >= 8 &&
    s.length % 4 === 0 &&
    /^[A-Za-z0-9+/]+={0,2}$/.test(s)
  );
}
function peekBase64Bytes(s, n = 2) {
  try {
    if (typeof window !== "undefined" && typeof atob === "function") {
      const c = atob(s.slice(0, 4 * Math.ceil(n / 3)));
      const o = new Uint8Array(c.length);
      for (let i = 0; i < c.length; i++) o[i] = c.charCodeAt(i);
      return o.slice(0, n);
    } else if (typeof Buffer !== "undefined") {
      return Buffer.from(s, "base64").subarray(0, n);
    }
  } catch {}
  return new Uint8Array(0);
}
function isProbablyBase64Gzip(s) {
  if (!isProbablyBase64(s)) return false;
  const h = peekBase64Bytes(s, 2);
  return h.length >= 2 && h[0] === 0x1f && h[1] === 0x8b;
}
function decodeBase64ToUint8(s) {
  if (typeof window !== "undefined" && typeof atob === "function") {
    const b = atob(s);
    const o = new Uint8Array(b.length);
    for (let i = 0; i < b.length; i++) o[i] = b.charCodeAt(i);
    return o;
  }
  return new Uint8Array(Buffer.from(s, "base64"));
}
const maybeInflateBase64Gzip = async (s) => {
  try {
    if (!s || typeof s !== "string") return s;
    if (!isProbablyBase64Gzip(s)) return s;
    const bytes = decodeBase64ToUint8(s);
    const p = await getPako();
    return p.ungzip(bytes, { to: "string" }) || s;
  } catch {
    return s;
  }
};
const looksLikeHTML = (s) => typeof s === "string" && /<\/?[a-z][\s\S]*>/i.test(s);
const cx = (...c) => c.filter(Boolean).join(" ");

/* ============================================================
   API helpers (axios.baseURL ya debe ser "/api")
   ============================================================ */
async function fetchDetail(id, signal) {
  const { data } = await api.get(`items/${encodeURIComponent(id)}`, { signal });
  return data;
}
async function fetchImpacto(id, signal) {
  try {
    const { data } = await api.get(`items/${encodeURIComponent(id)}/impacto`, {
      signal,
    });
    return data?.impacto ?? null;
  } catch {
    return null;
  }
}
async function fetchComments(id, page = 1, limit = 10, signal) {
  try {
    const { data } = await api.get(`items/${encodeURIComponent(id)}/comments`, {
      params: { page, limit },
      signal,
    });
    return data || { items: [], page: 1, pages: 0, total: 0, limit };
  } catch {
    return { items: [], page: 1, pages: 0, total: 0, limit };
  }
}
async function postComment(id, payload) {
  const { data } = await api.post(
    `items/${encodeURIComponent(id)}/comments`,
    payload
  );
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

/* ============================================================
   Resumen helpers: estructura Contexto/Fechas/Cambios/Conclusión
   ============================================================ */
function parseSummary(rawSummary, rawMetaResumen) {
  // 1) Si viene estructurado en metadata.resumen
  if (rawMetaResumen && typeof rawMetaResumen === "object") {
    return {
      contexto: rawMetaResumen.context ?? rawMetaResumen.contexto ?? null,
      cambios: rawMetaResumen.key_changes ?? rawMetaResumen.cambios ?? null,
      fechas: rawMetaResumen.key_dates_events ?? rawMetaResumen.fechas ?? null,
      conclusion: rawMetaResumen.conclusion ?? null,
    };
  }
  // 2) Si summary es JSON válido
  if (typeof rawSummary === "string" && rawSummary.trim().startsWith("{")) {
    try {
      const obj = JSON.parse(rawSummary);
      return {
        contexto: obj.context ?? obj.contexto ?? null,
        cambios: obj.key_changes ?? obj.cambios ?? null,
        fechas: obj.key_dates_events ?? obj.fechas ?? null,
        conclusion: obj.conclusion ?? null,
      };
    } catch {
      /* fallback */
    }
  }
  // 3) Fallback: todo a Contexto
  return {
    contexto:
      typeof rawSummary === "string" && rawSummary.trim()
        ? rawSummary.trim()
        : null,
    cambios: null,
    fechas: null,
    conclusion: null,
  };
}

/* ============================================================
   Página
   ============================================================ */
export default function BOEDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [doc, setDoc] = useState(null);
  const [summaryParts, setSummaryParts] = useState({
    contexto: null,
    cambios: null,
    fechas: null,
    conclusion: null,
  });
  const [impacto, setImpacto] = useState(null);

  const [likes, setLikes] = useState(0);
  const [dislikes, setDislikes] = useState(0);

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

  // Carga de detalle
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
          const { data } = await api.get(explicitEndpoint, {
            signal: ac.signal,
            baseURL: "",
          });
          raw = data;
        } else {
          raw = await fetchDetail(id, ac.signal);
        }

        // Normaliza y garantiza nombres (no códigos)
        const titulo_resumen = await maybeInflateBase64Gzip(
          raw.titulo_resumen ?? ""
        );
        const titulo = await maybeInflateBase64Gzip(raw.titulo ?? raw.title ?? "");
        const titulo_completo = await maybeInflateBase64Gzip(
          raw.titulo_completo ?? ""
        );

        const norm = {
          identificador: raw.identificador ?? id,
          titulo_resumen,
          titulo,
          titulo_completo,
          summary: await maybeInflateBase64Gzip(raw.resumen ?? raw.summary ?? ""),
          // Contenido eliminado del render, pero dejamos parseo por compatibilidad si se necesitara más adelante
          content: await maybeInflateBase64Gzip(raw.contenido ?? raw.content ?? ""),
          html: await maybeInflateBase64Gzip(raw.html ?? ""),
          // Mostrar solo nombres (evitar *_codigo)
          section_name:
            raw.seccion_nombre ??
            raw.seccion ??
            null,
          departamento_name:
            raw.departamento_nombre ??
            raw.departamento ??
            null,
          epigrafe_name: await maybeInflateBase64Gzip(
            raw.epigrafe_nombre ?? raw.epigrafe ?? ""
          ),
          control: raw.control ?? null,
          created_at: raw.created_at ?? raw.fecha ?? null,
          // PDF + fuente
          url_pdf: raw.url_pdf ?? raw.pdf_url ?? raw.pdf ?? raw.urlPdf ?? null,
          sourceUrl: raw.sourceUrl ?? raw.url_boe ?? null,
          likes: Number.isFinite(raw.likes) ? raw.likes : 0,
          dislikes: Number.isFinite(raw.dislikes) ? raw.dislikes : 0,
          metaResumen: raw?.metadata?.resumen || null,
        };

        setDoc(norm);
        setLikes(norm.likes);
        setDislikes(norm.dislikes);
        setSummaryParts(parseSummary(norm.summary, norm.metaResumen));
      } catch (err) {
        if (err?.name !== "AbortError") setError(err);
      } finally {
        setLoading(false);
      }
    })();
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, explicitEndpoint, useExplicit]);

  // Impacto
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

  // Comentarios
  const loadComments = useCallback(
    async (page = 1) => {
      if (!id || useExplicit) return;
      const data = await fetchComments(id, page, cLimit);
      setComments(data.items || []);
      setCPage(data.page || 1);
      setCPages(data.pages || 0);
      setCTotal(data.total || 0);
    },
    [id, useExplicit, cLimit]
  );
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
      const r = await likeItem(id);
      setLikes(typeof r?.likes === "number" ? r.likes : (v) => (v ?? 0) + 1);
    } catch {}
  }, [id, useExplicit]);

  const handleDislike = useCallback(async () => {
    if (!id || useExplicit) return;
    try {
      const r = await dislikeItem(id);
      setDislikes(
        typeof r?.dislikes === "number" ? r.dislikes : (v) => (v ?? 0) + 1
      );
    } catch {}
  }, [id, useExplicit]);

  // Post comentario
  const [commentAuthor, setCommentAuthor] = useState("");
  const [commentText, setCommentText] = useState("");
  const onSubmitComment = useCallback(
    async (e) => {
      e.preventDefault();
      if (!id || useExplicit) return;
      const text = commentText.trim();
      if (!text) return;
      setAddingComment(true);
      try {
        const created = await postComment(id, {
          text,
          author: commentAuthor.trim() || undefined,
        });
        setCommentText("");
        setCommentAuthor("");
        setComments((p) => [created, ...p]);
        setCTotal((t) => t + 1);
      } catch {
      } finally {
        setAddingComment(false);
      }
    },
    [id, useExplicit, commentText, commentAuthor]
  );

  if (loading) {
    return (
      <main className="mx-auto max-w-5xl p-4 md:p-6">
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
      <main className="mx-auto max-w-4xl p-4 md:p-6">
        <button
          onClick={handleBack}
          className="inline-flex items-center gap-2 text-sm text-blue-600 hover:underline"
        >
          <ArrowLeftIcon className="h-4 w-4" /> Volver
        </button>
        <div className="mt-6 rounded-2xl border border-red-200 bg-red-50 p-4 text-red-800">
          <h1 className="text-lg font-semibold">No se pudo cargar el documento</h1>
          <p className="mt-1 text-sm">{String(error.message || error)}</p>
          <div className="mt-3 text-xs opacity-75">ID: {id}</div>
        </div>
      </main>
    );
  }
  if (!doc) return null;

  const {
    titulo_resumen,
    titulo,
    titulo_completo,
    section_name,
    departamento_name,
    epigrafe_name,
    identificador,
    control,
    created_at,
    url_pdf,
    sourceUrl,
  } = doc;

  const displayDate = created_at
    ? new Date(created_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "2-digit",
      })
    : null;
  const longTitle = [titulo_completo, titulo].filter(Boolean).join("").trim();

  /* --------- Render Impacto en tarjetas con iconos + acordeón --------- */
  const impactMapOrder = [
    {
      key: "afectados",
      label: "Afectados",
      Icon: UserGroupIcon,
      dot: "bg-emerald-500",
    },
    {
      key: "cambios_operativos",
      label: "Cambios operativos",
      Icon: ArrowPathIcon,
      dot: "bg-sky-500",
    },
    {
      key: "riesgos_potenciales",
      label: "Riesgos potenciales",
      Icon: ExclamationTriangleIcon,
      dot: "bg-amber-500",
    },
    {
      key: "beneficios_previstos",
      label: "Beneficios previstos",
      Icon: SparklesIcon,
      dot: "bg-violet-500",
    },
    {
      key: "recomendaciones",
      label: "Recomendaciones",
      Icon: LightBulbIcon,
      dot: "bg-rose-500",
    },
  ];

  let impactoContent = null;
  if (impacto) {
    let parsed = impacto;
    if (typeof parsed === "string") {
      try {
        parsed = JSON.parse(parsed);
      } catch {}
    }
    if (parsed && typeof parsed === "object") {
      const sections = impactMapOrder.filter((s) => parsed[s.key]);
      impactoContent = (
        <div className="space-y-3">
          {sections.map(({ key, label, Icon, dot }) => {
            const val = parsed[key];
            return (
              <div key={key} className="rounded-xl border p-4">
                <div className="flex items-center gap-2">
                  <span className={cx("h-2.5 w-2.5 rounded-full", dot)} />
                  <Icon className="h-4 w-4 text-gray-600" />
                  <h3 className="text-sm font-medium text-gray-700">{label}</h3>
                </div>
                {Array.isArray(val) ? (
                  <ul className="mt-2 list-disc pl-5 text-sm text-gray-900">
                    {val.map((x, i) => (
                      <li key={i} className="break-words">
                        {String(x)}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-2 whitespace-pre-wrap break-words text-sm text-gray-900">
                    {String(val)}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      );
    } else {
      impactoContent = (
        <p className="whitespace-pre-wrap text-gray-800">{String(impacto)}</p>
      );
    }
  }

  return (
    <main role="main" className="mx-auto max-w-5xl p-4 md:p-6">
      {/* Top bar */}
      <div className="mb-3 flex items-center justify-between">
        <button
          onClick={handleBack}
          className="inline-flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50"
        >
          <ArrowLeftIcon className="h-4 w-4" /> Volver atrás
        </button>
        {/* Se elimina el PDF duplicado en top bar */}
      </div>

      {/* Cabecera */}
      <div className="rounded-2xl border bg-white p-5 shadow-sm">
        <h1 className="text-2xl font-extrabold leading-snug text-gray-900">
          {titulo_resumen || titulo || "Documento BOE"}
        </h1>
        {longTitle && (
          <p className="mt-2 whitespace-pre-wrap text-gray-800">{longTitle}</p>
        )}

        {/* Metadatos visuales (solo nombres) */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {section_name && (
            <span className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs font-medium text-gray-700">
              <span className="h-1.5 w-1.5 rounded-full bg-blue-500" />
              Sección: {section_name}
            </span>
          )}
          {epigrafe_name && (
            <span className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs font-medium text-gray-700">
              <span className="h-1.5 w-1.5 rounded-full bg-indigo-500" />
              Epígrafe: {epigrafe_name}
            </span>
          )}
          {departamento_name && (
            <span className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs font-medium text-gray-700">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
              Departamento: {departamento_name}
            </span>
          )}
        </div>

        {/* Detalles secundarios */}
        <dl className="mt-4 grid gap-y-2 text-sm md:grid-cols-3">
          {identificador && (
            <div>
              <dt className="text-gray-500">Identificador</dt>
              <dd className="break-words text-gray-900">{identificador}</dd>
            </div>
          )}
          {control && (
            <div>
              <dt className="text-gray-500">Control</dt>
              <dd className="break-words text-gray-900">{control}</dd>
            </div>
          )}
          {created_at && (
            <div>
              <dt className="text-gray-500">Fecha publicación</dt>
              <dd className="text-gray-900">{displayDate}</dd>
            </div>
          )}
        </dl>
      </div>

      {/* Likes / Dislikes + Acciones principales */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          onClick={handleLike}
          className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-sm text-emerald-800 hover:bg-emerald-100"
        >
          <HandThumbUpIcon className="h-4 w-4" /> {likes}
        </button>
        <button
          onClick={handleDislike}
          className="inline-flex items-center gap-2 rounded-full border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm text-rose-800 hover:bg-rose-100"
        >
          <HandThumbDownIcon className="h-4 w-4" /> {dislikes}
        </button>

        {url_pdf && (
          <a
            href={url_pdf}
            target="_blank"
            rel="noreferrer"
            className="ml-1 inline-flex items-center gap-2 rounded-xl bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700"
          >
            <DocumentTextIcon className="h-4 w-4" /> Ver PDF
          </a>
        )}

        {sourceUrl && (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50"
          >
            Abrir en BOE
          </a>
        )}
      </div>

      {/* Resumen (acordeón) */}
      {(summaryParts.contexto ||
        summaryParts.fechas ||
        summaryParts.cambios ||
        summaryParts.conclusion) && (
        <section className="mt-5">
          <Disclosure defaultOpen>
            {({ open }) => (
              <div className="overflow-hidden rounded-2xl border bg-white shadow-sm">
                <Disclosure.Button className="flex w-full items-center justify-between px-5 py-3 text-left">
                  <div className="flex items-center gap-2">
                    <DocumentTextIcon className="h-5 w-5 text-gray-600" />
                    <span className="text-base font-semibold text-gray-900">
                      Resumen
                    </span>
                  </div>
                  <ChevronDownIcon
                    className={cx(
                      "h-5 w-5 text-gray-500 transition-transform",
                      open && "rotate-180"
                    )}
                  />
                </Disclosure.Button>
                <Disclosure.Panel className="border-t px-5 py-4">
                  {summaryParts.contexto && (
                    <div className="mt-1">
                      <div className="flex items-center gap-2">
                        <BookOpenIcon className="h-4 w-4 text-gray-600" />
                        <h3 className="text-sm font-medium text-gray-700">
                          Contexto
                        </h3>
                      </div>
                      <p className="mt-1 whitespace-pre-wrap text-gray-800">
                        {summaryParts.contexto}
                      </p>
                    </div>
                  )}

                  {summaryParts.fechas && (
                    <div className="mt-4">
                      <div className="flex items-center gap-2">
                        <CalendarDaysIcon className="h-4 w-4 text-gray-600" />
                        <h3 className="text-sm font-medium text-gray-700">
                          Fechas clave
                        </h3>
                      </div>
                      {Array.isArray(summaryParts.fechas) ? (
                        <ul className="mt-1 list-disc pl-5 text-gray-800">
                          {summaryParts.fechas.map((f, i) => (
                            <li key={i}>{String(f)}</li>
                          ))}
                        </ul>
                      ) : (
                        <ul className="mt-1 list-disc pl-5 text-gray-800">
                          {String(summaryParts.fechas)
                            .split(/\r?\n|\u2022|-/)
                            .map((line, i) => {
                              const t = line.trim();
                              return t ? <li key={i}>{t}</li> : null;
                            })}
                        </ul>
                      )}
                    </div>
                  )}

                  {summaryParts.cambios && (
                    <div className="mt-4">
                      <div className="flex items-center gap-2">
                        <ArrowPathIcon className="h-4 w-4 text-gray-600" />
                        <h3 className="text-sm font-medium text-gray-700">
                          Cambios clave
                        </h3>
                      </div>
                      {Array.isArray(summaryParts.cambios) ? (
                        <ul className="mt-1 list-disc pl-5 text-gray-800">
                          {summaryParts.cambios.map((c, i) => (
                            <li key={i}>{String(c)}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-1 whitespace-pre-wrap text-gray-800">
                          {String(summaryParts.cambios)}
                        </p>
                      )}
                    </div>
                  )}

                  {summaryParts.conclusion && (
                    <div className="mt-4">
                      <div className="flex items-center gap-2">
                        <LightBulbIcon className="h-4 w-4 text-gray-600" />
                        <h3 className="text-sm font-medium text-gray-700">
                          Conclusión
                        </h3>
                      </div>
                      <p className="mt-1 whitespace-pre-wrap text-gray-800">
                        {summaryParts.conclusion}
                      </p>
                    </div>
                  )}
                </Disclosure.Panel>
              </div>
            )}
          </Disclosure>
        </section>
      )}

      {/* Informe de Impacto (acordeón) */}
      {impactoContent && (
        <section className="mt-5">
          <Disclosure>
            {({ open }) => (
              <div className="overflow-hidden rounded-2xl border bg-white shadow-sm">
                <Disclosure.Button className="flex w-full items-center justify-between px-5 py-3 text-left">
                  <div className="flex items-center gap-2">
                    <SparklesIcon className="h-5 w-5 text-gray-600" />
                    <span className="text-base font-semibold text-gray-900">
                      Informe de Impacto
                    </span>
                  </div>
                  <ChevronDownIcon
                    className={cx(
                      "h-5 w-5 text-gray-500 transition-transform",
                      open && "rotate-180"
                    )}
                  />
                </Disclosure.Button>
                <Disclosure.Panel className="border-t px-5 py-4">
                  {impactoContent}
                </Disclosure.Panel>
              </div>
            )}
          </Disclosure>
        </section>
      )}

      {/* Comentarios */}
      {!useExplicit && (
        <section className="mt-6 rounded-2xl border bg-white p-5 shadow-sm">
          <h2 className="text-base font-semibold text-gray-900">
            Comentarios ({cTotal})
          </h2>
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
                    {c.author || "Anónimo"} ·{" "}
                    {c.created_at ? new Date(c.created_at).toLocaleString() : ""}
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-sm text-gray-900">
                    {c.text || c.content}
                  </p>
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
              <div className="text-xs text-gray-600">
                Página {cPage} / {cPages}
              </div>
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
    </main>
  );
}
