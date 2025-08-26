import React from "react";
import MetaChip from "./ui/MetaChip";

const ResultCard = ({
  item,
  compact = false,
  getPublishedDate,
  expanded = false,
  onToggle,
  onOpen,
}) => {
  const title = item.titulo_resumen || item.titulo || "(Sin título)";
  const published = getPublishedDate ? getPublishedDate(item) : "—";

  const onKey = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen?.();
    }
  };

  return (
    <article
      className="group relative rounded-2xl border border-gray-100 bg-white p-5 shadow-sm hover:shadow-md transition-all cursor-pointer"
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={onKey}
      aria-label={`Abrir detalle de ${title}`}
    >
      <div className="mb-3">
        <h3 className={`font-semibold text-gray-900 ${compact ? "text-base" : "text-lg"}`}>
          {title}
        </h3>
        <p className="mt-1 text-xs font-medium text-gray-500">{item.identificador}</p>
      </div>

      {/* Título completo toggle (solo en modo no compacto y si existe) */}
      {!compact && item.titulo && (
        <>
          {expanded && <p className="text-sm text-gray-600 mb-2">{item.titulo}</p>}
          <button
            className="text-sm text-blue-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600/60 rounded"
            onClick={(e) => onToggle?.(e)}
            onKeyDown={(e) => e.stopPropagation()}
            aria-expanded={expanded}
          >
            {expanded ? "Ocultar título completo" : "Ver título completo"}
          </button>
        </>
      )}

      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        <MetaChip>
          Sección: {item.seccion_nombre || item.seccion_codigo || "—"}
        </MetaChip>
        <MetaChip>
          Departamento: {item.departamento_nombre || item.departamento_codigo || "—"}
        </MetaChip>
        <MetaChip>Epígrafe: {item.epigrafe || "—"}</MetaChip>
        <MetaChip>Fecha: {published}</MetaChip>
      </div>

      <div className="mt-4">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onOpen?.();
          }}
          className="inline-flex items-center rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600/60"
        >
          Ver detalle
        </button>
      </div>
    </article>
  );
};

export default ResultCard;
