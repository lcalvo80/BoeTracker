// src/pages/BOEDetailPage.jsx
import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getBoeById } from "../services/boeService";
import api from "../services/http";

/**
 * BOEDetailPage.jsx — UI: chips, votos, botón PDF rojo y tarjetas
 * Mantiene fetch + pako on-demand para campos base64+gzip.
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
      for (let i = 0; i < chunk.length; i++) out[i] = chunk.charCodeAt(i); // <- fix
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
        if (/^https?:\/\//i.test(apiUrl)) {
          const { data: json } = await api.get(apiUrl, { signal: ac.signal, baseURL: "" });
          setData(json);
        } else {
          const json = await getBoeById(id, { signal: ac.signal });
          setData(json);
        }
      } catch (err) {
        if (err?.name !== "AbortError") setError(err);
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
      const toInflate = ["content", "summary", "html", "epigrafe", "full_title", "titulo_completo"];
      const out = { ...data };
      for (const key of toInflate) {
        const v = data?.[key];
        if (typeof v === "string") out[key] = await maybeInflateBase64Gzip(v);
      }
      if (!cancelled) setInflated(out);
    })();
    return () => { cancelled = true; };
  }, [data]);

  const handleBack = useCallback(() => {
    if (window.history.length > 1) navigate(-1);
    else navigate("/", { replace: true });
  }, [navigate]);

  if (loading) return <main className="mx-auto max-w-5xl p-4">Cargando…</main>;
  if (error) return <main className="mx-auto max-w-5xl p-4">Error: {String(error.message || error)}</main>;
  if (!inflated) return null;

  const {
    title,
    date,
    section,
    // number — NO desestructurar para evitar 'no-unused-vars'
    sourceUrl,
    content,
    summary,
    html,
    metadata = {},
    epigrafe,
    url_pdf,
    full_title,
    titulo_completo,
    identificador,
    control,
  } = inflated;

  // Fallbacks para el número en metadatos
  const numberVal =
    inflated?.number ??
    metadata?.numero ??
    metadata?.número ??
    metadata?.num ??
    null;

  const displayDate = date
    ? new Date(date).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "2-digit" })
    : null;

  const completeTitle = (full_title || titulo_completo || title || "").trim();

  // Chips metadatos (incluye Nº)
  const chips = [
    section ? { k: "Sección", v: section } : null,
    numberVal ? { k: "Nº", v: numberVal } : null,
    metadata?.departamento || metadata?.ministerio
      ? { k: "Departamento", v: metadata.departamento || metadata.ministerio }
      : null,
    epigrafe ? { k: "Epígrafe", v: epigrafe } : null,
    identificador ? { k: "Identificador", v: identificador } : null,
    control ? { k: "Control", v: control } : null,
    displayDate ? { k: "Fecha", v: displayDate } : null,
  ].filter(Boolean);

  return (
    <main className="mx-auto max-w-5xl p-4">
      {/* Barra superior: volver, votos y PDF rojo */}
      <div className="mb-3 flex items-center justify-between">
        <button
          onClick={handleBack}
          className="inline-flex items-center gap-1 rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50"
        >
          ← Volver atrás
        </button>
        <div className="flex items-center gap-2">
          <button className="rounded-xl border px-3 py-1.5 text-sm">👍 0</button>
          <button className="rounded-xl border px-3 py-1.5 text-sm">👎 0</button>
          {url_pdf && (
            <a
              href={url_pdf}
              target="_blank"
              rel="noreferrer"
              className="rounded-xl bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700"
            >
              Ver PDF
            </a>
          )}
        </div>
      </div>

      {/* Cabecera y chips */}
      <div className="rounded-2xl border bg-white p-5 shadow-sm">
        <h1 className="text-2xl font-semibold leading-snug text-gray-900">
          {title || "Documento BOE"}
        </h1>
        <div className="mt-4 flex flex-wrap gap-2">
          {chips.map(({ k, v }, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-2 rounded-full border bg-gray-50 px-3 py-1 text-xs"
            >
              <span className="text-gray-500">{k}:</span>
              <span className="font-medium text-gray-900">{String(v)}</span>
            </span>
          ))}
        </div>

        {completeTitle && completeTitle !== (title || "").trim() && (
          <div className="mt-4 rounded-xl bg-gray-50 p-4">
            <h2 className="mb-1 text-sm font-medium text-gray-700">Título completo</h2>
            <p className="text-gray-900 whitespace-pre-wrap">{completeTitle}</p>
          </div>
        )}
      </div>

      {/* Resumen */}
      {summary && (
        <section className="mt-5 rounded-2xl border bg-white p-5 shadow-sm">
          <h2 className="text-base font-semibold text-gray-900">Resumen</h2>
          <p className="mt-2 whitespace-pre-wrap text-gray-800">{summary}</p>
        </section>
      )}

      {/* Contenido */}
      <section className="mt-5 rounded-2xl border bg-white p-5 shadow-sm">
        <h2 className="text-base font-semibold text-gray-900">Contenido</h2>
        <article className="prose mt-3 max-w-none">
          {html ? (
            <div dangerouslySetInnerHTML={{ __html: html }} />
          ) : (
            <pre className="whitespace-pre-wrap break-words text-[0.98rem] leading-relaxed text-gray-900">
              {content}
            </pre>
          )}
        </article>
      </section>

      {/* Metadatos */}
      {metadata && Object.keys(metadata).length > 0 && (
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

      {/* Acciones finales */}
      <div className="mt-6 flex flex-wrap items-center gap-2">
        <CopyButton text={html || content || ""} />
        {sourceUrl && (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noreferrer"
            className="rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50"
          >
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
      await navigator.clipboard.writeText(text);
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
