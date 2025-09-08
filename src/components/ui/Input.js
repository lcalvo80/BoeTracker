// src/components/ui/Input.js
const Input = ({ className = "", ...props }) => (
  <input
    {...props}
    className={`w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 ${className}`}
  />
);
export default Input;
