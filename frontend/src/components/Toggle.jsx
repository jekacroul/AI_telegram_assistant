import React from "react";

export default function Toggle({ checked, onChange, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={!!checked}
      disabled={disabled}
      onClick={() => !disabled && onChange?.(!checked)}
      className={`toggle ${checked ? "toggle-on" : "toggle-off"} ${
        disabled ? "opacity-50 cursor-not-allowed" : ""
      }`}
    >
      <span
        className={`toggle-knob ${
          checked ? "translate-x-4" : "translate-x-0"
        }`}
      />
    </button>
  );
}
