import React from "react";
import { Moon, Sun } from "lucide-react";

export default function ThemeToggle({ theme, onToggle, collapsed }) {
  const isDark = theme === "dark";
  return (
    <button
      className="nav-item w-full"
      onClick={onToggle}
      title={isDark ? "Светлая тема" : "Тёмная тема"}
    >
      {isDark ? <Sun size={16} /> : <Moon size={16} />}
      {!collapsed && <span>{isDark ? "Светлая тема" : "Тёмная тема"}</span>}
    </button>
  );
}
