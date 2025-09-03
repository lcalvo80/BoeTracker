import { useState } from "react";

export default function SearchBox({ value, onChange, placeholder = "Buscar...", autoFocus = true }) {
  const [composing, setComposing] = useState(false);

  return (
    <div className="flex items-center gap-2 rounded-xl border px-3 py-2 bg-white shadow-sm">
      <input
        className="flex-1 outline-none text-sm"
        type="text"
        value={value}
        onChange={(e) => !composing && onChange(e.target.value)}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={(e) => { setComposing(false); onChange(e.target.value); }}
        placeholder={placeholder}
        autoFocus={autoFocus}
        aria-label="Buscar en BOE"
      />
      {value?.length > 0 && (
        <button
          type="button"
          onClick={() => onChange("")}
          className="text-xs text-gray-600 hover:text-black"
          aria-label="Limpiar búsqueda"
        >
          ✕
        </button>
      )}
    </div>
  );
}
