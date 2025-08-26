import React from "react";

const Caret = ({ open }) => (
  <svg
    className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`}
    viewBox="0 0 20 20"
    fill="currentColor"
    aria-hidden
  >
    <path d="M5.23 7.21a.75.75 0 011.06.02L10 11.178l3.71-3.947a.75.75 0 111.08 1.04l-4.24 4.512a.75.75 0 01-1.08 0L5.25 8.27a.75.75 0 01-.02-1.06z" />
  </svg>
);

const Section = ({ title, children, defaultOpen = true }) => {
  const [open, setOpen] = React.useState(defaultOpen);
  const id = React.useId();
  return (
    <section className="bg-white rounded-2xl shadow-sm border border-gray-100">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-6 py-4 text-left"
        aria-expanded={open}
        aria-controls={`${id}-panel`}
      >
        <h2 className="text-base font-semibold">{title}</h2>
        <Caret open={open} />
      </button>
      {open && <div id={`${id}-panel`} className="px-6 pb-6">{children}</div>}
    </section>
  );
};

export default Section;
