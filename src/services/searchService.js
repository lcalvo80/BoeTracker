// src/services/searchService.js
import api, { createAbortController } from "./http";
// Si quieres reusar tu mapFilters, expórtalo desde boeService o crea uno aquí.
let currentController = null;

export async function searchItems(params = {}) {
  // Cancela la petición previa en curso
  if (currentController) currentController.abort();
  currentController = createAbortController();

  const { data } = await api.get("/items", {
    params, // incluye { q, seccion_codigo, ... page, pageSize }
    signal: currentController.signal,
  });
  return data;
}
