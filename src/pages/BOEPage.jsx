// src/pages/BOEPage.jsx
import React, { useEffect, useMemo, useState } from "react";
import Calendar from "react-calendar";
import "react-calendar/dist/Calendar.css";
import { useNavigate } from "react-router-dom";
import { getItems, getFilterOptions } from "../services/boeService";
import TagMultiSelect from "../components/TagMultiSelect";
import ResultCard from "../components/ResultCard";
import Section from "../components/ui/Section";

const ITEMS_PER_PAGE = 12;

const toIsoDate = (d) =>
  d instanceof Date && !isNaN(d)
    ? d.toLocaleDateString("sv-SE", { timeZone: "Europe/Madrid" })
    : null;

const formatDateEsLong = (dateObj) =>
  new Intl.DateTimeFormat("es-ES", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "Europe/Madrid",
  }).format(dateObj);

const getPublishedDate = (item) => {
  if (item?.created_at) {
    const d = new Date(item.created_at);
    if (!isNaN(d)) return formatDateEsLong(d);
  }
  const day = item?.created_at_date || item?.fecha_publicacion || item?.fecha;
  if (day && /^\d{4}-\d{2}-\d{2}$/.test(day)) {
    const [y, m, d] = day.split("-").map(Number);
    return formatDateEsLong(new Date(Date.UTC(y, m - 1, d)));
  }
  return "—";
};

const inputBase =
  "w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm placeholder:text-gray-400 " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600/60 disabled:opacity-50";

const BOEPage = () => {
  const navigate = useNavigate();

  const [items, setItems] = useState([]);
  const [expandedIds, setExpandedIds] = useState([]);
  const [currentPage, setCurrentPage] = useState(1);
  const [compactMode, setCompactMode] = useState(false);
  const [totalItems, setTotalItems] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const [filters, setFilters] = useState({
    q_adv: "",
    identificador: "",
    control: "",
    secciones: [],
    departamentos: [],
    epigrafes: [],
    fecha: null,
    fecha_desde: null,
    fecha_hasta: null,
    useRange: false,
  });

  const [typing, setTyping] = useState(null); // debounce
  const [options, setOptions] = useState({
    departamentos: [],
    epigrafes: [],
    secciones: [],
  });

  // Cargar opciones de filtros (arrays simples desde el servicio)
  useEffect(() => {
    const load = async () => {
      try {
        const res = await getFilterOptions();
        setOptions({
          departamentos: Array.isArray(res.departamentos) ? res.departamentos : [],
          secciones: Array.isArray(res.secciones) ? res.secciones : [],
          epigrafes: Array.isArray(res.epigrafes) ? res.epigrafes : [],
        });
      } catch (e) {
        console.error("Error loading filter options", e);
        setOptions({ departamentos: [], secciones: [], epigrafes: [] });
      }
    };
    load();
  }, []);

  // params → backend (boeService serializa correctamente)
  const queryParams = useMemo(() => {
    const {
      q_adv, identificador, control,
      secciones, departamentos, epigrafes,
      fecha, fecha_desde, fecha_hasta, useRange,
    } = filters;

    return {
      // texto
      q: q_adv?.trim() || undefined, // el backend espera 'q'
      identificador: identificador?.trim() || undefined,
      control: control?.trim() || undefined,

      // arrays (se serializan como claves repetidas)
      secciones,
      departamentos,
      epigrafes,

      // fechas
      useRange,
      fecha,
      fecha_desde,
      fecha_hasta,

      // paginación/orden
      page: currentPage,
      limit: ITEMS_PER_PAGE,
      sort_by: "created_at",
      sort_dir: "desc",
    };
  }, [filters, currentPage]);

  // Carga de items
  useEffect(() => {
    const fetchItems = async () => {
      try {
        setError("");
        setLoading(true);
        const data = await getItems(queryParams);
        setItems(Array.isArray(data?.items) ? data.items : []);
        setTotalItems(Number.isFinite(data?.total) ? data.total : 0);
      } catch (err) {
        console.error("Error fetching items", err);
        setItems([]);
        setTotalItems(0);
        setError(
          err?.response?.data?.error ||
          err?.response?.data?.detail ||
          err?.message ||
          "Error al cargar publicaciones."
        );
      } finally {
        setLoading(false);
      }
    };
    fetchItems();
  }, [queryParams]);

  // Handlers
  const debouncedTextChange = (name, value) => {
    if (typing) clearTimeout(typing);
    const t = setTimeout(() => {
      setFilters((prev) => ({ ...prev, [name]: value }));
      setCurrentPage(1);
    }, 350);
    setTyping(t);
  };

  const handleTextChange = (e) => {
    const { name, value } = e.target;
    debouncedTextChange(name, value);
  };

  const setSecciones = (arr) => {
    setFilters((p) => ({ ...p, secciones: Array.isArray(arr) ? arr : [] }));
    setCurrentPage(1);
  };
  const setDepartamentos = (arr) => {
    setFilters((p) => ({ ...p, departamentos: Array.isArray(arr) ? arr : [] }));
    setCurrentPage(1);
  };
  const setEpigrafes = (arr) => {
    setFilters((p) => ({ ...p, epigrafes: Array.isArray(arr) ? arr : [] }));
    setCurrentPage(1);
  };

  const toggleExpanded = (e, id) => {
    e.stopPropagation();
    setExpandedIds((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );
  };

  const resetFilters = () => {
    setFilters({
      q_adv: "",
      identificador: "",
      control: "",
      secciones: [],
      departamentos: [],
      epigrafes: [],
      fecha: null,
      fecha_desde: null,
      fecha_hasta: null,
      useRange: false,
    });
    setCurrentPage(1);
  };

  const totalPages = Math.ceil((totalItems || 0) / ITEMS_PER_PAGE);

  const seccionOpts = (options.secciones || []).map((o) => ({
    value: o.codigo,
    label: (o.nombre || o.codigo || "").toString().trim(),
  }));
  const departamentoOpts = (options.departamentos || []).map((o) => ({
    value: o.codigo,
    label: (o.nombre || o.codigo || "").toString().trim(),
  }));
  const epigrafeOpts = (options.epigrafes || []).map((e) => ({
    value: e,
    label: (e || "").toString().trim(),
  }));

  const DateModeToggle = () => (
    <div className="flex items-center gap-2">
      <span className="text-sm text-gray-700">Fecha</span>
      <div className="ml-auto inline-flex rounded-lg border border-gray-200 bg-gray-50 p-1">
        {["Exacta", "Rango"].map((label, idx) => {
          const active = (idx === 1) === !!filters.useRange;
          return (
            <button
              key={label}
              type="button"
              onClick={() =>
                setFilters((p) => ({
                  ...p,
                  useRange: idx === 1,
                  ...(idx === 1
                    ? { fecha: null }
                    : { fecha_desde: null, fecha_hasta: null }),
                }))
              }
              className={`px-3 py-1.5 text-xs rounded-md transition ${
                active
                  ? "bg-white shadow-sm text-gray-900"
                  : "text-gray-600 hover:text-gray-900"
              }`}
              aria-pressed={active}
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 lg:px-8">
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        {/* Sidebar filtros */}
        <aside className="lg:col-span-4">
          <div className="sticky top-6 space-y-4 bg-white rounded-2xl shadow-sm p-6 border border-gray-100">
            <div className="flex justify-between items-center">
              <h2 className="text-lg font-bold">Filtros</h2>
              <button
                onClick={resetFilters}
                className="text-sm text-blue-700 underline hover:text-blue-900"
              >
                Limpiar filtros
              </button>
            </div>

            <Section title="Búsqueda" defaultOpen>
              <div className="space-y-3">
                <div>
                  <label className="text-sm font-medium text-gray-800 mb-1 block">
                    Búsqueda avanzada
                  </label>
                  <input
                    type="text"
                    name="q_adv"
                    value={filters.q_adv}
                    onChange={handleTextChange}
                    className={inputBase}
                    placeholder='Escribe lo que buscas. Ej.: ayudas vivienda, "contrato menor"'
                  />
                  <p className="mt-2 text-xs text-gray-500">
                    Usa comillas para frase exacta y{" "}
                    <code className="rounded bg-gray-100 px-1 py-0.5">
                      -palabra
                    </code>{" "}
                    para excluir.
                  </p>
                </div>

                <div>
                  <label className="text-sm font-medium text-gray-700 mb-1 block">
                    Identificador
                  </label>
                  <input
                    type="text"
                    name="identificador"
                    onChange={handleTextChange}
                    value={filters.identificador}
                    className={inputBase}
                    placeholder="Buscar por identificador"
                  />
                </div>

                <div>
                  <label className="text-sm font-medium text-gray-700 mb-1 block">
                    Control
                  </label>
                  <input
                    type="text"
                    name="control"
                    onChange={handleTextChange}
                    value={filters.control}
                    className={inputBase}
                    placeholder="Buscar por control"
                  />
                </div>
              </div>
            </Section>

            <Section title="Taxonomías" defaultOpen>
              <div className="space-y-3">
                <TagMultiSelect
                  label="Sección (múltiple)"
                  options={seccionOpts}
                  values={filters.secciones}
                  onChange={setSecciones}
                  placeholder="Escribe para filtrar secciones..."
                />
                <TagMultiSelect
                  label="Departamento (múltiple)"
                  options={departamentoOpts}
                  values={filters.departamentos}
                  onChange={setDepartamentos}
                  placeholder="Escribe para filtrar departamentos..."
                />
                <TagMultiSelect
                  label="Epígrafe (múltiple)"
                  options={epigrafeOpts}
                  values={filters.epigrafes}
                  onChange={setEpigrafes}
                  placeholder="Escribe para filtrar epígrafes..."
                />
              </div>
            </Section>

            <Section title="Fecha" defaultOpen={false}>
              <div className="space-y-3">
                <DateModeToggle />
                {!filters.useRange ? (
                  <div>
                    <label className="text-sm font-medium text-gray-700 mb-1 block">
                      Fecha de creación (exacta)
                    </label>
                    <div className="rounded-2xl border border-gray-100 p-2 shadow-inner">
                      <Calendar
                        onChange={(date) =>
                          setFilters((prev) => ({ ...prev, fecha: date }))
                        }
                        value={filters.fecha}
                        className="w-full"
                        tileClassName="text-sm"
                      />
                    </div>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 gap-3">
                    <div>
                      <label className="text-sm font-medium text-gray-700 mb-1 block">
                        Desde
                      </label>
                      <input
                        type="date"
                        className={inputBase}
                        value={filters.fecha_desde ? toIsoDate(filters.fecha_desde) : ""}
                        onChange={(e) =>
                          setFilters((prev) => ({
                            ...prev,
                            fecha_desde: e.target.value ? new Date(e.target.value) : null,
                          }))
                        }
                      />
                    </div>
                    <div>
                      <label className="text-sm font-medium text-gray-700 mb-1 block">
                        Hasta
                      </label>
                      <input
                        type="date"
                        className={inputBase}
                        value={filters.fecha_hasta ? toIsoDate(filters.fecha_hasta) : ""}
                        onChange={(e) =>
                          setFilters((prev) => ({
                            ...prev,
                            fecha_hasta: e.target.value ? new Date(e.target.value) : null,
                          }))
                        }
                      />
                    </div>
                  </div>
                )}
              </div>
            </Section>
          </div>
        </aside>

        {/* Resultados */}
        <section className="lg:col-span-8 space-y-6">
          <div className="flex justify-between items-center">
            <h2 className="text-2xl font-bold">Publicaciones encontradas</h2>

            <div className="inline-flex rounded-lg border border-gray-200 p-1 bg-gray-50">
              {[
                { key: "full", label: "Completo", active: !compactMode },
                { key: "compact", label: "Compacto", active: compactMode },
              ].map(({ key, label, active }) => (
                <button
                  key={key}
                  onClick={() => setCompactMode(key === "compact")}
                  className={`px-3 py-1.5 text-sm rounded-md transition ${
                    active
                      ? "bg-white shadow-sm text-gray-900"
                      : "text-gray-600 hover:text-gray-900"
                  }`}
                  aria-pressed={active}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {error ? (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
              {error}
            </div>
          ) : loading ? (
            <div className="p-6 text-gray-600">Cargando...</div>
          ) : items?.length > 0 ? (
            <div className={`grid gap-4 ${compactMode ? "grid-cols-1" : "sm:grid-cols-2"}`}>
              {items.map((item) => (
                <ResultCard
                  key={item.id}
                  item={item}
                  compact={compactMode}
                  getPublishedDate={getPublishedDate}
                  expanded={expandedIds.includes(item.id)}
                  onToggle={(e) => toggleExpanded(e, item.id)}
                  onOpen={() =>
                    navigate(`/item/${encodeURIComponent(item.identificador)}`)
                  }
                />
              ))}
            </div>
          ) : (
            <div className="rounded-2xl border border-dashed border-gray-300 bg-gray-50 p-10 text-center">
              <div className="text-3xl mb-2">🧐</div>
              <p className="text-gray-700 font-medium">
                No hay coincidencias con los filtros actuales.
              </p>
              <p className="text-sm text-gray-500 mt-1">
                Prueba a ampliar el rango de fechas o limpiar los filtros.
              </p>
              <button
                onClick={resetFilters}
                className="mt-4 inline-flex items-center rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm hover:bg-gray-100"
              >
                Limpiar filtros
              </button>
            </div>
          )}

          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-1 pt-6 flex-wrap">
              <button
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                className="px-3 py-1 rounded border text-sm text-gray-600 hover:bg-gray-100 disabled:opacity-40"
                disabled={currentPage === 1}
                aria-label="Página anterior"
              >
                &larr;
              </button>

              {currentPage > 3 && (
                <>
                  <button
                    onClick={() => setCurrentPage(1)}
                    className="px-3 py-1 text-sm border rounded hover:bg-gray-100"
                  >
                    1
                  </button>
                  <span className="px-1 text-sm text-gray-400">...</span>
                </>
              )}

              {Array.from({ length: totalPages }, (_, i) => i + 1)
                .filter(
                  (page) =>
                    page === 1 ||
                    page === totalPages ||
                    Math.abs(currentPage - page) <= 2
                )
                .map((page) => (
                  <button
                    key={page}
                    onClick={() => setCurrentPage(page)}
                    className={`px-3 py-1 text-sm border rounded ${
                      page === currentPage
                        ? "bg-blue-600 text-white"
                        : "text-gray-700 hover:bg-gray-100"
                    }`}
                    aria-current={page === currentPage ? "page" : undefined}
                  >
                    {page}
                  </button>
                ))}

              {currentPage < totalPages - 2 && (
                <>
                  <span className="px-1 text-sm text-gray-400">...</span>
                  <button
                    onClick={() => setCurrentPage(totalPages)}
                    className="px-3 py-1 text-sm border rounded hover:bg-gray-100"
                  >
                    {totalPages}
                  </button>
                </>
              )}

              <button
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                className="px-3 py-1 rounded border text-sm text-gray-600 hover:bg-gray-100 disabled:opacity-40"
                disabled={currentPage === totalPages}
                aria-label="Página siguiente"
              >
                &rarr;
              </button>
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

export default BOEPage;
