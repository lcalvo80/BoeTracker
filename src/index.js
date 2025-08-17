import React from "react";
import ReactDOM from "react-dom/client";
import "./styles/base/variables.css";
import "./styles/base/global.css";
import RootApp from "./App";
import { ClerkProvider } from '@clerk/clerk-react';
import './index.css';  // Ensure Tailwind is included


const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <RootApp />
  </React.StrictMode>
);

const pk = process.env.REACT_APP_CLERK_PUBLISHABLE_KEY;

root.render(
  <React.StrictMode>
    <ClerkProvider publishableKey={pk}>
      <App />
    </ClerkProvider>
  </React.StrictMode>
);