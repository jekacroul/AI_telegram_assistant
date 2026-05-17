import React from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "../hooks/useTheme.js";
import { useLang } from "../hooks/useLang.js";

export default function ThemeToggle() {
  const { isDark, toggle } = useTheme();
  const { t } = useLang();
  return (
    <button
      className="nav-item"
      onClick={toggle}
      title={isDark ? t("theme.light") : t("theme.dark")}
      aria-label={t("theme.toggle")}
    >
      {isDark ? <Sun size={17} /> : <Moon size={17} />}
    </button>
  );
}
