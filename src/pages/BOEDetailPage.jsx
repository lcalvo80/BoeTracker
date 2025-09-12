// src/pages/BOEDetailPage.jsx
import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getBoeById } from "../services/boeService";
import api from "../services/http";

/**
 * BOEDetailPage.jsx — UI alineada con mockups (chips, PDF rojo, acordeones)
 * Mantiene tu fetch + pako on-demand para campos base64+gzip.
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
      const toInflate = [
        "content",
        "summary",
        "html",
        "epigrafe",
        "full_title",
        "titulo_completo",
      ];
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

  if (loading) {
    return (
      <main role="main" className="mx-auto max-w-5xl p-4 md:p-6">
        <Skeleton />
      </main>
    );
  }
  if (error) {
    return (
      <main role="main" className="mx-auto max-w-3xl p-4 md:p-6">
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
    date,
    section,
    number,
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

  const displayDate = date
    ? new Date(date).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "2-digit" })
    : null;

  const completeTitle = (full_title || titulo_completo || title || "").trim();
  const showCompleteTitleBlock = completeTitle && completeTitle !== (title || "").trim();

  const isImpactReport =
    /impacto/i.test(section || "") || /impacto/i.test(String(metadata?.tipo || ""));

  // Campos para chips (según capturas)
  const chips = [
    section ? { k: "Sección", v: section } : null,
    metadata?.departamento || metadata?.ministerio ? { k: "Departamento", v: metadata.departamento || metadata.ministerio } : null,
    epigrafe ? { k: "Epígrafe", v: epigrafe } : null,
    (identificador || metadata?.identificador || metadata?.id_boe) ? { k: "Identificador", v: identificador || metadata?.identificador || metadata?.id_boe } : null,
    (control || metadata?.control) ? { k: "Control", v: control || metadata?.control } : null,
    displayDate ? { k: "Fecha publicación", v: displayDate } : null,
  ].filter(Boolean);

  // Resumen estructurado (si backend lo trae); si no, usamos summary plano.
  const resumen = {
    contexto: metadata?.resumen?.contexto,
    fechas: metadata?.resumen?.fechas,
    conclusion: metadata?.resumen?.conclusion,
  };

  return (
    <main role="main" className="mx-auto max-w-5xl p-4 md:p-6">
      {/* BreadCrumb/Volver */}
      <div className="mb-3 flex items-center justify-between">
        <button
          onClick={handleBack}
          className="inline-flex items-center gap-1 rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50"
        >
          <span aria-hidden>←</span> Volver atrás
        </button>

        <div className="flex items-center gap-2">
          {/* Votos dummy para UI */}
          <VoteButton icon="👍" label="0" />
          <VoteButton icon="👎" label="0" />
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

      {/* Cabecera del documento */}
      <div className="rounded-2xl border bg-white p-5 shadow-sm">
        <h1 className="text-2xl font-semibold leading-snug text-gray-900">
          {title || "Documento BOE"}
        </h1>

        {/* Chips metadatos */}
        <div className="mt-4 flex flex-wrap gap-2">
          {chips.map(({ k, v }, i) => (
            <KVPill key={i} k={k} v={String(v)} />
          ))}
        </div>

        {showCompleteTitleBlock && (
          <div className="mt-4 rounded-xl bg-gray-50 p-4">
            <h2 className="mb-1 text-sm font-medium text-gray-700">Título completo</h2>
            <p className="text-gray-900 whitespace-pre-wrap">{completeTitle}</p>
          </div>
        )}
      </div>

      {/* Resumen (acordeón) */}
      {(summary || resumen.contexto || resumen.fechas || resumen.conclusion) && (
        <Accordion title="Resumen" defaultOpen className="mt-5">
          {summary && (
            <p className="text-gray-800 whitespace-pre-wrap">{summary}</p>
          )}

          {!summary && (
            <div className="space-y-4">
              {resumen.contexto && (
                <SectionCard title="Contexto">
                  <p className="text-gray-800 whitespace-pre-wrap">{resumen.contexto}</p>
                </SectionCard>
              )}
              {resumen.fechas && (
                <SectionCard title="Fechas clave">
                  {Array.isArray(resumen.fechas) ? (
                    <ul className="list-disc pl-5 text-gray-800">
                      {resumen.fechas.map((f, i) => <li key={i}>{String(f)}</li>)}
                    </ul>
                  ) : (
                    <p className="text-gray-800 whitespace-pre-wrap">{String(resumen.fechas)}</p>
                  )}
                </SectionCard>
              )}
              {resumen.conclusion && (
                <SectionCard title="Conclusión">
                  <p className="text-gray-800 whitespace-pre-wrap">{resumen.conclusion}</p>
                </SectionCard>
              )}
            </div>
          )}
        </Accordion>
      )}

      {/* Cuerpo */}
      <Accordion title="Contenido" defaultOpen className="mt-5">
        <article className="prose prose-gray max-w-none">
          {html ? (
            <div dangerouslySetInnerHTML={{ __html: html }} />
          ) : (
            <pre className="whitespace-pre-wrap break-words text-[0.98rem] leading-relaxed text-gray-900">
              {content}
            </pre>
          )}
        </article>
      </Accordion>

      {/* Informe de Impacto o Metadatos (acordeón) */}
      {Object.keys(metadata || {}).length > 0 && (
        <Accordion
          title={isImpactReport ? "Informe de Impacto" : "Metadatos"}
          className="mt-5"
        >
          {isImpactReport ? (
            <ImpactBlocks metadata={metadata} />
          ) : (
            <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {Object.entries(metadata).map(([k, v]) => (
                <div key={k} className="rounded-xl border p-3 text-sm">
                  <dt className="text-gray-500">{k}</dt>
                  <dd className="mt-1 break-words text-gray-900">
                    {Array.isArray(v) ? v.join(", ") : String(v)}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </Accordion>
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

/* ======================
 * Subcomponentes UI
 * =====================*/

function Skeleton() {
  return (
    <>
      <div className="h-5 w-24 rounded bg-gray-200" />
      <div className="mt-4 rounded-2xl border bg-white p-5 shadow-sm">
        <div className="h-8 w-3/4 rounded bg-gray-200" />
        <div className="mt-4 flex flex-wrap gap-2">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-6 w-36 rounded-full bg-gray-100 border" />
          ))}
        </div>
      </div>
      <div className="mt-5 rounded-2xl border bg-white p-5 shadow-sm">
        <div className="h-5 w-24 rounded bg-gray-200" />
        <div className="mt-3 h-40 w-full rounded bg-gray-100" />
      </div>
    </>
  );
}

function VoteButton({ icon, label }) {
  return (
    <button
      type="button"
      className="inline-flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm hover:bg-gray-50"
      title="No operativo (solo UI)"
    >
      <span>{icon}</span>
      <span className="tabular-nums">{label}</span>
    </button>
  );
}

function KVPill({ k, v }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border bg-gray-50 px-3 py-1 text-xs">
      <span className="text-gray-500">{k}:</span>
      <span className="font-medium text-gray-900">{v}</span>
    </span>
  );
}

function Accordion({ title, children, defaultOpen = false, className = "" }) {
  return (
    <details className={`group rounded-2xl border bg-white p-5 shadow-sm ${className}`} open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center justify-between">
        <h2 className="text-base font-semibold text-gray-900">{title}</h2>
        <span className="ml-3 rounded-full border px-2 py-0.5 text-xs text-gray-500 group-open:rotate-180 transition">
          ▾
        </span>
      </summary>
      <div className="mt-3">{children}</div>
    </details>
  );
}

function SectionCard({ title, children }) {
  return (
    <div className="rounded-xl border p-4">
      <h3 className="text-sm font-medium text-gray-700">{title}</h3>
      <div className="mt-2 text-sm">{children}</div>
    </div>
  );
}

function ImpactBlocks({ metadata }) {
  const afectados = metadata?.afectados;
  const cambios = metadata?.cambios || metadata?.cambios_operativos;
  const riesgos = metadata?.riesgos || metadata?.riesgos_potenciales;
  const beneficios = metadata?.beneficios;
  const recomendaciones = metadata?.recomendaciones;

  const Block = ({ title, value, bullet = true }) => {
    if (!value) return null;
    const isList = Array.isArray(value);
    return (
      <div className="mt-3 rounded-xl border p-4">
        <h3 className="text-sm font-medium text-gray-700">{title}</h3>
        {isList ? (
          <ul className={`${bullet ? "list-disc pl-5" : ""} mt-2 text-sm text-gray-900`}>
            {value.map((x, i) => <li key={i} className="break-words">{String(x)}</li>)}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-gray-900 whitespace-pre-wrap break-words">{String(value)}</p>
        )}
      </div>
    );
  };

  return (
    <section>
      <Block title="Afectados" value={afectados} />
      <Block title="Cambios operativos" value={cambios} />
      <Block title="Riesgos potenciales" value={riesgos} />
      <Block title="Beneficios previstos" value={beneficios} />
      <Block title="Recomendaciones" value={recomendaciones} />
    </section>
  );
}

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
