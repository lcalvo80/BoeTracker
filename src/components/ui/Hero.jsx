import React from "react";
import { Link } from "react-router-dom";
import heroImage from "../../assets/hero.png";

const Hero = () => {
  return (
    <section className="bg-gradient-to-b from-white to-gray-50 py-16 px-4 sm:px-6 lg:px-8">
      <div className="max-w-6xl mx-auto flex flex-col-reverse md:flex-row items-center gap-10">
        <div className="text-center md:text-left flex-1">
          <h1 className="text-3xl sm:text-5xl font-extrabold text-gray-900 mb-6 leading-tight">
            Convierte el BOE en lenguaje claro<br /> con ayuda de la IA
          </h1>
          <p className="text-base sm:text-lg text-gray-600 mb-8">
            Analizamos el contenido del BOE y lo transformamos en explicaciones sencillas, accesibles y útiles.
          </p>
          <Link
            to="/boe"
            className="inline-block bg-blue-600 hover:bg-blue-700 text-white font-semibold py-3 px-6 rounded-lg transition"
          >
            Ver publicaciones
          </Link>
        </div>
        <div className="flex-1">
          <img src={heroImage} alt="AI helping with BOE" className="w-full max-w-md mx-auto" />
        </div>
      </div>
    </section>
  );
};

export default Hero;
