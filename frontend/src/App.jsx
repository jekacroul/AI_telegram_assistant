import React from "react";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider } from "./hooks/useTheme.js";
import Layout from "./components/Layout.jsx";

export default function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Layout />
      </BrowserRouter>
    </ThemeProvider>
  );
}
