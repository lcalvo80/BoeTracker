import React from "react";
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import Navbar from "./components/layout/Navbar";
import Home from "./pages/Home";
import BOEPage from "./pages/BOEPage";
import BOEDetailPage from "./pages/BOEDetailPage";
import ContactPage from "./pages/ContactPage";

const App = () => {
  return (
    <Router>
      <Navbar />
      <main className="main-content p-4">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/boe" element={<BOEPage />} />
          <Route path="/item/:id" element={<BOEDetailPage />} />
          <Route path="/contact" element={<ContactPage />} />
        </Routes>
      </main>
    </Router>
  );
};

export default App;
