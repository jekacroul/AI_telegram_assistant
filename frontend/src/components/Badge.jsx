import React from "react";

const VARIANTS = {
  green:
    "bg-emerald-100 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300",
  amber:
    "bg-amber-100 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
  rose: "bg-rose-100 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300",
  sky: "bg-sky-100 dark:bg-sky-950/40 text-sky-700 dark:text-sky-300",
  violet:
    "bg-violet-100 dark:bg-violet-950/40 text-violet-700 dark:text-violet-300",
  zinc: "bg-light-card2 dark:bg-dark-card2 text-zinc-600 dark:text-slate-400",
};

export default function Badge({ variant = "zinc", children, className = "" }) {
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 text-[10px]
        font-medium rounded-full ${VARIANTS[variant] || VARIANTS.zinc} ${className}`}
    >
      {children}
    </span>
  );
}
