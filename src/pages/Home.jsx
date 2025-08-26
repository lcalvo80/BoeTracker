import React, { useState } from "react";
import { Link } from "react-router-dom";
import heroImage from "../assets/hero.png";
import boeFlowImage from "../assets/boe-flow.png";
import {
  DocumentTextIcon,
  BoltIcon,
  ChatBubbleLeftRightIcon,
  CheckCircleIcon,
} from "@heroicons/react/24/outline";
import { SignedIn, SignedOut, SignUpButton } from "@clerk/clerk-react";

const Home = () => {
  // Toggle de facturación
  const [annual, setAnnual] = useState(true);
  const monthlyPrice = 5;
  const annualPrice = 54; // $54 si se paga anual (~10% ahorro)
  const savingsPct = Math.round((1 - annualPrice / (monthlyPrice * 12)) * 100);

  return (
    <div>
      {/* HERO */}
      <section className="bg-gradient-to-b from-white to-gray-50 py-16 px-6">
        <div className="max-w-6xl mx-auto flex flex-col-reverse md:flex-row items-center gap-10">
          <div className="text-center md:text-left flex-1">
            <h1 className="text-4xl sm:text-5xl font-extrabold text-gray-900 mb-6 leading-tight">
              Simplifica las publicaciones del BOE<br />con ayuda de IA
            </h1>
            <p className="text-lg text-gray-600 mb-8">
              Nuestra plataforma convierte el lenguaje técnico del BOE en
              resúmenes claros, comprensibles y accesibles para todos.
            </p>

            {/* CTAs principales */}
            <div className="flex flex-col sm:flex-row gap-3 justify-center md:justify-start">
              <Link
                to="/boe"
                className="inline-flex items-center justify-center bg-blue-600 hover:bg-blue-700 text-white font-semibold py-3 px-6 rounded-lg transition"
              >
                Explorar publicaciones
              </Link>

              <SignedOut>
                <SignUpButton mode="modal" afterSignUpUrl="/pricing">
                  <button className="inline-flex items-center justify-center bg-gray-900 hover:bg-black text-white font-semibold py-3 px-6 rounded-lg transition">
                    Crear cuenta y suscribirme
                  </button>
                </SignUpButton>
              </SignedOut>

              <SignedIn>
                <Link
                  to="/pricing"
                  className="inline-flex items-center justify-center bg-gray-900 hover:bg-black text-white font-semibold py-3 px-6 rounded-lg transition"
                >
                  Ver planes
                </Link>
              </SignedIn>
            </div>
          </div>

          <div className="flex-1">
            <img
              src={heroImage}
              alt="AI simplifies BOE"
              className="w-full max-w-md mx-auto"
            />
          </div>
        </div>
      </section>

      {/* PRICING / SUBSCRIPTION */}
      <section className="py-20 px-6 bg-white">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-10">
            <h2 className="text-3xl font-bold text-gray-900">Elige tu plan</h2>
            <p className="text-gray-600 mt-2">
              Acceso <span className="font-medium">Gratis</span> sin registro
              (limitado), o <span className="font-medium">Pro</span> con todo el
              contenido.
            </p>

            {/* Toggle mensual/anual */}
            <div className="inline-flex items-center gap-3 mt-6 bg-gray-100 rounded-full p-1">
              <button
                type="button"
                onClick={() => setAnnual(false)}
                className={`px-4 py-2 text-sm rounded-full transition ${
                  !annual ? "bg-white shadow text-gray-900" : "text-gray-600"
                }`}
              >
                Mensual
              </button>
              <button
                type="button"
                onClick={() => setAnnual(true)}
                className={`px-4 py-2 text-sm rounded-full transition relative ${
                  annual ? "bg-white shadow text-gray-900" : "text-gray-600"
                }`}
              >
                Anual
                {annual && (
                  <span className="absolute -top-2 -right-2 text-[10px] bg-green-100 text-green-800 rounded-full px-2 py-[2px]">
                    Ahorra {savingsPct}%
                  </span>
                )}
              </button>
            </div>
          </div>

          {/* Tarjetas de precios */}
          <div className="grid md:grid-cols-2 gap-6 items-stretch">
            {/* FREE */}
            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md transition flex flex-col h-full">
              <div className="flex items-center justify-between">
                <h3 className="text-xl font-semibold text-gray-900">Gratis</h3>
                <span className="text-xs px-2 py-1 rounded-full bg-gray-100 text-gray-700">
                  Sin registro
                </span>
              </div>
              <p className="text-gray-600 mt-2">
                Acceso limitado sin necesidad de iniciar sesión.
              </p>

              <div className="mt-6">
                <div className="text-3xl font-bold text-gray-900">$0</div>
                <div className="text-gray-500 text-sm">para siempre</div>
              </div>

              <ul className="mt-6 space-y-2 text-sm">
                {[
                  "Ver listado público de publicaciones",
                  "Vista previa de contenido",
                  "Búsqueda básica",
                ].map((item) => (
                  <li key={item} className="flex items-start gap-2">
                    <CheckCircleIcon className="w-5 h-5 text-green-600 mt-[2px]" />
                    <span className="text-gray-700">{item}</span>
                  </li>
                ))}
                {[
                  "Descarga de documentos",
                  "Resúmenes extendidos e impactos",
                  "Comentarios y participación completa",
                ].map((item) => (
                  <li
                    key={item}
                    className="flex items-start gap-2 opacity-60"
                  >
                    <CheckCircleIcon className="w-5 h-5 text-gray-300 mt-[2px]" />
                    <span className="text-gray-500 line-through">{item}</span>
                  </li>
                ))}
              </ul>

              {/* CTA */}
              <div className="mt-auto pt-6">
                <Link
                  to="/boe"
                  className="inline-flex w-full items-center justify-center rounded-lg bg-gray-900 text-white px-4 py-2.5 text-sm font-medium hover:bg-black transition"
                >
                  Explorar gratis
                </Link>
              </div>
            </div>

            {/* PRO */}
            <div className="rounded-2xl border border-blue-200 bg-gradient-to-b from-white to-blue-50 p-6 shadow-md hover:shadow-lg transition relative flex flex-col h-full">
              <span className="absolute -top-3 left-6 bg-blue-600 text-white text-xs px-2 py-1 rounded-full shadow">
                Recomendado
              </span>

              <h3 className="text-xl font-semibold text-gray-900">Pro</h3>
              <p className="text-gray-600 mt-2">
                Acceso total a resúmenes, impactos y participación.
              </p>

              <div className="mt-6 flex items-baseline gap-2">
                {!annual ? (
                  <>
                    <div className="text-4xl font-extrabold text-gray-900">
                      ${monthlyPrice}
                    </div>
                    <div className="text-gray-500 text-sm">/mes</div>
                  </>
                ) : (
                  <>
                    <div className="text-4xl font-extrabold text-gray-900">
                      ${annualPrice}
                    </div>
                    <div className="text-gray-500 text-sm">/año</div>
                    <span className="ml-2 text-xs bg-green-100 text-green-800 px-2 py-[2px] rounded-full">
                      Ahorra {savingsPct}%
                    </span>
                  </>
                )}
              </div>

              <ul className="mt-6 space-y-2 text-sm">
                {[
                  "Todo el contenido completo",
                  "Resúmenes extendidos e impactos clave",
                  "Búsqueda avanzada",
                  "Comentarios y participación completa",
                  "Acceso prioritario a nuevas funciones",
                ].map((item) => (
                  <li key={item} className="flex items-start gap-2">
                    <CheckCircleIcon className="w-5 h-5 text-green-600 mt-[2px]" />
                    <span className="text-gray-800">{item}</span>
                  </li>
                ))}
              </ul>

              {/* CTA */}
              <div className="mt-auto pt-6">
                <SignedOut>
                  <SignUpButton mode="modal" afterSignUpUrl="/pricing">
                    <button className="inline-flex w-full items-center justify-center rounded-lg bg-blue-600 text-white px-4 py-2.5 text-sm font-medium hover:bg-blue-700 transition">
                      Crear cuenta y suscribirme
                    </button>
                  </SignUpButton>
                </SignedOut>

                <SignedIn>
                  <Link
                    to="/pricing"
                    className="inline-flex w-full items-center justify-center rounded-lg bg-blue-600 text-white px-4 py-2.5 text-sm font-medium hover:bg-blue-700 transition"
                  >
                    Continuar con suscripción
                  </Link>
                </SignedIn>
              </div>

              <p className="text-xs text-gray-500 mt-4">
                Puedes cancelar en cualquier momento. Facturación{" "}
                {annual ? "anual" : "mensual"}.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section className="py-20 px-6 bg-white">
        <div className="max-w-5xl mx-auto grid md:grid-cols-2 gap-10 items-center">
          <div>
            <img src={boeFlowImage} alt="Cómo funciona" className="w-full" />
          </div>
          <div>
            <h2 className="text-3xl font-bold mb-4 text-gray-800">
              ¿Cómo funciona?
            </h2>
            <p className="text-gray-600 text-lg">
              Nuestra IA analiza las publicaciones del BOE, extrae la
              información esencial y la transforma en un resumen sencillo,
              destaca los impactos clave y permite a los usuarios comentar y
              participar.
            </p>
          </div>
        </div>
      </section>

      {/* FEATURES al final */}
      <section className="py-20 px-6 bg-gray-50">
        <div className="max-w-6xl mx-auto text-center">
          <h2 className="text-3xl font-bold mb-12 text-gray-800">
            Características clave
          </h2>
          <div className="grid sm:grid-cols-2 md:grid-cols-3 gap-10">
            <div className="flex flex-col items-center text-center">
              <DocumentTextIcon className="w-12 h-12 text-blue-600 mb-4" />
              <h3 className="font-semibold text-lg text-gray-800 mb-2">
                Resúmenes claros
              </h3>
              <p className="text-gray-600 text-sm">
                Comprende fácilmente lo importante sin leer páginas de jerga
                legal.
              </p>
            </div>
            <div className="flex flex-col items-center text-center">
              <BoltIcon className="w-12 h-12 text-blue-600 mb-4" />
              <h3 className="font-semibold text-lg text-gray-800 mb-2">
                Impactos clave
              </h3>
              <p className="text-gray-600 text-sm">
                Detecta las consecuencias relevantes de cada norma publicada.
              </p>
            </div>
            <div className="flex flex-col items-center text-center">
              <ChatBubbleLeftRightIcon className="w-12 h-12 text-blue-600 mb-4" />
              <h3 className="font-semibold text-lg text-gray-800 mb-2">
                Participación
              </h3>
              <p className="text-gray-600 text-sm">
                Opina, debate y conoce las ideas de otros ciudadanos.
              </p>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
};

export default Home;
