const features = [
  {
    icon: "⚡",
    title: "Rendimiento",
    desc: "Carga rápida, navegación fluida y una estructura optimizada."
  },
  {
    icon: "🎯",
    title: "Enfoque UX",
    desc: "Diseño centrado en el usuario, simple e intuitivo."
  },
  {
    icon: "🛠️",
    title: "Tecnología Moderna",
    desc: "Construido con React, Tailwind, y buenas prácticas desde el inicio."
  }
];

const FeatureSection = () => (
  <div className="grid md:grid-cols-3 gap-12 text-center">
    {features.map((f, idx) => (
      <div
        key={idx}
        className="flex flex-col items-center hover:scale-105 transition-transform duration-300"
      >
        <div className="text-4xl mb-4">{f.icon}</div>
        <h3 className="text-xl font-semibold mb-2">{f.title}</h3>
        <p className="text-gray-600 text-sm max-w-xs">{f.desc}</p>
      </div>
    ))}
  </div>
);
export default FeatureSection;
