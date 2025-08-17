import React from "react";
import "../../styles/components/components.css";

const Typography = ({ variant = "p", children, style, className = "" }) => {
  const Tag = variant;

  return (
    <Tag className={`typography ${className}`} style={style}>
      {children}
    </Tag>
  );
};

export default Typography;
