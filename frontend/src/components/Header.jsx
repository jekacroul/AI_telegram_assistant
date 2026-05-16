import React from "react";

export default function Header({ title, subtitle, actions }) {
  return (
    <header
      className="flex items-center justify-between gap-4 px-6 h-[56px] flex-shrink-0
                 bg-light-card dark:bg-dark-card
                 border-b border-light-border dark:border-dark-border"
    >
      <div className="min-w-0">
        <h1 className="text-base font-bold text-zinc-900 dark:text-slate-100 truncate">
          {title}
        </h1>
        {subtitle && (
          <p className="text-xs text-zinc-400 dark:text-slate-500 truncate">
            {subtitle}
          </p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}
