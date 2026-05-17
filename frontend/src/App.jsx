import React from "react";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider } from "./hooks/useTheme.js";
import { LanguageProvider } from "./hooks/useLang.js";
import Layout from "./components/Layout.jsx";

export default function App() {
  return (
    <ThemeProvider>
      <LanguageProvider>
        <BrowserRouter>
          <Layout />
        </BrowserRouter>
      </LanguageProvider>
    </ThemeProvider>
  );
}
