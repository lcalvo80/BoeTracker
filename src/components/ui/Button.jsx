import React from "react";
import "../../styles/components/components.css";


const Button = ({ variant = "primary", children, ...props }) => {
  return (
    <button className={`button button-${variant}`} {...props}>
      {children}
    </button>
  );
};

export default Button;
