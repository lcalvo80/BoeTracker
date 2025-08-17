import React, { useState } from "react";
import { FaChevronUp, FaChevronDown } from "react-icons/fa";

const SubSectionToggle = ({ label, content, isList = false, color = "border-gray-300" }) => {
  const [open, setOpen] = useState(true);

  return (
    <div className={`mb-4 border-l-4 pl-3 ${color}`}>
      <div
        className="flex justify-between items-center cursor-pointer mb-1"
        onClick={() => setOpen(!open)}
      >
        <h3 className="text-sm font-bold text-gray-800">{label}</h3>
        {open ? <FaChevronUp className="text-xs" /> : <FaChevronDown className="text-xs" />}
      </div>
      {open && (
        <div className="text-sm text-gray-700">
          {isList ? (
            <ul className="list-disc pl-4 space-y-1">
              {content.map((item, idx) => <li key={idx}>{item}</li>)}
            </ul>
          ) : (
            <p>{content}</p>
          )}
        </div>
      )}
    </div>
  );
};

export default SubSectionToggle;
