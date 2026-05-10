export function Card({ title, children, action }) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      {(title || action) && (
        <header className="mb-3 flex items-center justify-between">
          {title && <h2 className="text-sm font-semibold text-slate-200">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Button({ children, className = "", variant = "primary", ...props }) {
  const styles = {
    primary: "bg-brand-600 hover:bg-brand-500 text-white",
    ghost: "bg-transparent hover:bg-slate-800 text-slate-200 border border-slate-700",
    danger: "bg-rose-600 hover:bg-rose-500 text-white",
    success: "bg-emerald-600 hover:bg-emerald-500 text-white",
  };
  return (
    <button
      className={`rounded px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function Input({ className = "", ...props }) {
  return (
    <input
      className={`w-full rounded border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-100 placeholder-slate-500 focus:border-brand-500 focus:outline-none ${className}`}
      {...props}
    />
  );
}

export function Textarea({ className = "", ...props }) {
  return (
    <textarea
      className={`w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-brand-500 focus:outline-none ${className}`}
      {...props}
    />
  );
}
