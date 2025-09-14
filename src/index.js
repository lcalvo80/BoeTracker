import React from "react";
import ReactDOM from "react-dom/client";
import { ClerkProvider } from "@clerk/clerk-react";
import App from "./App";
import "./styles/base/variables.css";
import "./styles/base/global.css";
import "./index.css"; // Tailwind incluido

// Publishable Key de Clerk (desde .env - CRA)
const PUBLISHABLE_KEY = process.env.REACT_APP_CLERK_PUBLISHABLE_KEY;

if (!PUBLISHABLE_KEY) {
  console.warn("⚠️ Falta REACT_APP_CLERK_PUBLISHABLE_KEY en tu .env");
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <ClerkProvider publishableKey={PUBLISHABLE_KEY}>
      <App />
    </ClerkProvider>
  </React.StrictMode>
);
