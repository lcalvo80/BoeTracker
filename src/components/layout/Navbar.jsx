import React, { useState } from "react";
import { Link } from "react-router-dom";
import { FaBars, FaTimes } from "react-icons/fa";

const Navbar = () => {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <nav className="bg-white shadow-md sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-16">
        <Link to="/" className="text-xl font-bold text-blue-700">BOE Tracker</Link>

        <div className="hidden md:flex space-x-6">
          <Link to="/" className="text-gray-700 hover:text-blue-600">Inicio</Link>
          <Link to="/boe" className="text-gray-700 hover:text-blue-600">Publicaciones</Link>
          <Link to="/contact" className="text-gray-700 hover:text-blue-600">Contacto</Link>
        </div>

        <div className="md:hidden">
          <button onClick={() => setMenuOpen(!menuOpen)} className="text-gray-700 focus:outline-none">
            {menuOpen ? <FaTimes size={20} /> : <FaBars size={20} />}
          </button>
        </div>
      </div>

      {menuOpen && (
        <div className="md:hidden bg-white px-4 py-3 space-y-2 shadow-md">
          <Link to="/" className="block text-gray-700 hover:text-blue-600">Inicio</Link>
          <Link to="/boe" className="block text-gray-700 hover:text-blue-600">Publicaciones</Link>
          <Link to="/contact" className="block text-gray-700 hover:text-blue-600">Contacto</Link>
        </div>
      )}
    </nav>
  );
};

export default Navbar;
