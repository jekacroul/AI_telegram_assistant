import React from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "../hooks/useTheme.js";

export default function ThemeToggle() {
  const { isDark, toggle } = useTheme();
  return (
    <button
      className="nav-item"
      onClick={toggle}
      title={isDark ? "Светлая тема" : "Тёмная тема"}
      aria-label="Переключить тему"
    >
      {isDark ? <Sun size={17} /> : <Moon size={17} />}
    </button>
  );
}
