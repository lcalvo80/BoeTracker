// src/components/ResultCard.jsx
import React from "react";

const ResultCard = ({
  item,
  compact = false,
  getPublishedDate,
  getEpigrafe,
  getItemTitle,      // <-- NUEVO
  expanded = false,
  onToggle,
  onOpen,
}) => {
  const fecha = getPublishedDate ? getPublishedDate(item) : "—";
  const epigrafe = getEpigrafe ? getEpigrafe(item) : (item?.epigrafe ?? "—");
  const title = getItemTitle ? getItemTitle(item) : (item?.titulo ?? "—"); // <-- NUEVO

  return (
    <article
      className="group rounded-2xl border border-gray-100 bg-white shadow-sm hover:shadow-md transition"
      aria-labelledby={`pub-${item?.id}-title`}
    >
      <button
        type="button"
        onClick={onOpen}
        className="w-full text-left p-4 sm:p-5"
      >
        {/* TÍTULO EN NEGRITA Y RESUMIDO */}
        <h3
          id={`pub-${item?.id}-title`}
          className="text-gray-900 font-semibold text-base sm:text-lg leading-snug line-clamp-2"
          title={title}
        >
          {title}
        </h3>

        {/* Meta info */}
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-600">
          <span className="inline-flex items-center rounded-full bg-gray-50 px-2 py-0.5 border border-gray-200">
            {epigrafe || "—"}
          </span>
          <span className="h-3 w-px bg-gray-200" />
          <span className="tabular-nums">{fecha}</span>
          {item?.identificador && (
            <>
              <span className="h-3 w-px bg-gray-200" />
              <span className="text-gray-500">ID: {item.identificador}</span>
            </>
          )}
        </div>

        {/* Resumen/ cuerpo corto si no es compacto */}
        {!compact && item?.resumen && (
          <p className="mt-3 text-sm text-gray-700 line-clamp-3">{item.resumen}</p>
        )}
      </button>

      {/* Footer de acciones */}
      <div className="px-4 sm:px-5 pb-4 sm:pb-5 -mt-2">
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={onOpen}
            className="text-sm text-blue-700 hover:text-blue-900 underline"
          >
            Abrir detalle
          </button>

          <button
            type="button"
            onClick={onToggle}
            className="text-sm text-gray-600 hover:text-gray-900"
            aria-expanded={expanded}
          >
            {expanded ? "Ocultar" : "Ver más"}
          </button>
        </div>

        {/* Contenido expandible */}
        {expanded && (
          <div className="mt-3 text-sm text-gray-700 space-y-2">
            {item?.titulo && (
              <div>
                <span className="font-medium text-gray-900">Título completo:</span>{" "}
                <span>{item.titulo}</span>
              </div>
            )}
            {item?.departamento?.nombre && (
              <div>
                <span className="font-medium text-gray-900">Departamento:</span>{" "}
                <span>{item.departamento.nombre}</span>
              </div>
            )}
            {item?.seccion?.nombre && (
              <div>
                <span className="font-medium text-gray-900">Sección:</span>{" "}
                <span>{item.seccion.nombre}</span>
              </div>
            )}
          </div>
        )}
      </div>
    </article>
  );
};

export default ResultCard;
