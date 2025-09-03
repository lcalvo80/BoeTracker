import React, { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { FaBars, FaTimes } from "react-icons/fa";
import {
  SignedIn,
  SignedOut,
  UserButton,
  useClerk
} from "@clerk/clerk-react";

const Navbar = () => {
  const [menuOpen, setMenuOpen] = useState(false);
  const { pathname } = useLocation();
  const { openSignIn, openSignUp } = useClerk();

  const closeMenu = () => setMenuOpen(false);
  const isActive = (to) =>
    pathname === to ? "text-blue-700 font-semibold" : "text-gray-700";

  const handleSignUp = () =>
    openSignUp({ mode: "modal", afterSignUpUrl: "/pricing" });

  const handleSignIn = () =>
    openSignIn({ mode: "modal", afterSignInUrl: "/account" });

  return (
    <nav className="bg-white shadow-md sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-16">
        {/* Logo / Marca */}
        <Link to="/" className="text-xl font-bold text-blue-700" onClick={closeMenu}>
          BOE Tracker
        </Link>

        {/* Links desktop */}
        <div className="hidden md:flex space-x-6 items-center">
          <Link to="/" className={`${isActive("/")} hover:text-blue-600`}>
            Inicio
          </Link>
          <Link to="/boe" className={`${isActive("/boe")} hover:text-blue-600`}>
            Publicaciones
          </Link>
          <Link to="/contact" className={`${isActive("/contact")} hover:text-blue-600`}>
            Contacto
          </Link>
          <Link to="/pricing" className={`${isActive("/pricing")} hover:text-blue-600`}>
            Planes
          </Link>

          {/* Área de sesión */}
          <SignedOut>
            <button
              type="button"
              onClick={handleSignUp}
              className="px-3 py-2 text-sm font-medium rounded-md bg-gray-100 text-gray-900 hover:bg-gray-200"
            >
              Crear cuenta
            </button>

            <button
              type="button"
              onClick={handleSignIn}
              className="px-3 py-2 text-sm font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700"
            >
              Iniciar sesión
            </button>
          </SignedOut>

          <SignedIn>
            <Link
              to="/account"
              className="px-3 py-2 text-sm font-medium rounded-md bg-gray-100 text-gray-900 hover:bg-gray-200"
            >
              Mi cuenta
            </Link>
            <UserButton afterSignOutUrl="/" />
          </SignedIn>
        </div>

        {/* Botón hamburguesa en mobile */}
        <div className="md:hidden">
          <button
            type="button"
            onClick={() => setMenuOpen(!menuOpen)}
            className="text-gray-700 focus:outline-none"
            aria-label={menuOpen ? "Cerrar menú" : "Abrir menú"}
          >
            {menuOpen ? <FaTimes size={20} /> : <FaBars size={20} />}
          </button>
        </div>
      </div>

      {/* Menú mobile */}
      {menuOpen && (
        <div className="md:hidden bg-white px-4 py-3 space-y-2 shadow-md">
          <Link to="/" className="block text-gray-700 hover:text-blue-600" onClick={closeMenu}>
            Inicio
          </Link>
          <Link to="/boe" className="block text-gray-700 hover:text-blue-600" onClick={closeMenu}>
            Publicaciones
          </Link>
          <Link to="/contact" className="block text-gray-700 hover:text-blue-600" onClick={closeMenu}>
            Contacto
          </Link>
          <Link to="/pricing" className="block text-gray-700 hover:text-blue-600" onClick={closeMenu}>
            Planes
          </Link>

          {/* Área de sesión en mobile */}
          <div className="pt-2 border-t">
            <SignedOut>
              <button
                type="button"
                onClick={() => { handleSignUp(); closeMenu(); }}
                className="w-full px-3 py-2 text-sm font-medium rounded-md bg-gray-100 text-gray-900 hover:bg-gray-200"
              >
                Crear cuenta
              </button>

              <div className="h-2" />

              <button
                type="button"
                onClick={() => { handleSignIn(); closeMenu(); }}
                className="w-full px-3 py-2 text-sm font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700"
              >
                Iniciar sesión
              </button>
            </SignedOut>

            <SignedIn>
              <Link
                to="/account"
                className="block w-full px-3 py-2 text-sm font-medium rounded-md bg-gray-100 text-gray-900 hover:bg-gray-200 text-center"
                onClick={closeMenu}
              >
                Mi cuenta
              </Link>
              <div className="py-2">
                <UserButton afterSignOutUrl="/" />
              </div>
            </SignedIn>
          </div>
        </div>
      )}
    </nav>
  );
};

export default Navbar;
