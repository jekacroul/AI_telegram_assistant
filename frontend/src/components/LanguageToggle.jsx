import React from "react";
import { useLang } from "../hooks/useLang.js";

export default function LanguageToggle() {
  const { lang, toggle, t } = useLang();
  return (
    <button
      className="nav-item text-[11px] font-bold tracking-wide"
      onClick={toggle}
      title={t("language.toggle")}
      aria-label={t("language.toggle")}
    >
      {lang === "ru" ? "RU" : "EN"}
    </button>
  );
}
