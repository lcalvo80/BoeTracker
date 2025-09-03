import React from "react";
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import Home from "./components/ui/Home";
import AboutUsPage from "./pages/AboutUsPage";
import ContactPage from "./pages/ContactPage";
import BOEPage from "./pages/BOEPage";
import BOEDetailPage from "./pages/BOEDetailPage";
import PricingPage from "./pages/PricingPage";
import Navbar from "./components/Navbar"; // o "./components/layout/Navbar" según tu estructura

const App = () => {
  return (
    <Router>
      <Navbar />
      <div className="main-content">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/about" element={<AboutUsPage />} />
          <Route path="/contact" element={<ContactPage />} />
          <Route path="/boe" element={<BOEPage />} />
          <Route path="/item/:id" element={<BOEDetailPage />} />
+         <Route path="/pricing" element={<PricingPage />} />
        </Routes>
      </div>
    </Router>
  );
};

export default App;
