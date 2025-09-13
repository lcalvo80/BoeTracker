import React from "react";

const CalendarTheme = () => (
  <style>{`
/* --- BOE Calendar theme (scoped con .boe-calendar) --- */
.boe-calendar {
  --boe-radius: 1rem;
  --boe-border: 1px solid rgb(229 231 235); /* gray-200 */
  --boe-bg: #fff;
  --boe-hover: rgb(243 244 246);            /* gray-100 */
  --boe-active: rgb(31 41 55);              /* gray-800 */
  --boe-text: rgb(17 24 39);                /* gray-900 */
  --boe-muted: rgb(107 114 128);            /* gray-500 */
  --boe-focus: 0 0 0 4px rgba(156, 163, 175, 0.35); /* gray-400/35 */
}

.boe-calendar.react-calendar {
  background: var(--boe-bg);
  border: var(--boe-border);
  border-radius: var(--boe-radius);
  padding: 0.5rem;
  box-shadow: inset 0 1px 2px rgba(0,0,0,0.02);
  font-size: 0.9rem;
}

.boe-calendar .react-calendar__navigation {
  display: flex;
  gap: .25rem;
  margin-bottom: .25rem;
}

.boe-calendar .react-calendar__navigation button {
  border-radius: .5rem;
  padding: .5rem .6rem;
  font-weight: 600;
  color: var(--boe-text);
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

.boe-calendar .react-calendar__tile {
  border-radius: .75rem;
  padding: .4rem 0;
}

.boe-calendar .react-calendar__tile:enabled:hover {
  background: var(--boe-hover);
}

.boe-calendar .react-calendar__tile--now {
  outline: 2px dashed rgba(31,41,55,.25);
  outline-offset: 2px;
}

.boe-calendar .react-calendar__tile--active,
.boe-calendar .react-calendar__tile--rangeStart,
.boe-calendar .react-calendar__tile--rangeEnd {
  background: var(--boe-active) !important;
  color: white !important;
}

.boe-calendar .react-calendar__tile--range {
  background: rgba(31, 41, 55, .08);
}

.boe-calendar .react-calendar__tile--range:hover {
  background: rgba(31, 41, 55, .12);
}

.boe-calendar .react-calendar__tile--hasActive {
  background: rgba(31, 41, 55, .12);
}

/* Asegurar accesibilidad foco */
.boe-calendar .react-calendar__tile:focus-visible,
.boe-calendar .react-calendar__navigation button:focus-visible {
  outline: none;
  box-shadow: var(--boe-focus);
}
  `}</style>
);

export default CalendarTheme;
