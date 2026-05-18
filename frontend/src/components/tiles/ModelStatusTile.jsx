import React, { useEffect, useState } from "react";
import { GripVertical, Cpu, Thermometer } from "lucide-react";
import { api } from "../../lib/api.js";
import MemoryCard from "../MemoryCard.jsx";
import { useLang } from "../../hooks/useLang.js";

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

function StatusRow({ color, name, status, device, detail, memory, error }) {
  return (
    <div className="py-2 border-b border-light-border dark:border-dark-border last:border-none">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={`w-2 h-2 rounded-full flex-shrink-0 ${
              DOT[color] || DOT.rose
            }`}
          />
          <span className="text-xs font-semibold text-zinc-700 dark:text-slate-200 truncate">
            {name}
          </span>
        </div>
        <span className="text-[11px] text-zinc-400 dark:text-slate-500 flex-shrink-0">
          {status}
          {memory ? ` · ${memory} GB` : ""}
        </span>
      </div>
      {(device || detail) && (
        <div className="ml-4 mt-0.5 flex items-center gap-1.5 text-[10px] text-zinc-400 dark:text-slate-500 truncate">
          {device && (
            <span
              className="px-1.5 py-px rounded font-mono
                         bg-light-hover dark:bg-dark-hover
                         text-zinc-500 dark:text-slate-400"
            >
              {device}
            </span>
          )}
          {detail && <span className="truncate">{detail}</span>}
        </div>
      )}
      {error && (
        <div
          className="ml-4 mt-0.5 text-[10px] text-rose-500 dark:text-rose-400 truncate"
          title={error}
        >
          ⚠ {error}
        </div>
      )}
    </div>
  );
}

function GroupLabel({ children }) {
  return (
    <div className="text-[10px] font-semibold uppercase tracking-widest text-zinc-400 dark:text-slate-500 mt-3 mb-1">
      {children}
    </div>
  );
}

// Keeps last-known values across navigations so the tile renders
// instantly on return instead of flashing loading placeholders.
let cachedRes = null;
let cachedStatus = null;

export default function ModelStatusTile({ dragHandleProps }) {
  const { t } = useLang();
  const [res, setRes] = useState(cachedRes);
  const [status, setStatus] = useState(cachedStatus);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const [r, s] = await Promise.allSettled([
        api.systemResources(),
        api.status(),
      ]);
      if (cancelled) return;
      if (r.status === "fulfilled") {
        cachedRes = r.value;
        setRes(r.value);
        setError(false);
      } else {
        setError(true);
      }
      if (s.status === "fulfilled") {
        cachedStatus = s.value;
        setStatus(s.value);
      }
    };
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
  const gpuLoad = vram && (vram.util_percent != null || vram.temp_c != null);

  const services = status
    ? [
        { name: t("tiles.lmStudio"), ok: !!status.llm },
        { name: t("tiles.telegramBot"), ok: !!status.bot },
        { name: t("tiles.database"), ok: !!status.db },
      ]
    : [];

  return (
    <div className="tile flex flex-col">
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <span className="tile-label mb-0">{t("tiles.systemStatus")}</span>
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
            label={t("tiles.vram")}
            color="amber"
            value={`${vram.used_gb} GB`}
            sub={t("tiles.memOf", {
              total: vram.total_gb,
              percent: vram.percent,
            })}
            percent={vram.percent}
          />
        ) : (
          <EmptyCard label={t("tiles.vram")} hint={t("tiles.noGpu")} />
        )}
        {ram ? (
          <MemoryCard
            label={t("tiles.ram")}
            color="emerald"
            value={`${ram.used_gb} GB`}
            sub={t("tiles.memOf", {
              total: ram.total_gb,
              percent: ram.percent,
            })}
            percent={ram.percent}
          />
        ) : (
          <EmptyCard
            label={t("tiles.ram")}
            hint={error ? t("tiles.noDataShort") : t("tiles.loadingShort")}
          />
        )}
        {cpu ? (
          <MemoryCard
            label={t("tiles.cpu")}
            color="sky"
            value={`${cpu.percent}%`}
            sub={t("tiles.cores", { count: cpu.cores })}
            percent={cpu.percent}
          />
        ) : (
          <EmptyCard label={t("tiles.cpu")} hint="—" />
        )}
        {disk ? (
          <MemoryCard
            label={t("tiles.disk")}
            color="violet"
            value={`${disk.used_gb} GB`}
            sub={t("tiles.memOf", {
              total: disk.total_gb,
              percent: disk.percent,
            })}
            percent={disk.percent}
          />
        ) : (
          <EmptyCard label={t("tiles.disk")} hint="—" />
        )}
      </div>

      {gpuLoad && (
        <div className="flex items-center gap-4 mt-3 flex-shrink-0 text-xs">
          {vram.util_percent != null && (
            <span className="flex items-center gap-1.5 text-zinc-500 dark:text-slate-400">
              <Cpu size={13} className="text-amber-500 dark:text-amber-300" />
              {t("tiles.gpuLoad")}
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

      <div className="flex-1 min-h-0 overflow-y-auto">
        <GroupLabel>{t("tiles.services")}</GroupLabel>
        {services.length === 0 && (
          <div className="stat-label text-xs py-2">
            {t("tiles.loadingShort")}
          </div>
        )}
        {services.map((s) => (
          <StatusRow
            key={s.name}
            name={s.name}
            color={s.ok ? "emerald" : "rose"}
            status={s.ok ? t("tiles.online") : t("tiles.offline")}
          />
        ))}

        <GroupLabel>{t("tiles.models")}</GroupLabel>
        {models.length === 0 && (
          <div className="stat-label text-xs py-2">
            {error ? t("tiles.statusFailed") : t("tiles.loadingShort")}
          </div>
        )}
        {models.map((m, i) => (
          <StatusRow
            key={i}
            name={m.name}
            color={m.color}
            status={m.status}
            device={m.device}
            detail={m.detail}
            memory={m.memory_gb}
            error={m.error}
          />
        ))}
      </div>
    </div>
  );
}
