import React from "react";

const MetaPill = ({ label, value }) => {
  if (!value || value === "—") return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-gray-50 px-2 py-0.5 border border-gray-200 text-xs text-gray-700 w-fit">
      <span className="font-medium">{label}:</span>
      <span className="truncate">{String(value)}</span>
    </span>
  );
};

const looksLikeGzipBase64 = (s) => {
  if (!s || typeof s !== "string") return false;
  if (/^H4sI[A-Za-z0-9+/=]{10,}$/.test(s)) return true;
  if (/^[A-Za-z0-9+/=]{50,}$/.test(s) && !/\s/.test(s)) return true;
  return false;
};

const ResultCard = ({
  item,
  compact = false,
  expanded = false,
  onToggle,
  onOpen,
  getPublishedDate,
  getEpigrafe,
  getItemTitle,
  getFullTitle,
  getIdentifier,
  getSeccion,
  getDepartamento,
}) => {
  const fecha = getPublishedDate ? getPublishedDate(item) : "—";

  const identificador = getIdentifier
    ? getIdentifier(item)
    : (item?.identificador ?? item?.id ?? item?.boe_id ?? "—");

  const tituloResumen = getItemTitle
    ? getItemTitle(item)
    : (item?.titulo_resumen ??
       item?.titulo_corto ??
       item?.titulo ??
       "—");

  const tituloCompleto = getFullTitle
    ? getFullTitle(item)
    : (item?.titulo ??
       item?.titulo_completo ??
       item?.title ??
       "");

  const seccion = getSeccion
    ? getSeccion(item)
    : (item?.seccion?.nombre ??
       item?.seccion_nombre ??
       item?.seccion ??
       (Array.isArray(item?.secciones) ? item.secciones[0] : null) ??
       "—");

  const departamento = getDepartamento
    ? getDepartamento(item)
    : (item?.departamento?.nombre ??
       item?.departamento_nombre ??
       item?.departamento ??
       (Array.isArray(item?.departamentos) ? item.departamentos[0] : null) ??
       "—");

  const epigrafe = getEpigrafe
    ? getEpigrafe(item)
    : (item?.epigrafe?.nombre ??
       item?.epigrafe_nombre ??
       item?.epigrafe_titulo ??
       item?.epigrafe ??
       (Array.isArray(item?.epigrafes) ? item.epigrafes[0] : null) ??
       "—");

  const norm = (s) => (s || "")
    .replace(/\s+/g, " ")
    .replace(/[·•\-–—]+/g, "-")
    .trim()
    .toLowerCase();

  const sameTitle =
    norm(tituloResumen) !== "—" &&
    !!norm(tituloCompleto) &&
    norm(tituloResumen) === norm(tituloCompleto);

  const hasReadableResumen = !!(item?.resumen && !looksLikeGzipBase64(item.resumen));
  const shouldShowExpandable = (!sameTitle && !!tituloCompleto) || hasReadableResumen;

  return (
    <article
      className="group rounded-2xl border border-gray-100 bg-white shadow-sm hover:shadow-md transition"
      aria-labelledby={`pub-${item?.id || identificador}-title`}
    >
      <div className="p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-2 text-xs text-gray-600">
          <span className="font-mono text-[11px] text-gray-800">
            ID: {identificador || "—"}
          </span>
          <span className="h-3 w-px bg-gray-200" />
          <span className="tabular-nums">{fecha}</span>
        </div>

        <button type="button" onClick={onOpen} className="mt-2 block text-left w-full">
          <h3
            id={`pub-${item?.id || identificador}-title`}
            className="text-gray-900 font-semibold text-base sm:text-lg leading-snug line-clamp-2"
            title={tituloResumen}
          >
            {tituloResumen}
          </h3>
        </button>

        {/* 📌 Lista vertical de metadatos */}
        <div className="mt-2 flex flex-col gap-1">
          <MetaPill label="Sección" value={seccion} />
          <MetaPill label="Departamento" value={departamento} />
          <MetaPill label="Epígrafe" value={epigrafe} />
        </div>

        {!compact && hasReadableResumen && (
          <p className="mt-3 text-sm text-gray-700 line-clamp-3">{item.resumen}</p>
        )}

        <div className="mt-3 flex items-center justify-between">
          <button
            type="button"
            onClick={onOpen}
            className="text-sm text-blue-700 hover:text-blue-900 underline"
          >
            Abrir detalle
          </button>

          {shouldShowExpandable && (
            <button
              type="button"
              onClick={onToggle}
              className="text-sm text-gray-600 hover:text-gray-900"
              aria-expanded={expanded}
              aria-controls={`rc-${identificador}-expand`}
            >
              {expanded ? "Ocultar" : "Ver más"}
            </button>
          )}
        </div>

        {expanded && shouldShowExpandable && (
          <div
            id={`rc-${identificador}-expand`}
            className="mt-3 rounded-lg border border-gray-100 bg-gray-50 p-3 text-sm text-gray-800"
          >
            {!sameTitle && !!tituloCompleto && (
              <>
                <div className="font-medium text-gray-900 mb-1">Título completo</div>
                <p className="leading-relaxed">{tituloCompleto}</p>
              </>
            )}

            {hasReadableResumen && (
              <>
                {!sameTitle && !!tituloCompleto && <div className="h-3" />}
                <div className="font-medium text-gray-900 mb-1">Resumen</div>
                <p className="leading-relaxed">{item.resumen}</p>
              </>
            )}
          </div>
        )}
      </div>
    </article>
  );
};

export default ResultCard;
