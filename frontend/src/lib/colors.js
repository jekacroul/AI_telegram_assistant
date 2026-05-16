export const colorMap = {
  sky: { dark: "#38bdf8", light: "#0284c7" },
  emerald: { dark: "#34d399", light: "#059669" },
  violet: { dark: "#a78bfa", light: "#7c3aed" },
  indigo: { dark: "#818cf8", light: "#4f46e5" },
  amber: { dark: "#fbbf24", light: "#d97706" },
  rose: { dark: "#fb7185", light: "#e11d48" },
};

export function pickColor(name, isDark) {
  const c = colorMap[name] || colorMap.indigo;
  return c[isDark ? "dark" : "light"];
}

export function chartColors(isDark) {
  return {
    primary: isDark ? "#818cf8" : "#4f46e5",
    secondary: isDark ? "#34d399" : "#059669",
    grid: isDark ? "#2a3347" : "#dde3ef",
    axisText: isDark ? "#64748b" : "#94a3b8",
    tooltipBg: isDark ? "#161b27" : "#ffffff",
    tooltipBorder: isDark ? "#2a3347" : "#dde3ef",
    tooltipText: isDark ? "#f1f5f9" : "#0f172a",
  };
}
