import React, { useState } from "react";
import { FaEnvelope, FaPaperPlane } from "react-icons/fa";

const ContactPage = () => {
  const [contactEmail, setContactEmail] = useState("");
  const [contactMessage, setContactMessage] = useState("");

  const handleSendEmail = () => {
    if (!contactEmail.trim() || !contactMessage.trim()) {
      alert("Por favor, rellena tu correo y mensaje.");
      return;
    }

    const subject = encodeURIComponent("Mensaje desde BOE tracker");
    const body = encodeURIComponent(`Usuario: ${contactEmail}\n\nMensaje:\n${contactMessage}`);
    const mailtoLink = `mailto:luiscalvo80@gmail.com?subject=${subject}&body=${body}`;
    window.location.href = mailtoLink;
  };

  return (
    <div className="max-w-2xl mx-auto px-4 sm:px-6 lg:px-8 py-10 bg-white rounded-lg shadow">
      <h1 className="text-3xl font-bold mb-6 text-gray-800 flex items-center gap-2">
        <FaEnvelope className="text-blue-600" /> Contáctanos
      </h1>

      <p className="text-gray-600 mb-8">
        Si tienes preguntas, sugerencias o propuestas, puedes escribirnos directamente.
      </p>

      <div className="space-y-6">
        <div>
          <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
            Tu correo electrónico
          </label>
          <input
            type="email"
            id="email"
            placeholder="ejemplo@correo.com"
            value={contactEmail}
            onChange={(e) => setContactEmail(e.target.value)}
            className="w-full border px-4 py-2 rounded focus:outline-none focus:ring focus:border-blue-300"
          />
        </div>

        <div>
          <label htmlFor="message" className="block text-sm font-medium text-gray-700 mb-1">
            Tu mensaje
          </label>
          <textarea
            id="message"
            placeholder="Escribe tu mensaje..."
            rows={5}
            value={contactMessage}
            onChange={(e) => setContactMessage(e.target.value)}
            className="w-full border px-4 py-2 rounded resize-none focus:outline-none focus:ring focus:border-blue-300"
          />
        </div>

        <button
          onClick={handleSendEmail}
          className="flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold px-6 py-3 rounded transition w-full sm:w-auto"
        >
          <FaPaperPlane /> Enviar mensaje
        </button>
      </div>
    </div>
  );
};

export default ContactPage;
