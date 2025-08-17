import React from "react";
import { Link } from "react-router-dom";
import heroImage from "../assets/hero.png";
import boeFlowImage from "../assets/boe-flow.png";
import {
  DocumentTextIcon,
  BoltIcon,
  ChatBubbleLeftRightIcon,
} from "@heroicons/react/24/outline";

const Home = () => {
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
              Nuestra plataforma convierte el lenguaje técnico del BOE en resúmenes claros, comprensibles y accesibles para todos.
            </p>
            <Link
              to="/boe"
              className="inline-block bg-blue-600 hover:bg-blue-700 text-white font-semibold py-3 px-6 rounded-lg transition"
            >
              Explorar publicaciones
            </Link>
          </div>
          <div className="flex-1">
            <img src={heroImage} alt="AI simplifies BOE" className="w-full max-w-md mx-auto" />
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
            <h2 className="text-3xl font-bold mb-4 text-gray-800">¿Cómo funciona?</h2>
            <p className="text-gray-600 text-lg">
              Nuestra IA analiza las publicaciones del BOE, extrae la información esencial y
              la transforma en un resumen sencillo, destaca los impactos clave y permite a los usuarios comentar y participar.
            </p>
          </div>
        </div>
      </section>

      {/* FEATURES */}
      <section className="py-20 px-6 bg-gray-50">
        <div className="max-w-6xl mx-auto text-center">
          <h2 className="text-3xl font-bold mb-12 text-gray-800">Características clave</h2>
          <div className="grid sm:grid-cols-2 md:grid-cols-3 gap-10">
            {/* Feature 1: Summary */}
            <div className="flex flex-col items-center text-center">
              <DocumentTextIcon className="w-12 h-12 text-blue-600 mb-4" />
              <h3 className="font-semibold text-lg text-gray-800 mb-2">Resúmenes claros</h3>
              <p className="text-gray-600 text-sm">
                Comprende fácilmente lo importante sin leer páginas de jerga legal.
              </p>
            </div>

            {/* Feature 2: Key Impacts */}
            <div className="flex flex-col items-center text-center">
              <BoltIcon className="w-12 h-12 text-blue-600 mb-4" />
              <h3 className="font-semibold text-lg text-gray-800 mb-2">Impactos clave</h3>
              <p className="text-gray-600 text-sm">
                Detecta las consecuencias relevantes de cada norma publicada.
              </p>
            </div>

            {/* Feature 3: Comments */}
            <div className="flex flex-col items-center text-center">
              <ChatBubbleLeftRightIcon className="w-12 h-12 text-blue-600 mb-4" />
              <h3 className="font-semibold text-lg text-gray-800 mb-2">Participación</h3>
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
