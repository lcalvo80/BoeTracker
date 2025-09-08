// src/components/ui/Select.js
const Select = ({ children, className = "", ...props }) => (
  <select
    {...props}
    className={`w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 ${className}`}
  >
    {children}
  </select>
);
export const SelectItem = ({ value, children }) => <option value={value}>{children}</option>;
