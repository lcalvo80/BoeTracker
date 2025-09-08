import React, { Suspense, lazy } from "react";
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import Navbar from "./components/layout/Navbar";

// Code-splitting de páginas
const Home = lazy(() => import("./pages/Home"));
const AboutUsPage = lazy(() => import("./pages/AboutUsPage"));
const ContactPage = lazy(() => import("./pages/ContactPage"));
const BOEPage = lazy(() => import("./pages/BOEPage"));
const BOEDetailPage = lazy(() => import("./pages/BOEDetailPage"));
const PricingPage = lazy(() => import("./pages/PricingPage"));

// Fallback de carga simple y accesible
const Loader = () => (
  <div role="status" aria-live="polite" className="p-6 text-center">
    Cargando…
  </div>
);

// 404 básico (evita pantalla en blanco)
const NotFound = () => (
  <div className="p-6">
    <h1 className="text-2xl font-semibold mb-2">Página no encontrada</h1>
    <p>La ruta solicitada no existe.</p>
  </div>
);

const App = () => {
  return (
    <Router>
      {/* Enlace de salto para accesibilidad */}
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 bg-black text-white px-3 py-2 rounded"
      >
        Saltar al contenido
      </a>

      <Navbar />

      <main id="content" className="main-content min-h-screen">
        <Suspense fallback={<Loader />}>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/about" element={<AboutUsPage />} />
            <Route path="/contact" element={<ContactPage />} />
            <Route path="/boe" element={<BOEPage />} />
            {/* Compat: detalle por /item/:id y /boe/:id apuntan al mismo componente */}
            <Route path="/item/:id" element={<BOEDetailPage />} />
            <Route path="/boe/:id" element={<BOEDetailPage />} />
            <Route path="/pricing" element={<PricingPage />} />
            {/* 404 */}
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Suspense>
      </main>
    </Router>
  );
};

export default App;
