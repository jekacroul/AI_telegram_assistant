import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useState,
} from "react";

const ThemeContext = createContext(null);

function getInitial() {
  try {
    const stored = localStorage.getItem("theme");
    if (stored) return stored;
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  } catch {
    return "dark";
  }
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(getInitial);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    try {
      localStorage.setItem("theme", theme);
    } catch {
      // ignore
    }
  }, [theme]);

  const toggle = () => setTheme((t) => (t === "dark" ? "light" : "dark"));

  return createElement(
    ThemeContext.Provider,
    { value: { theme, toggle, isDark: theme === "dark" } },
    children
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) return { theme: "dark", toggle: () => {}, isDark: true };
  return ctx;
}
