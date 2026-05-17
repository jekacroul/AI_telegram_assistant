import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useState,
} from "react";
import { translations, LANGUAGES } from "../lib/i18n.js";

const LangContext = createContext(null);

function getInitial() {
  try {
    const stored = localStorage.getItem("lang");
    if (LANGUAGES.includes(stored)) return stored;
    const nav = (navigator.language || "").toLowerCase();
    return nav.startsWith("ru") ? "ru" : "en";
  } catch {
    return "ru";
  }
}

function resolve(dict, key) {
  return key
    .split(".")
    .reduce((acc, part) => (acc == null ? acc : acc[part]), dict);
}

function interpolate(str, vars) {
  if (!vars) return str;
  return str.replace(/\{(\w+)\}/g, (m, k) =>
    k in vars ? String(vars[k]) : m
  );
}

export function LanguageProvider({ children }) {
  const [lang, setLang] = useState(getInitial);

  useEffect(() => {
    document.documentElement.setAttribute("lang", lang);
    try {
      localStorage.setItem("lang", lang);
    } catch {
      // ignore
    }
  }, [lang]);

  const t = (key, vars) => {
    let val = resolve(translations[lang], key);
    if (val == null) val = resolve(translations.ru, key);
    if (val == null) return key;
    return typeof val === "string" ? interpolate(val, vars) : val;
  };

  const toggle = () => setLang((l) => (l === "ru" ? "en" : "ru"));

  return createElement(
    LangContext.Provider,
    { value: { lang, setLang, toggle, t } },
    children
  );
}

export function useLang() {
  const ctx = useContext(LangContext);
  if (!ctx) {
    return { lang: "ru", setLang: () => {}, toggle: () => {}, t: (k) => k };
  }
  return ctx;
}
