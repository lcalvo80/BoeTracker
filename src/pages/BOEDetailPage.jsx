import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

/**
 * BOEDetailPage.jsx — "pako on-demand" version (actualizado)
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
    if (!isProbablyBase64Gzip(s)) return s;
    const bytes = decodeBase64ToUint8(s);
    const pako = await getPako();
    return pako.ungzip(bytes, { to: "string" }) || s;
  } catch {
    return s;
  }
};

// =====================
// Vista principal
// =====================
export default function BOEDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [data, setData] = useState(null);
  const [inflated, setInflated] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const controllerRef = useRef(null);

  const apiUrl = useMemo(() => {
    const ep = searchParams.get("endpoint");
    return ep || `/api/boe/${encodeURIComponent(id || "")}`;
  }, [id, searchParams]);

  useEffect(() => {
    if (!id) return;
    controllerRef.current?.abort?.();
    const ac = new AbortController();
    controllerRef.current = ac;

    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(apiUrl, { signal: ac.signal, headers: { Accept: "application/json" } });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        setData(json);
      } catch (err) {
        if (err?.name !== "AbortError") {
          setError(err);
        }
      } finally {
        setLoading(false);
      }
    })();

    return () => ac.abort();
  }, [apiUrl, id]);

  // Post-proceso: inflar campos potencialmente comprimidos
  useEffect(() => {
    let cancelled = false;
    if (!data) {
      setInflated(null);
      return;
    }
    (async () => {
      const toInflate = [
        "content",
        "summary",
        "html",
        // NUEVO: campos que podrían venir comprimidos también
        "epigrafe",
        "full_title",
        "titulo_completo",
      ];
      const out = { ...data };
      for (const key of toInflate) {
        const v = data?.[key];
        if (typeof v === "string") {
          out[key] = await maybeInflateBase64Gzip(v);
        }
      }
      if (!cancelled) setInflated(out);
    })();
    return () => {
      cancelled = true;
    };
  }, [data]);

  const handleBack = useCallback(() => {
    if (window.history.length > 1) navigate(-1);
    else navigate("/", { replace: true });
  }, [navigate]);

  if (loading) {
    return (
      <main role="main" className="mx-auto max-w-4xl p-4 md:p-6">
        <button onClick={handleBack} className="text-sm text-blue-600 hover:underline">← Volver</button>
        <div className="mt-6 animate-pulse space-y-4" aria-busy>
          <div className="h-8 w-2/3 rounded bg-gray-200" />
          <div className="h-4 w-1/2 rounded bg-gray-200" />
          <div className="h-72 w-full rounded bg-gray-200" />
        </div>
      </main>
    );
  }

  if (error) {
    return (
      <main role="main" className="mx-auto max-w-3xl p-4 md:p-6">
        <button onClick={handleBack} className="text-sm text-blue-600 hover:underline">← Volver</button>
        <div className="mt-6 rounded-xl border border-red-200 bg-red-50 p-4 text-red-800">
          <h1 className="text-lg font-semibold">No se pudo cargar el documento</h1>
          <p className="mt-1 text-sm">{String(error.message || error)}</p>
          <div className="mt-3 text-xs text-red-700 opacity-75">ID: {id}</div>
        </div>
      </main>
    );
  }

  if (!inflated) return null;

  const {
    title,
    date,
    section,
    number,
    sourceUrl,
    content,
    summary,
    html,
    metadata,
    epigrafe,
    url_pdf,          // NUEVO: link PDF
    full_title,       // NUEVO: posible título extendido
    titulo_completo,  // NUEVO: alias posible
  } = inflated;

  const displayDate = date
    ? new Date(date).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "2-digit" })
    : null;

  // “Título completo” preferido si existe
  const completeTitle = (full_title || titulo_completo || title || "").trim();
  const showCompleteTitleBlock = completeTitle && completeTitle !== (title || "").trim();

  // Detección simple de “Informe de impacto”
  const isImpactReport =
    /impacto/i.test(section || "") || (metadata && /impacto/i.test(String(metadata?.tipo || "")));

  // Picks comunes para informes de impacto
  const afectados = metadata?.afectados;
  const ambito = metadata?.ambito || metadata?.ámbito;
  const materias = metadata?.materias || metadata?.sectores || metadata?.materia;

  return (
    <main role="main" className="mx-auto max-w-4xl p-4 md:p-6">
      <nav className="mb-4 flex items-center justify-between gap-2">
        <button onClick={handleBack} className="inline-flex items-center gap-1 rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50">
          <span aria-hidden>←</span>
          <span>Volver</span>
        </button>

        <div className="flex items-center gap-2">
          {url_pdf && (
            <a
              href={url_pdf}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm font-medium hover:bg-gray-50"
            >
              Ver PDF
            </a>
          )}
          {sourceUrl && (
            <a
              href={sourceUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            >
              Ver en BOE
            </a>
          )}
        </div>
      </nav>

      <header className="space-y-2">
        {section && (
          <div className="text-xs uppercase tracking-wide text-gray-500">{section}</div>
        )}
        <h1 className="text-2xl font-semibold leading-snug text-gray-900">
          {title || "Documento BOE"}
        </h1>

        {/* Epígrafe (subtítulo) */}
        {epigrafe && (
          <p className="text-sm text-gray-700">{epigrafe}</p>
        )}

        <div className="text-sm text-gray-600">
          {number && <span className="mr-2">Nº {number}</span>}
          {displayDate && <time dateTime={date}>{displayDate}</time>}
        </div>
      </header>

      {/* Título completo encima del resumen, si difiere del principal */}
      {showCompleteTitleBlock && (
        <section className="mt-6 rounded-2xl border bg-gray-50 p-4">
          <h2 className="mb-2 text-sm font-medium text-gray-700">Título completo</h2>
          <p className="whitespace-pre-wrap text-gray-900">{completeTitle}</p>
        </section>
      )}

      {/* Resumen */}
      {summary && (
        <section className="mt-6 rounded-2xl border bg-gray-50 p-4">
          <h2 className="mb-2 text-sm font-medium text-gray-700">Resumen</h2>
          <p className="whitespace-pre-wrap text-gray-800">{summary}</p>
        </section>
      )}

      {/* Cuerpo principal */}
      <article className="prose prose-gray mt-6 max-w-none">
        {html ? (
          <div dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <pre className="whitespace-pre-wrap break-words text-[0.98rem] leading-relaxed text-gray-900">{content}</pre>
        )}
      </article>

      {/* Metadatos / Informe de impacto */}
      {metadata && (
        <>
          {isImpactReport ? (
            <section className="mt-8">
              <h2 className="text-sm font-semibold text-gray-700">Informe de Impacto</h2>

              {/* Afectados */}
              {afectados && (
                <div className="mt-3 rounded-xl border p-4">
                  <h3 className="text-sm font-medium text-gray-700">Afectados</h3>
                  {Array.isArray(afectados) ? (
                    <ul className="mt-2 list-disc pl-5 text-sm text-gray-900">
                      {afectados.map((a, i) => (
                        <li key={i} className="break-words">{String(a)}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-2 text-sm text-gray-900 whitespace-pre-wrap break-words">{String(afectados)}</p>
                  )}
                </div>
              )}

              {/* Ámbito */}
              {ambito && (
                <div className="mt-3 rounded-xl border p-4">
                  <h3 className="text-sm font-medium text-gray-700">Ámbito</h3>
                  <p className="mt-2 text-sm text-gray-900 whitespace-pre-wrap break-words">{String(ambito)}</p>
                </div>
              )}

              {/* Materias / Sectores */}
              {materias && (
                <div className="mt-3 rounded-xl border p-4">
                  <h3 className="text-sm font-medium text-gray-700">Materias / Sectores</h3>
                  {Array.isArray(materias) ? (
                    <ul className="mt-2 list-disc pl-5 text-sm text-gray-900">
                      {materias.map((m, i) => (
                        <li key={i} className="break-words">{String(m)}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-2 text-sm text-gray-900 whitespace-pre-wrap break-words">{String(materias)}</p>
                  )}
                </div>
              )}
            </section>
          ) : (
            // Grid genérico solo si NO es informe de impacto
            <section className="mt-8">
              <h2 className="text-sm font-semibold text-gray-700">Metadatos</h2>
              <dl className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                {Object.entries(metadata).map(([k, v]) => (
                  <div key={k} className="rounded-xl border p-3 text-sm">
                    <dt className="text-gray-500">{k}</dt>
                    <dd className="mt-1 break-words text-gray-900">{String(v)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )}
        </>
      )}

      {/* Acciones */}
      <div className="mt-8 flex flex-wrap items-center gap-2">
        <CopyButton text={html || content || ""} />
        {url_pdf && (
          <a href={url_pdf} target="_blank" rel="noreferrer" className="rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50">
            Ver PDF
          </a>
        )}
        {sourceUrl && (
          <a href={sourceUrl} target="_blank" rel="noreferrer" className="rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50">
            Abrir fuente
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
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
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
