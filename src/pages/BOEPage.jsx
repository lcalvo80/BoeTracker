import React, { useEffect, useMemo, useState } from "react";
import Calendar from "react-calendar";
import "react-calendar/dist/Calendar.css";
import { useNavigate } from "react-router-dom";
import { getItems, getFilterOptions } from "../services/boeService";
import TagMultiSelect from "../components/TagMultiSelect";

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
  const day = item?.created_at_date || item?.fecha_publicacion;
  if (day && /^\d{4}-\d{2}-\d{2}$/.test(day)) {
    const [y, m, d] = day.split("-").map(Number);
    return formatDateEsLong(new Date(Date.UTC(y, m - 1, d)));
  }
  return "—";
};

const BOEPage = () => {
  const [items, setItems] = useState([]);
  const [expandedIds, setExpandedIds] = useState([]);
  const [currentPage, setCurrentPage] = useState(1);
  const [compactMode, setCompactMode] = useState(false);
  const [totalItems, setTotalItems] = useState(0);
  const [error, setError] = useState("");

  const [filters, setFilters] = useState({
    // Búsqueda avanzada (texto libre) — NO incluye epígrafe/depto/sección
    q_adv: "",
    // filtros textuales
    identificador: "",
    control: "",
    // multiselección
    secciones: [],
    departamentos: [],
    epigrafes: [],
    // fechas
    fecha: null,
    fecha_desde: null,
    fecha_hasta: null,
    useRange: false,
  });

  const [options, setOptions] = useState({
    departamentos: [], // [{codigo, nombre}]
    epigrafes: [],     // [string]
    secciones: [],     // [{codigo, nombre}]
  });

  const navigate = useNavigate();

  // Opciones de filtros
  useEffect(() => {
    const fetchFilterOptions = async () => {
      try {
        const [d, e, s] = await getFilterOptions();
        setOptions({
          departamentos: Array.isArray(d?.data) ? d.data : [],
          epigrafes: Array.isArray(e?.data) ? e.data : [],
          secciones: Array.isArray(s?.data) ? s.data : [],
        });
      } catch (err) {
        console.error("Error loading filter options", err);
      }
    };
    fetchFilterOptions();
  }, []);

  // Parámetros para backend
  const queryParams = useMemo(() => {
    const {
      q_adv, identificador, control,
      secciones, departamentos, epigrafes,
      fecha, fecha_desde, fecha_hasta, useRange,
    } = filters;

    const params = { page: currentPage, limit: ITEMS_PER_PAGE };

    if (q_adv?.trim()) params.q_adv = q_adv.trim();
    if (identificador?.trim()) params.identificador = identificador.trim();
    if (control?.trim()) params.control = control.trim();

    if (Array.isArray(secciones) && secciones.length > 0) {
      params.seccion = secciones.join(",");
    }
    if (Array.isArray(departamentos) && departamentos.length > 0) {
      params.departamento = departamentos.join(",");
    }
    if (Array.isArray(epigrafes) && epigrafes.length > 0) {
      params.epigrafe = epigrafes.join(",");
    }

    if (useRange) {
      const fd = toIsoDate(fecha_desde);
      const fh = toIsoDate(fecha_hasta);
      if (fd) params.fecha_desde = fd;
      if (fh) params.fecha_hasta = fh;
    } else {
      const f = toIsoDate(fecha);
      if (f) params.fecha = f;
    }

    params.sort_by = "created_at";
    params.sort_dir = "desc";

    return params;
  }, [filters, currentPage]);

  // Carga items
  useEffect(() => {
    const fetchItems = async () => {
      try {
        setError("");
        const { data } = await getItems(queryParams);
        setItems(Array.isArray(data?.items) ? data.items : []);
        setTotalItems(typeof data?.total === "number" ? data.total : 0);
      } catch (err) {
        console.error("Error fetching items", err);
        setItems([]);
        setTotalItems(0);
        setError(
          err?.response?.data?.error ||
          err?.message ||
          "Error al cargar publicaciones."
        );
      }
    };
    fetchItems();
  }, [queryParams]);

  // Handlers
  const handleTextChange = (e) => {
    const { name, value } = e.target;
    setFilters((prev) => ({ ...prev, [name]: value }));
    setCurrentPage(1);
  };

  const setSecciones = (arr) => { setFilters((p) => ({ ...p, secciones: arr })); setCurrentPage(1); };
  const setDepartamentos = (arr) => { setFilters((p) => ({ ...p, departamentos: arr })); setCurrentPage(1); };
  const setEpigrafes = (arr) => { setFilters((p) => ({ ...p, epigrafes: arr })); setCurrentPage(1); };

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

  // Mapear opciones para TagMultiSelect
  const seccionOpts = useMemo(
    () => (options.secciones || []).map((o) => ({ value: o.codigo, label: o.nombre || o.codigo })),
    [options.secciones]
  );
  const departamentoOpts = useMemo(
    () => (options.departamentos || []).map((o) => ({ value: o.codigo, label: o.nombre || o.codigo })),
    [options.departamentos]
  );
  const epigrafeOpts = useMemo(
    () => (options.epigrafes || []).map((e) => ({ value: e, label: e })),
    [options.epigrafes]
  );

  return (
    <div className="flex flex-col lg:flex-row gap-8 p-8">
      {/* Sidebar filtros */}
      <aside className="w-full lg:w-1/3 bg-white shadow-md rounded-lg p-6 space-y-4">
        <div className="flex justify-between items-center">
          <h2 className="text-lg font-bold">Filtros</h2>
          <button
            onClick={resetFilters}
            className="text-sm text-blue-600 underline hover:text-blue-800"
          >
            Limpiar filtros
          </button>
        </div>

        {/* Búsqueda avanzada */}
        <div className="border rounded-md p-3 bg-gray-50">
          <label className="text-sm font-semibold text-gray-800 mb-1 block">
            Búsqueda avanzada
          </label>
          <input
            type="text"
            name="q_adv"
            value={filters.q_adv}
            onChange={handleTextChange}
            className="w-full border px-3 py-2 rounded"
            placeholder='Escribe lo que buscas. Ej.: ayudas vivienda, "contrato menor"'
          />
          <p className="mt-2 text-xs text-gray-600">
            Consejo: usa comillas para una frase exacta y un guion para excluir (ej.: <code>-subvención</code>).
          </p>
        </div>

        {/* Identificador */}
        <div>
          <label className="text-sm font-medium text-gray-700 mb-1 block">Identificador</label>
          <input
            type="text"
            name="identificador"
            onChange={handleTextChange}
            value={filters.identificador}
            className="w-full border px-3 py-2 rounded"
            placeholder="Buscar por identificador"
          />
        </div>

        {/* Control */}
        <div>
          <label className="text-sm font-medium text-gray-700 mb-1 block">Control</label>
          <input
            type="text"
            name="control"
            onChange={handleTextChange}
            value={filters.control}
            className="w-full border px-3 py-2 rounded"
            placeholder="Buscar por control"
          />
        </div>

        {/* Sección (chips) */}
        <TagMultiSelect
          label="Sección (múltiple)"
          options={seccionOpts}
          values={filters.secciones}
          onChange={setSecciones}
          placeholder="Escribe para filtrar secciones..."
          className="mt-2"
        />

        {/* Departamento (chips) */}
        <TagMultiSelect
          label="Departamento (múltiple)"
          options={departamentoOpts}
          values={filters.departamentos}
          onChange={setDepartamentos}
          placeholder="Escribe para filtrar departamentos..."
          className="mt-2"
        />

        {/* Epígrafe (chips) */}
        <TagMultiSelect
          label="Epígrafe (múltiple)"
          options={epigrafeOpts}
          values={filters.epigrafes}
          onChange={setEpigrafes}
          placeholder="Escribe para filtrar epígrafes..."
          className="mt-2"
        />

        {/* Fecha exacta vs rango */}
        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <input
              id="use-range"
              type="checkbox"
              checked={filters.useRange}
              onChange={(e) =>
                setFilters((prev) => ({
                  ...prev,
                  useRange: e.target.checked,
                  ...(e.target.checked
                    ? { fecha: null }
                    : { fecha_desde: null, fecha_hasta: null }),
                }))
              }
            />
            <label htmlFor="use-range" className="text-sm text-gray-700">
              Filtrar por rango de fechas (creación)
            </label>
          </div>

          {!filters.useRange ? (
            <div>
              <label className="text-sm font-medium text-gray-700 mb-1 block">Fecha de creación (exacta)</label>
              <div className="rounded border p-2 shadow-inner">
                <Calendar
                  onChange={(date) => setFilters((prev) => ({ ...prev, fecha: date }))}
                  value={filters.fecha}
                  className="w-full"
                  tileClassName="text-sm"
                />
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3">
              <div>
                <label className="text-sm font-medium text-gray-700 mb-1 block">Desde</label>
                <input
                  type="date"
                  className="w-full border px-3 py-2 rounded"
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
                <label className="text-sm font-medium text-gray-700 mb-1 block">Hasta</label>
                <input
                  type="date"
                  className="w-full border px-3 py-2 rounded"
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
      </aside>

      {/* Resultados */}
      <section className="flex-1 space-y-6">
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-2xl font-bold">Publicaciones encontradas</h2>
          <button
            onClick={() => setCompactMode(!compactMode)}
            className="text-sm bg-gray-200 hover:bg-gray-300 text-gray-700 px-4 py-2 rounded"
          >
            {compactMode ? "Modo completo" : "Modo compacto"}
          </button>
        </div>

        {error ? (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
            {error}
          </div>
        ) : items?.length > 0 ? (
          items.map((item) =>
            compactMode ? (
              <div
                key={item.id}
                onClick={() => navigate(`/item/${encodeURIComponent(item.identificador)}`)}
                className="border rounded-lg bg-white p-4 shadow-sm hover:shadow-md transition cursor-pointer"
              >
                <h3 className="font-semibold text-gray-800 text-base mb-1">
                  {item.titulo_resumen}
                </h3>
                <p className="text-sm text-gray-500">{item.identificador}</p>

                <div className="text-xs text-gray-600 space-y-0.5 mt-2">
                  <p><strong>Sección:</strong> {item.seccion_nombre}</p>
                  <p><strong>Departamento:</strong> {item.departamento_nombre}</p>
                  <p><strong>Epígrafe:</strong> {item.epigrafe}</p>
                </div>

                <p className="text-xs text-gray-500 mt-2">
                  <strong>Fecha publicación:</strong> {getPublishedDate(item)}
                </p>
              </div>
            ) : (
              <div
                key={item.id}
                className="border rounded-lg shadow-sm p-6 bg-white hover:shadow-md transition"
              >
                <div className="mb-3">
                  <h3 className="text-xl font-bold text-gray-900 mb-1">
                    {item.titulo_resumen}
                  </h3>
                  <p className="text-sm text-gray-600 font-semibold">{item.identificador}</p>
                </div>

                {expandedIds.includes(item.id) && item.titulo && (
                  <p className="text-sm text-gray-600 mb-2">{item.titulo}</p>
                )}

                {item.titulo && (
                  <button
                    onClick={(e) => toggleExpanded(e, item.id)}
                    className="text-sm text-blue-600 hover:underline mb-2"
                  >
                    {expandedIds.includes(item.id) ? "Ocultar título completo" : "Ver título completo"}
                  </button>
                )}

                <div className="text-sm text-gray-600 space-y-1 mb-3">
                  <p><strong>Sección:</strong> {item.seccion_nombre}</p>
                  <p><strong>Departamento:</strong> {item.departamento_nombre}</p>
                  <p><strong>Epígrafe:</strong> {item.epigrafe}</p>
                </div>

                <div className="flex flex-wrap items-center justify-between text-sm text-gray-500 border-t pt-2">
                  <span><strong>Control:</strong> {item.control}</span>
                  <span><strong>Fecha publicación:</strong> {getPublishedDate(item)}</span>
                </div>

                <div className="mt-4 flex justify-start">
                  <button
                    onClick={() => navigate(`/item/${encodeURIComponent(item.identificador)}`)}
                    className="text-sm bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 transition"
                  >
                    Ver detalle
                  </button>
                </div>
              </div>
            )
          )
        ) : (
          <p className="text-gray-500">No hay coincidencias.</p>
        )}

        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-1 pt-6 flex-wrap">
            <button
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              className="px-3 py-1 rounded border text-sm text-gray-600 hover:bg-gray-100 disabled:opacity-40"
              disabled={currentPage === 1}
            >
              &larr;
            </button>

            {currentPage > 3 && (
              <>
                <button onClick={() => setCurrentPage(1)} className="px-3 py-1 text-sm border rounded hover:bg-gray-100">
                  1
                </button>
                <span className="px-1 text-sm text-gray-400">...</span>
              </>
            )}

            {Array.from({ length: totalPages }, (_, i) => i + 1)
              .filter((page) =>
                page === 1 || page === totalPages || Math.abs(currentPage - page) <= 2
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
                >
                  {page}
                </button>
              ))}

            {currentPage < totalPages - 2 && (
              <>
                <span className="px-1 text-sm text-gray-400">...</span>
                <button onClick={() => setCurrentPage(totalPages)} className="px-3 py-1 text-sm border rounded hover:bg-gray-100">
                  {totalPages}
                </button>
              </>
            )}

            <button
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              className="px-3 py-1 rounded border text-sm text-gray-600 hover:bg-gray-100 disabled:opacity-40"
              disabled={currentPage === totalPages}
            >
              &rarr;
            </button>
          </div>
        )}
      </section>
    </div>
  );
};

export default BOEPage;
