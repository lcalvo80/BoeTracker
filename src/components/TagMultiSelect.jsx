// FrontEnd/boe/src/components/TagMultiSelect.jsx
import React, { Fragment, useMemo, useState } from "react";
import { Combobox, Transition } from "@headlessui/react";
import { FaCheck, FaChevronDown, FaTimes } from "react-icons/fa";

/**
 * TagMultiSelect
 * props:
 *  - label: string
 *  - options: Array<{ value: string, label: string }>
 *  - values: string[] (array de "value" seleccionados)
 *  - onChange: (newValues: string[]) => void
 *  - placeholder?: string
 *  - className?: string
 */
const TagMultiSelect = ({
  label,
  options,
  values,
  onChange,
  placeholder = "Selecciona opciones...",
  className = "",
}) => {
  const [query, setQuery] = useState("");

  const selectedObjects = useMemo(() => {
    const map = new Map(options.map((o) => [o.value, o]));
    return (values || []).map((v) => map.get(v)).filter(Boolean);
  }, [options, values]);

  const filtered =
    query.trim() === ""
      ? options
      : options.filter((o) =>
          o.label.toLowerCase().includes(query.toLowerCase()) ||
          o.value.toLowerCase().includes(query.toLowerCase())
        );

  const removeValue = (value) => {
    const next = (values || []).filter((v) => v !== value);
    onChange(next);
  };

  const clearAll = () => onChange([]);

  return (
    <div className={`w-full ${className}`}>
      {label && (
        <label className="text-sm font-medium text-gray-700 mb-1 block">
          {label}
        </label>
      )}

      <Combobox
        value={selectedObjects}
        onChange={(objs) => onChange(objs.map((o) => o.value))}
        multiple
      >
        <div className="relative">
          <div className="relative w-full cursor-default overflow-hidden rounded-md bg-white text-left border focus-within:ring-2 focus-within:ring-blue-500">
            <Combobox.Input
              className="w-full border-0 py-2 pl-3 pr-8 text-sm text-gray-900 placeholder-gray-400 focus:outline-none"
              displayValue={() => ""}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={placeholder}
            />
            <Combobox.Button className="absolute inset-y-0 right-0 flex items-center pr-2">
              <FaChevronDown className="text-gray-400" />
            </Combobox.Button>
          </div>

          <Transition
            as={Fragment}
            leave="transition ease-in duration-100"
            leaveFrom="opacity-100"
            leaveTo="opacity-0"
            afterLeave={() => setQuery("")}
          >
            <Combobox.Options className="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md bg-white py-1 text-sm shadow-lg ring-1 ring-black ring-opacity-5 focus:outline-none">
              {filtered.length === 0 ? (
                <div className="cursor-default select-none px-4 py-2 text-gray-500">
                  Sin resultados
                </div>
              ) : (
                filtered.map((opt) => {
                  const checked = values?.includes(opt.value);
                  return (
                    <Combobox.Option
                      key={opt.value}
                      value={opt}
                      className={({ active }) =>
                        `relative cursor-pointer select-none py-2 pl-8 pr-3 ${
                          active ? "bg-blue-50 text-blue-900" : "text-gray-900"
                        }`
                      }
                    >
                      <span className="absolute left-2 top-2.5">
                        <span
                          className={`inline-flex h-4 w-4 items-center justify-center rounded border ${
                            checked
                              ? "bg-blue-600 border-blue-600 text-white"
                              : "bg-white border-gray-300"
                          }`}
                        >
                          {checked && <FaCheck className="text-[10px]" />}
                        </span>
                      </span>
                      <span className="block truncate">
                        {opt.label}{" "}
                        <span className="text-gray-400">({opt.value})</span>
                      </span>
                    </Combobox.Option>
                  );
                })
              )}
            </Combobox.Options>
          </Transition>
        </div>
      </Combobox>

      {/* Chips seleccionados */}
      {selectedObjects.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {selectedObjects.map((opt) => (
            <span
              key={opt.value}
              className="inline-flex items-center gap-1 rounded-full bg-blue-100 text-blue-800 px-2 py-1 text-xs"
            >
              {opt.label}
              <button
                type="button"
                onClick={() => removeValue(opt.value)}
                className="ml-1 inline-flex h-4 w-4 items-center justify-center rounded-full hover:bg-blue-200"
                aria-label={`Quitar ${opt.label}`}
                title={`Quitar ${opt.label}`}
              >
                <FaTimes />
              </button>
            </span>
          ))}
          {selectedObjects.length > 1 && (
            <button
              type="button"
              onClick={clearAll}
              className="text-xs text-gray-600 underline hover:text-gray-800"
            >
              Limpiar todo
            </button>
          )}
        </div>
      )}
    </div>
  );
};

export default TagMultiSelect;
