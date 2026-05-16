import React, { useEffect, useState } from "react";
import { GripVertical, Cpu, Thermometer } from "lucide-react";
import { api } from "../../lib/api.js";
import MemoryCard from "../MemoryCard.jsx";

const DOT = {
  emerald: "bg-emerald-400 dark:bg-emerald-300",
  amber: "bg-amber-400 dark:bg-amber-300 animate-pulse-dot",
  rose: "bg-rose-400 dark:bg-rose-300",
};

function EmptyCard({ label, hint }) {
  return (
    <div className="mem-card">
      <div className="tile-label">{label}</div>
      <div className="text-xl font-bold leading-none mb-1 text-zinc-400 dark:text-slate-500">
        —
      </div>
      <div className="stat-label text-xs">{hint}</div>
    </div>
  );
}

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
  const cpu = res?.cpu;
  const disk = res?.disk;
  const models = res?.models || [];
  const gpuLoad =
    vram && (vram.util_percent != null || vram.temp_c != null);

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

      <div className="grid grid-cols-2 gap-2 flex-shrink-0">
        {vram ? (
          <MemoryCard
            label="VRAM"
            color="amber"
            value={`${vram.used_gb} GB`}
            sub={`из ${vram.total_gb} GB · ${vram.percent}%`}
            percent={vram.percent}
          />
        ) : (
          <EmptyCard label="VRAM" hint="GPU не обнаружен" />
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
          <EmptyCard label="RAM" hint={error ? "нет данных" : "загрузка…"} />
        )}

        {cpu ? (
          <MemoryCard
            label="CPU"
            color="sky"
            value={`${cpu.percent}%`}
            sub={`${cpu.cores} ядер`}
            percent={cpu.percent}
          />
        ) : (
          <EmptyCard label="CPU" hint="—" />
        )}

        {disk ? (
          <MemoryCard
            label="Диск"
            color="violet"
            value={`${disk.used_gb} GB`}
            sub={`из ${disk.total_gb} GB · ${disk.percent}%`}
            percent={disk.percent}
          />
        ) : (
          <EmptyCard label="Диск" hint="—" />
        )}
      </div>

      {gpuLoad && (
        <div className="flex items-center gap-4 mt-3 flex-shrink-0 text-xs">
          {vram.util_percent != null && (
            <span className="flex items-center gap-1.5 text-zinc-500 dark:text-slate-400">
              <Cpu size={13} className="text-amber-500 dark:text-amber-300" />
              Нагрузка GPU:{" "}
              <span className="font-semibold text-zinc-700 dark:text-slate-200">
                {vram.util_percent}%
              </span>
            </span>
          )}
          {vram.temp_c != null && (
            <span className="flex items-center gap-1.5 text-zinc-500 dark:text-slate-400">
              <Thermometer
                size={13}
                className="text-rose-500 dark:text-rose-300"
              />
              <span className="font-semibold text-zinc-700 dark:text-slate-200">
                {vram.temp_c}°C
              </span>
            </span>
          )}
        </div>
      )}

      <div className="mt-3 flex-1 min-h-0 overflow-y-auto">
        {models.map((m, i) => (
          <div
            key={i}
            className="py-2 border-b border-light-border dark:border-dark-border last:border-none"
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <span
                  className={`w-2 h-2 rounded-full flex-shrink-0 ${
                    DOT[m.color] || DOT.rose
                  }`}
                />
                <span className="text-xs font-semibold text-zinc-700 dark:text-slate-200 truncate">
                  {m.name}
                </span>
              </div>
              <span className="text-[11px] text-zinc-400 dark:text-slate-500 flex-shrink-0">
                {m.status}
                {m.memory_gb ? ` · ${m.memory_gb} GB` : ""}
              </span>
            </div>
            {(m.device || m.detail) && (
              <div className="ml-4 mt-0.5 flex items-center gap-1.5 text-[10px] text-zinc-400 dark:text-slate-500 truncate">
                {m.device && (
                  <span
                    className="px-1.5 py-px rounded font-mono
                               bg-light-hover dark:bg-dark-hover
                               text-zinc-500 dark:text-slate-400"
                  >
                    {m.device}
                  </span>
                )}
                {m.detail && <span className="truncate">{m.detail}</span>}
              </div>
            )}
            {m.error && (
              <div
                className="ml-4 mt-0.5 text-[10px] text-rose-500 dark:text-rose-400 truncate"
                title={m.error}
              >
                ⚠ {m.error}
              </div>
            )}
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
