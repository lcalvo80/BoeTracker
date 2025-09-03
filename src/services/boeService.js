// src/services/boeService.js
import api from "./http";

export const getItems = async (params = {}) => {
const {
secciones,
departamentos,
epigrafes,
...rest
} = params;

const finalParams = {
...rest,
// 👇 traducimos a lo que espera backend
...(Array.isArray(secciones) && secciones.length > 0
? { seccion_codigo: secciones.join(",") }
: {}),
...(Array.isArray(departamentos) && departamentos.length > 0
? { departamento_codigo: departamentos.join(",") }
: {}),
...(Array.isArray(epigrafes) && epigrafes.length > 0
? { epigrafe: epigrafes.join(",") }
: {}),
};

const { data } = await api.get("/items", { params: finalParams });
return data;
};