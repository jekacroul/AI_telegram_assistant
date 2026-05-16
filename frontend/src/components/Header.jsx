import React from "react";

export default function Header({ title, subtitle }) {
  return (
    <header
      className="flex items-center justify-between px-6 h-[52px] flex-shrink-0
                 border-b border-line bg-bg sticky top-0 z-10"
    >
      <div className="min-w-0">
        <h1 className="text-base font-semibold text-fg truncate">{title}</h1>
        {subtitle && (
          <p className="text-xs text-muted truncate">{subtitle}</p>
        )}
      </div>
    </header>
  );
}
