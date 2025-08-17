import React from "react";

const Card = ({ children }) => {
  return (
    <div style={{ padding: "var(--spacing-4)", border: "1px solid var(--color-tertiary)", borderRadius: "8px" }}>
      {children}
    </div>
  );
};

export default Card;
