const Select = ({ children, ...props }) => (
    <select
      {...props}
      className="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400"
    >
      {children}
    </select>
  );
  
  const SelectItem = ({ value, children }) => <option value={value}>{children}</option>;
  
  export { Select, SelectItem };
  