import React from "react";

export default function Page({ title, actions, className = "space-y-4", children }) {
  return (
    <div className={className}>
      {(title || actions) && (
        <div className="flex items-center justify-between gap-3">
          {title ? (
            <p className="text-xs text-zinc-400 dark:text-slate-500">{title}</p>
          ) : (
            <span />
          )}
          {actions && (
            <div className="flex items-center gap-2 flex-wrap justify-end">
              {actions}
            </div>
          )}
        </div>
      )}
      {children}
    </div>
  );
}
