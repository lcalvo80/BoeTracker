import React from "react";
import Calendar from "react-calendar"; // Instalar react-calendar: npm install react-calendar
import "react-calendar/dist/Calendar.css"; // Importar estilos del calendario
import "../../styles/layout/Navbar.css";         // para Navbar.jsx
import "../../styles/layout/Sidebar.css";        // para Sidebar.jsx
import "../../styles/layout/Layout.css";         // para Layout.jsx
import "../../styles/layout/NotificationCard.css"; // para NotificationCard.jsx


const Sidebar = () => {
  return (
    <aside className="sidebar">
      <h2>Filtrar Publicaciones</h2>
      
      {/* Calendario */}
      <div className="filter-section">
        <h3>Fecha</h3>
        <Calendar />
      </div>

      {/* Categorías */}
      <div className="filter-section">
        <h3>Categorías</h3>
        <ul>
          <li><input type="checkbox" /> Leyes</li>
          <li><input type="checkbox" /> Decretos</li>
          <li><input type="checkbox" /> Subvenciones</li>
          <li><input type="checkbox" /> Contrataciones</li>
        </ul>
      </div>

      {/* Tags */}
      <div className="filter-section">
        <h3>Tags</h3>
        <ul>
          <li><input type="checkbox" /> Educación</li>
          <li><input type="checkbox" /> Salud</li>
          <li><input type="checkbox" /> Economía</li>
          <li><input type="checkbox" /> Justicia</li>
        </ul>
      </div>

      {/* Departamentos */}
      <div className="filter-section">
        <h3>Departamentos</h3>
        <select>
          <option>Ministerio de Educación</option>
          <option>Ministerio de Salud</option>
          <option>Ministerio de Economía</option>
          <option>Ministerio de Justicia</option>
        </select>
      </div>
    </aside>
  );
};

export default Sidebar;
