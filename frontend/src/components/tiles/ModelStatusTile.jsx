import React, { useEffect, useState } from "react";
import { GripVertical } from "lucide-react";
import { api } from "../../lib/api.js";
import MemoryCard from "../MemoryCard.jsx";

const DOT = {
  emerald: "bg-emerald-400 dark:bg-emerald-300",
  amber: "bg-amber-400 dark:bg-amber-300 animate-pulse-dot",
  rose: "bg-rose-400 dark:bg-rose-300",
};

export default function ModelStatusTile({ dragHandleProps }) {
  const [res, setRes] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .systemResources()
        .then((r) => !cancelled && (setRes(r), setError(false)))
        .catch(() => !cancelled && setError(true));
    load();
    const id = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const vram = res?.vram;
  const ram = res?.ram;
  const models = res?.models || [];

  return (
    <div className="tile flex flex-col">
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <span className="tile-label mb-0">Статус моделей</span>
        <span
          {...dragHandleProps}
          className="cursor-grab active:cursor-grabbing text-zinc-300
                     dark:text-slate-600 hover:text-zinc-500
                     dark:hover:text-slate-400 touch-none"
        >
          <GripVertical size={16} />
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 flex-shrink-0">
        {vram ? (
          <MemoryCard
            label="VRAM"
            color="amber"
            value={`${vram.used_gb} GB`}
            sub={`из ${vram.total_gb} GB · ${vram.percent}%`}
            percent={vram.percent}
          />
        ) : (
          <div className="mem-card">
            <div className="tile-label">VRAM</div>
            <div className="text-xl font-bold leading-none mb-1 text-zinc-400 dark:text-slate-500">
              —
            </div>
            <div className="stat-label text-xs">GPU не обнаружен</div>
          </div>
        )}

        {ram ? (
          <MemoryCard
            label="RAM"
            color="emerald"
            value={`${ram.used_gb} GB`}
            sub={`из ${ram.total_gb} GB · ${ram.percent}%`}
            percent={ram.percent}
          />
        ) : (
          <div className="mem-card">
            <div className="tile-label">RAM</div>
            <div className="text-xl font-bold leading-none mb-1 text-zinc-400 dark:text-slate-500">
              —
            </div>
            <div className="stat-label text-xs">
              {error ? "нет данных" : "загрузка…"}
            </div>
          </div>
        )}
      </div>

      <div className="mt-3 space-y-1 flex-1 min-h-0 overflow-y-auto">
        {models.map((m, i) => (
          <div key={i} className="stat-row">
            <div className="flex items-center gap-2 min-w-0">
              <span
                className={`w-2 h-2 rounded-full flex-shrink-0 ${
                  DOT[m.color] || DOT.rose
                }`}
              />
              <span className="stat-value truncate">{m.name}</span>
            </div>
            <span className="stat-label flex-shrink-0">
              {m.status}
              {m.memory_gb ? ` · ${m.memory_gb} GB` : ""}
            </span>
          </div>
        ))}
        {models.length === 0 && (
          <div className="stat-label text-xs py-2">
            {error ? "Не удалось получить статус" : "Загрузка…"}
          </div>
        )}
      </div>
    </div>
  );
}
