import React from "react";

const VARIANTS = {
  green: "badge-green",
  red: "badge-red",
  amber: "badge-amber",
  zinc: "badge-zinc",
};

export default function Badge({ variant = "zinc", children, className = "" }) {
  return (
    <span className={`badge ${VARIANTS[variant] || VARIANTS.zinc} ${className}`}>
      {children}
    </span>
  );
}
