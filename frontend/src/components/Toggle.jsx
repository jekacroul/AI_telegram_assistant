import React from "react";

export default function Toggle({ checked, onChange, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={!!checked}
      disabled={disabled}
      onClick={() => !disabled && onChange?.(!checked)}
      className={`relative w-9 h-5 rounded-full transition-colors duration-200 flex-shrink-0
        disabled:opacity-40 disabled:cursor-not-allowed
        ${checked ? "bg-accent" : "bg-line"}`}
    >
      <span
        className={`absolute top-0.5 w-4 h-4 rounded-full bg-panel shadow-sm
          transition-transform duration-200
          ${checked ? "translate-x-4" : "translate-x-0.5"}`}
      />
    </button>
  );
}
