const Footer = () => {
  return (
    <footer className="bg-gray-100 border-t py-6 text-center text-sm text-gray-600 mt-20">
      <p>
        &copy; {new Date().getFullYear()} MiProyecto. Todos los derechos reservados.
      </p>
      <div className="mt-2 space-x-4">
        <a href="https://facebook.com" target="_blank" rel="noreferrer" className="hover:underline">Facebook</a>
        <a href="https://twitter.com" target="_blank" rel="noreferrer" className="hover:underline">Twitter</a>
        <a href="https://instagram.com" target="_blank" rel="noreferrer" className="hover:underline">Instagram</a>
      </div>
    </footer>
  );
};

export default Footer;
