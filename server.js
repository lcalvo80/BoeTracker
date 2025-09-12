// server.js — servidor de producción para React (CRA/Vite build)
// CommonJS para evitar requerir "type":"module"

const express = require("express");
const path = require("path");

const app = express();
const PORT = process.env.PORT || 3000;

// 1) Blindaje: si alguien golpea /api en este servicio de FRONTEND, responde 502 JSON
app.use("/api", (_req, res) => {
  res
    .status(502)
    .json({ error: "API no disponible en el servicio de frontend" });
});

// 2) Servir estáticos del build
//   - CRA genera "build/"
//   - Vite genera "dist/" (ajusta la carpeta si usas Vite)
const BUILD_DIR = path.join(__dirname, "build"); // cambia a "dist" si es Vite

app.use(express.static(BUILD_DIR, {
  setHeaders: (res, filePath) => {
    // Cache agresivo para assets fingerprinted
    if (/\.(?:js|css|woff2?|ttf|otf|png|jpg|jpeg|gif|svg)$/.test(filePath)) {
      res.setHeader("Cache-Control", "public, max-age=31536000, immutable");
    }
  },
}));

// 3) SPA fallback: cualquier ruta no-API devuelve index.html
app.get("*", (_req, res) => {
  res.sendFile(path.join(BUILD_DIR, "index.html"));
});

// 4) Arranque
app.listen(PORT, () => {
  console.log(`Frontend running on http://0.0.0.0:${PORT}`);
});
