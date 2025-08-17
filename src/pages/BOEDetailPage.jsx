import React, { useState, useEffect, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  getItemById,
  getResumen,
  getImpacto,
  getComments,
  postComment,
  likeItem,
  dislikeItem,
} from "../services/boeService";
import {
  FaFilePdf,
  FaArrowLeft,
  FaThumbsUp,
  FaThumbsDown,
  FaChevronDown,
  FaChevronUp,
} from "react-icons/fa";

/* ======================== Helpers de fecha ======================== */

const formatDateEsLong = (dateObj) =>
  new Intl.DateTimeFormat("es-ES", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "Europe/Madrid",
  }).format(dateObj);

/** Publicación: solo fecha (prioriza created_at; fallback created_at_date/fecha_publicacion) */
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

/** Comentarios: fecha+hora corta (si llega timestamp) */
const formatDateTimeEs = (ts) => {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d)) return ts;
  return new Intl.DateTimeFormat("es-ES", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Madrid",
  }).format(d);
};

/* ======================== UI toggles ======================== */

const SectionToggle = ({ title, icon, children, defaultOpen = true }) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="mb-8 bg-gray-50 p-5 rounded-lg shadow-md border border-gray-200">
      <div
        className="flex items-center justify-between cursor-pointer mb-2"
        onClick={() => setOpen(!open)}
      >
        <h2 className="text-xl font-semibold flex items-center gap-2 text-gray-800">
          {icon} {title}
        </h2>
        {open ? <FaChevronUp /> : <FaChevronDown />}
      </div>
      {open && children}
    </section>
  );
};

const SubSectionToggle = ({ label, children }) => {
  const [open, setOpen] = useState(true);
  return (
    <div className="mb-4 border-l-4 pl-3 border-blue-200">
      <div
        className="flex justify-between items-center cursor-pointer mb-1"
        onClick={() => setOpen(!open)}
      >
        <h3 className="text-sm font-semibold text-gray-800">{label}</h3>
        {open ? <FaChevronUp className="text-xs" /> : <FaChevronDown className="text-xs" />}
      </div>
      {open && <div className="text-sm text-gray-700">{children}</div>}
    </div>
  );
};

/* ======================== Página detalle ======================== */

const BOEDetailPage = () => {
  const params = useParams();
  // Acepta rutas /item/:identificador o /item/:id
  const identificador = useMemo(
    () => decodeURIComponent(params.identificador ?? params.id ?? ""),
    [params]
  );

  const navigate = useNavigate();

  const [item, setItem] = useState(null);
  const [resumen, setResumen] = useState(null);
  const [impacto, setImpacto] = useState(null);
  const [comments, setComments] = useState([]);
  const [likes, setLikes] = useState(0);
  const [dislikes, setDislikes] = useState(0);

  const [newComment, setNewComment] = useState("");
  const [username, setUsername] = useState("");

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      if (!identificador) {
        setError("Identificador no válido.");
        setLoading(false);
        return;
      }

      setLoading(true);
      setError("");

      try {
        // 1) Cargar el item primero (si falla, mostramos error y paramos)
        const itemRes = await getItemById(identificador);
        const itemData = itemRes?.data;
        if (!itemData || Object.keys(itemData).length === 0) {
          throw new Error("No se encontró la publicación.");
        }
        if (cancelled) return;

        setItem(itemData);
        setLikes(itemData.likes ?? 0);
        setDislikes(itemData.dislikes ?? 0);

        // 2) Cargar resto en paralelo, sin bloquear el render del item
        const [resResumen, resImpacto, resComments] = await Promise.allSettled([
          getResumen(identificador),
          getImpacto(identificador),
          getComments(identificador),
        ]);
        if (cancelled) return;

        if (resResumen.status === "fulfilled") {
          setResumen(resResumen.value?.data || {});
        } else {
          setResumen({});
        }
        if (resImpacto.status === "fulfilled") {
          setImpacto(resImpacto.value?.data || {});
        } else {
          setImpacto({});
        }
        if (resComments.status === "fulfilled") {
          setComments(Array.isArray(resComments.value?.data) ? resComments.value.data : []);
        } else {
          setComments([]);
        }
      } catch (e) {
        if (cancelled) return;
        console.error("Error al cargar detalle:", e);
        setError(e?.message || "Error al cargar la publicación.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [identificador]);

  const handleLike = async () => {
    try {
      const res = await likeItem(identificador);
      setLikes(res?.data?.likes ?? (likes + 1)); // fallback optimista
    } catch (err) {
      console.error("Error al dar like:", err);
    }
  };

  const handleDislike = async () => {
    try {
      const res = await dislikeItem(identificador);
      setDislikes(res?.data?.dislikes ?? (dislikes + 1)); // fallback optimista
    } catch (err) {
      console.error("Error al dar dislike:", err);
    }
  };

  const handleAddComment = async () => {
    if (!username.trim() || !newComment.trim()) return;
    try {
      await postComment({
        item_identificador: identificador,
        user_name: username.trim(),
        comment: newComment.trim(),
      });
      setUsername("");
      setNewComment("");
      const res = await getComments(identificador);
      setComments(Array.isArray(res?.data) ? res.data : []);
    } catch (err) {
      console.error("Error al enviar comentario:", err);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center items-center h-64">
        <span className="text-gray-500">Cargando publicación...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-3xl mx-auto px-6 py-10 bg-white rounded-lg shadow">
        <button
          onClick={() => navigate(-1)}
          className="text-blue-600 flex items-center mb-6 hover:underline"
        >
          <FaArrowLeft className="mr-2" /> Volver atrás
        </button>
        <div className="bg-red-50 text-red-700 border border-red-200 rounded p-4">
          {error}
        </div>
      </div>
    );
  }

  if (!item) {
    return (
      <div className="max-w-3xl mx-auto px-6 py-10 bg-white rounded-lg shadow">
        <button
          onClick={() => navigate(-1)}
          className="text-blue-600 flex items-center mb-6 hover:underline"
        >
          <FaArrowLeft className="mr-2" /> Volver atrás
        </button>
        <div className="text-gray-600">No se pudo cargar la publicación.</div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-6 py-10 bg-white rounded-lg shadow">
      <button
        onClick={() => navigate(-1)}
        className="text-blue-600 flex items-center mb-6 hover:underline"
      >
        <FaArrowLeft className="mr-2" /> Volver atrás
      </button>

      {/* Cabecera */}
      <section className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 mb-2">{item.titulo_resumen}</h1>
        {!!item.titulo && <p className="text-sm text-gray-700 mb-2">{item.titulo}</p>}

        {/* Orden: Sección, Departamento, Epígrafe */}
        <div className="text-sm text-gray-600 space-y-1">
          <p><strong>Sección:</strong> {item.seccion_nombre}</p>
          <p><strong>Departamento:</strong> {item.departamento_nombre}</p>
          <p><strong>Epígrafe:</strong> {item.epigrafe}</p>
          <p><strong>Identificador:</strong> {item.identificador}</p>
          <p><strong>Control:</strong> {item.control}</p>
          <p><strong>Fecha publicación:</strong> {getPublishedDate(item)}</p>
        </div>
      </section>

      {/* Acciones */}
      <div className="flex items-center gap-4 mb-8 flex-wrap">
        <button
          onClick={handleLike}
          className="flex items-center gap-2 bg-green-100 text-green-700 px-3 py-1 rounded-full hover:bg-green-200 transition text-sm"
          aria-label="Me gusta"
        >
          <FaThumbsUp /> {likes}
        </button>
        <button
          onClick={handleDislike}
          className="flex items-center gap-2 bg-red-100 text-red-700 px-3 py-1 rounded-full hover:bg-red-200 transition text-sm"
          aria-label="No me gusta"
        >
          <FaThumbsDown /> {dislikes}
        </button>
        {item.url_pdf && (
          <a
            href={item.url_pdf}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto flex items-center gap-2 bg-red-600 text-white px-4 py-2 rounded-full hover:bg-red-700 transition text-sm"
          >
            <FaFilePdf /> Ver PDF
          </a>
        )}
      </div>

      {/* Resumen */}
      {resumen && (
        <SectionToggle title="Resumen" icon="📝">
          {!!resumen.context && (
            <SubSectionToggle label="📄 Contexto">
              <p>{resumen.context}</p>
            </SubSectionToggle>
          )}

          {(Array.isArray(resumen.key_dates_events) && resumen.key_dates_events.length > 0) && (
            <SubSectionToggle label="📅 Fechas clave">
              <ul className="list-disc pl-4 space-y-1">
                {resumen.key_dates_events.map((e, idx) => (
                  <li key={idx}>{e}</li>
                ))}
              </ul>
            </SubSectionToggle>
          )}

          {!!resumen.conclusion && (
            <SubSectionToggle label="🧾 Conclusión">
              <p>{resumen.conclusion}</p>
            </SubSectionToggle>
          )}
        </SectionToggle>
      )}

      {/* Informe de Impacto */}
      {impacto && (
        <SectionToggle title="Informe de Impacto" icon="🧠" defaultOpen={false}>
          {[
            ["🌍 Afectados", Array.isArray(impacto.afectados) ? impacto.afectados : []],
            ["🔄 Cambios operativos", Array.isArray(impacto.cambios_operativos) ? impacto.cambios_operativos : []],
            ["⚠️ Riesgos potenciales", Array.isArray(impacto.riesgos_potenciales) ? impacto.riesgos_potenciales : []],
            ["✅ Beneficios previstos", Array.isArray(impacto.beneficios_previstos) ? impacto.beneficios_previstos : []],
            ["🧭 Recomendaciones", Array.isArray(impacto.recomendaciones) ? impacto.recomendaciones : []],
          ].map(([label, data], i) =>
            data.length ? (
              <SubSectionToggle label={label} key={i}>
                <ul className="list-disc pl-4 space-y-1">
                  {data.map((e, j) => <li key={j}>{e}</li>)}
                </ul>
              </SubSectionToggle>
            ) : null
          )}
        </SectionToggle>
      )}

      {/* Comentarios */}
      <SectionToggle title="Comentarios" icon="💬" defaultOpen>
        {comments.length === 0 && <p className="text-sm text-gray-500">No hay comentarios aún.</p>}

        {comments.map((c, i) => (
          <div key={i} className="border-t pt-3 mt-2 text-sm">
            <p className="font-semibold">{c.user_name}</p>
            <p className="whitespace-pre-wrap">{c.comment}</p>
            <p className="text-gray-400 text-xs">{formatDateTimeEs(c.created_at)}</p>
          </div>
        ))}

        <div className="mt-4">
          <input
            type="text"
            placeholder="Tu nombre"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full border px-3 py-2 rounded mb-2"
          />
        </div>
        <div>
          <textarea
            placeholder="Escribe un comentario..."
            value={newComment}
            onChange={(e) => setNewComment(e.target.value)}
            className="w-full border px-3 py-2 rounded mb-2"
            rows={3}
          />
        </div>
        <button
          onClick={handleAddComment}
          className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700"
        >
          Enviar comentario
        </button>
      </SectionToggle>
    </div>
  );
};

export default BOEDetailPage;
