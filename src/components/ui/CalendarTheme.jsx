import React from "react";

const CalendarTheme = () => (
  <style>{`
/* --- BOE Calendar theme (scoped con .boe-calendar) --- */
.boe-calendar {
  --boe-radius: 1rem;
  --boe-border: 1px solid rgb(229 231 235); /* gray-200 */
  --boe-bg: #fff;
  --boe-text: rgb(17 24 39);                /* gray-900 */
  --boe-muted: rgb(107 114 128);            /* gray-500 */
  --boe-hover: rgb(243 244 246);            /* gray-100 */
  --boe-focus: 0 0 0 4px rgba(59, 130, 246, 0.35);  /* blue-500/35 */
  --boe-primary: rgb(29 78 216);            /* blue-700 */
  --boe-primary-weak: rgba(29, 78, 216, .10);
  --boe-primary-weak-2: rgba(29, 78, 216, .14);
}

.boe-calendar.react-calendar {
  background: var(--boe-bg);
  border: var(--boe-border);
  border-radius: var(--boe-radius);
  padding: 0.5rem;
  box-shadow: inset 0 1px 2px rgba(0,0,0,0.02);
  font-size: 0.92rem;
}

.boe-calendar .react-calendar__navigation {
  display: flex;
  gap: .25rem;
  margin-bottom: .25rem;
  align-items: center;
}

.boe-calendar .react-calendar__navigation button {
  border-radius: .5rem;
  padding: .45rem .6rem;
  font-weight: 600;
  color: var(--boe-text);
}

.boe-calendar .react-calendar__navigation__label {
  flex: 1;
  text-align: center;
  color: var(--boe-primary);
  font-weight: 700;
  border-radius: .5rem;
}

.boe-calendar .react-calendar__navigation button:enabled:hover {
  background: var(--boe-hover);
}

.boe-calendar .react-calendar__month-view__weekdays {
  text-transform: capitalize;
  font-weight: 500;
  color: var(--boe-muted);
  padding: .25rem 0;
}

/* Evitar rojos por defecto en fines de semana */
.boe-calendar .react-calendar__month-view__days__day--weekend {
  color: var(--boe-text);
}

.boe-calendar .react-calendar__tile {
  border-radius: .75rem;
  padding: .45rem 0;
}

/* hover discreto */
.boe-calendar .react-calendar__tile:enabled:hover {
  background: var(--boe-hover);
}

/* Hoy: borde azul punteado */
.boe-calendar .react-calendar__tile--now {
  outline: 2px dashed rgba(29,78,216,.35);
  outline-offset: 2px;
}

/* Selección (día activo o extremos del rango) */
.boe-calendar .react-calendar__tile--active,
.boe-calendar .react-calendar__tile--rangeStart,
.boe-calendar .react-calendar__tile--rangeEnd {
  background: var(--boe-primary) !important;
  color: white !important;
}

/* Rango intermedio */
.boe-calendar .react-calendar__tile--range {
  background: var(--boe-primary-weak);
}

.boe-calendar .react-calendar__tile--range:hover {
  background: var(--boe-primary-weak-2);
}

.boe-calendar .react-calendar__tile--hasActive {
  background: var(--boe-primary-weak-2);
}

/* Accesibilidad foco */
.boe-calendar .react-calendar__tile:focus-visible,
.boe-calendar .react-calendar__navigation button:focus-visible {
  outline: none;
  box-shadow: var(--boe-focus);
}
  `}</style>
);

export default CalendarTheme;
