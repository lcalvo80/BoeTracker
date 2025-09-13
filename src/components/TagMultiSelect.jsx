import React, { useEffect, useMemo, useRef, useState } from "react";

/**
 * TagMultiSelect
 * - options: [{ value: string, label: string }]
 * - values: string[] (selected values)
 * - onChange: (string[]) => void
 * - label: string
 * - placeholder?: string
 * - showCode?: boolean (default true) -> Si false, NO muestra " (COD)" junto al nombre.
 * - Nota: ahora las opciones aparecen en un dropdown al enfocar/teclear.
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
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const inputRef = useRef(null);

  // Cerrar al hacer click fuera
  useEffect(() => {
    const onDocClick = (e) => {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  // Keyboard: Esc para cerrar
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const normalizedOptions = useMemo(
    () =>
      (options || []).map((o) => ({
        value: String(o.value ?? "").trim(),
        label: String(o.label ?? "").trim(),
      })),
    [options]
  );

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
    if (!showCode) return opt.label || opt.value;
    if (opt.label && opt.value) return `${opt.label} (${opt.value})`;
    return opt.label || opt.value;
  };

  const selectedObjects = useMemo(() => {
    const map = new Map(normalizedOptions.map((o) => [o.value, o]));
    return (values || []).map((v) => map.get(v) || { value: v, label: v });
  }, [values, normalizedOptions]);

  return (
    <div ref={wrapRef} className="relative">
      {label && (
        <label className="text-sm font-medium text-gray-700 mb-1 block">
          {label}
        </label>
      )}

      {/* Input de búsqueda + botón chevron */}
      <div
        className={`flex items-center gap-2 rounded-lg border bg-white px-3 py-2 text-sm ${
          open ? "border-blue-400 ring-2 ring-blue-600/30" : "border-gray-300"
        }`}
        onClick={() => {
          setOpen(true);
          inputRef.current?.focus();
        }}
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        <input
          ref={inputRef}
          type="text"
          value={query}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            if (!open) setOpen(true);
          }}
          className="w-full outline-none placeholder:text-gray-400"
          placeholder={placeholder}
        />
        <button
          type="button"
          aria-label={open ? "Cerrar opciones" : "Abrir opciones"}
          className="shrink-0 rounded-md p-1 hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600/50"
          onClick={(e) => {
            e.stopPropagation();
            setOpen((v) => !v);
            inputRef.current?.focus();
          }}
        >
          <svg
            viewBox="0 0 20 20"
            fill="currentColor"
            className={`h-4 w-4 text-gray-500 transition-transform ${
              open ? "rotate-180" : ""
            }`}
          >
            <path d="M5.23 7.21a.75.75 0 011.06.02L10 11.185l3.71-3.954a.75.75 0 111.08 1.04l-4.24 4.52a.75.75 0 01-1.08 0l-4.24-4.52a.75.75 0 01.02-1.06z" />
          </svg>
        </button>
      </div>

      {/* Chips seleccionados */}
      {selectedObjects?.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {selectedObjects.map((opt) => (
            <span
              key={opt.value}
              className="inline-flex items-center gap-1 rounded-full bg-blue-50 text-blue-800 border border-blue-200 px-2 py-0.5 text-xs"
            >
              <span className="truncate max-w-[16rem]">
                {renderOptionText(opt)}
              </span>
              <button
                type="button"
                onClick={() => remove(opt.value)}
                className="rounded-full hover:bg-blue-100 p-0.5"
                aria-label={`Quitar ${opt.label}`}
                title="Quitar"
              >
                <svg
                  viewBox="0 0 20 20"
                  fill="currentColor"
                  className="h-3.5 w-3.5"
                >
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

      {/* Dropdown de opciones */}
      {open && (
        <div
          className="absolute z-20 mt-1 w-full max-h-56 overflow-auto rounded-lg border border-gray-200 bg-white shadow-lg"
          role="listbox"
        >
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
                  role="option"
                  aria-selected={active}  // ✅ correcto para role="option"
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
      )}
    </div>
  );
};

export default TagMultiSelect;
