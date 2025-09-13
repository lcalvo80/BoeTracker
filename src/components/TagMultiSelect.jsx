import React, { useMemo, useState } from "react";

/**
 * TagMultiSelect
 * - options: [{ value: string, label: string }]
 * - values: string[] (selected values)
 * - onChange: (string[]) => void
 * - label: string
 * - placeholder?: string
 * - showCode?: boolean (default true) -> Si false, NO muestra " (COD)" junto al nombre.
 */
const TagMultiSelect = ({
  label,
  options,
  values,
  onChange,
  placeholder = "Escribe para filtrar...",
  showCode = true,
}) => {
  const [query, setQuery] = useState("");

  const normalizedOptions = useMemo(() => {
    // Asegurar estructura { value, label }
    return (options || []).map((o) => ({
      value: String(o.value ?? "").trim(),
      label: String(o.label ?? "").trim(),
    }));
  }, [options]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return normalizedOptions;
    return normalizedOptions.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        o.value.toLowerCase().includes(q)
    );
  }, [normalizedOptions, query]);

  const toggle = (val) => {
    if (!onChange) return;
    if (values?.includes(val)) {
      onChange(values.filter((v) => v !== val));
    } else {
      onChange([...(values || []), val]);
    }
  };

  const remove = (val) => {
    if (!onChange) return;
    onChange((values || []).filter((v) => v !== val));
  };

  const isSelected = (val) => (values || []).includes(val);

  const renderOptionText = (opt) => {
    // Cuando showCode=false, solo nombre (label) sin "(value)"
    if (!showCode) return opt.label || opt.value;
    // Con showCode, mostramos "Nombre (COD)" si ambos existen
    if (opt.label && opt.value) return `${opt.label} (${opt.value})`;
    return opt.label || opt.value;
    };

  const selectedObjects = useMemo(() => {
    const map = new Map(normalizedOptions.map((o) => [o.value, o]));
    return (values || []).map((v) => map.get(v) || { value: v, label: v });
  }, [values, normalizedOptions]);

  return (
    <div>
      {label && (
        <label className="text-sm font-medium text-gray-700 mb-1 block">
          {label}
        </label>
      )}

      {/* Input búsqueda */}
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600/60"
        placeholder={placeholder}
      />

      {/* Chips seleccionados */}
      {selectedObjects?.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {selectedObjects.map((opt) => (
            <span
              key={opt.value}
              className="inline-flex items-center gap-1 rounded-full bg-blue-50 text-blue-800 border border-blue-200 px-2 py-0.5 text-xs"
            >
              <span className="truncate max-w-[12rem]">
                {renderOptionText(opt)}
              </span>
              <button
                type="button"
                onClick={() => remove(opt.value)}
                className="rounded-full hover:bg-blue-100 p-0.5"
                aria-label={`Quitar ${opt.label}`}
                title="Quitar"
              >
                <svg viewBox="0 0 20 20" fill="currentColor" className="h-3.5 w-3.5">
                  <path
                    fillRule="evenodd"
                    d="M10 8.586l3.182-3.182a1 1 0 111.414 1.414L11.414 10l3.182 3.182a1 1 0 01-1.414 1.414L10 11.414l-3.182 3.182a1 1 0 01-1.414-1.414L8.586 10 5.404 6.818a1 1 0 111.414-1.414L10 8.586z"
                    clipRule="evenodd"
                  />
                </svg>
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Lista opciones */}
      <div className="mt-2 max-h-48 overflow-auto rounded-lg border border-gray-200 divide-y divide-gray-100">
        {filtered.length === 0 ? (
          <div className="p-3 text-xs text-gray-500">Sin resultados</div>
        ) : (
          filtered.map((opt) => {
            const active = isSelected(opt.value);
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => toggle(opt.value)}
                className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-50 focus-visible:outline-none ${
                  active ? "bg-blue-50" : "bg-white"
                }`}
                aria-pressed={active}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate">{renderOptionText(opt)}</span>
                  {active && (
                    <svg
                      viewBox="0 0 20 20"
                      fill="currentColor"
                      className="h-4 w-4 text-blue-600 shrink-0"
                    >
                      <path
                        fillRule="evenodd"
                        d="M16.707 5.293a1 1 0 010 1.414l-7.071 7.071a1 1 0 01-1.414 0L3.293 9.95a1 1 0 011.414-1.414l3.102 3.101 6.364-6.364a1 1 0 011.414 0z"
                        clipRule="evenodd"
                      />
                    </svg>
                  )}
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
};

export default TagMultiSelect;
