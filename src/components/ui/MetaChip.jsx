import React from "react";

const MetaChip = ({ children }) => (
  <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-700">
    {children}
  </span>
);

export default MetaChip;
