import React, { useCallback, useEffect, useState } from "react";
import { GripVertical, Play, RotateCw, Square } from "lucide-react";
import { api } from "../../lib/api.js";
import Toggle from "../Toggle.jsx";

export default function LlamaServerTile({ dragHandleProps }) {
  const [srv, setSrv] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setSrv(await api.llamaServerStatus());
    } catch {
      // ignore — keep last known state
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [load]);

  async function act(fn) {
    setError("");
    try {
      const res = await fn();
      if (res && res.started === false) {
        setError(res.reason || "не удалось запустить");
      }
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function toggleAutoResume(v) {
    setSrv((s) => (s ? { ...s, auto_resume: v } : s));
    try {
      await api.llamaServerSetAutoResume(v);
    } catch (e) {
      setError(e.message);
      setSrv((s) => (s ? { ...s, auto_resume: !v } : s));
    }
  }

  const running = !!srv?.running;
  const starting = !!srv?.starting;
  const stopping = !!srv?.stopping;
  const busy = starting || stopping;

  let dot = "bg-slate-400 dark:bg-slate-600";
  let stateText = "остановлен";
  if (starting) {
    dot = "bg-amber-400 dark:bg-amber-300 animate-pulse-dot";
    stateText = "запуск…";
  } else if (stopping) {
    dot = "bg-amber-400 dark:bg-amber-300 animate-pulse-dot";
    stateText = "останавливается…";
  } else if (running) {
    dot = "bg-emerald-400 dark:bg-emerald-300";
    stateText = `работает на :${srv.port}`;
  }

  const loraName = srv?.lora_path
    ? srv.lora_path.split(/[\\/]/).pop()
    : null;

  return (
    <div className="tile flex flex-col">
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <span className="tile-label mb-0">Llama Server</span>
        <span
          {...dragHandleProps}
          className="cursor-grab active:cursor-grabbing text-zinc-300
                     dark:text-slate-600 hover:text-zinc-500
                     dark:hover:text-slate-400 touch-none"
        >
          <GripVertical size={16} />
        </span>
      </div>

      <div className="flex items-center gap-2 mb-2">
        <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${dot}`} />
        <span className="text-sm font-semibold text-zinc-800 dark:text-slate-200">
          {srv ? stateText : "загрузка…"}
        </span>
      </div>

      <div className="text-xs text-zinc-400 dark:text-slate-500 mb-3">
        {running ? (
          loraName ? (
            <>
              LoRA:{" "}
              <span className="font-mono text-zinc-500 dark:text-slate-400">
                {loraName}
              </span>
            </>
          ) : (
            "без адаптера (чистая база)"
          )
        ) : (
          "адаптер модели для инференса"
        )}
      </div>

      <div className="flex flex-wrap gap-2 mb-3">
        {!running && !starting && (
          <button
            className="btn-primary"
            onClick={() => act(api.llamaServerStart)}
            disabled={busy || !srv?.configured}
          >
            <Play size={14} /> Запустить
          </button>
        )}
        {running && (
          <button
            className="btn-ghost"
            onClick={() => act(api.llamaServerRestart)}
            disabled={busy}
          >
            <RotateCw size={14} /> Перезапустить
          </button>
        )}
        {running && (
          <button
            className="btn-danger"
            onClick={() => act(api.llamaServerStop)}
            disabled={busy}
          >
            <Square size={13} /> Остановить
          </button>
        )}
      </div>

      <label className="flex items-center gap-2 cursor-pointer mt-auto">
        <Toggle
          checked={!!srv?.auto_resume}
          onChange={toggleAutoResume}
        />
        <span className="text-xs text-zinc-500 dark:text-slate-400">
          Авто-подъём после обучения и Merge → GGUF
        </span>
      </label>

      {srv && !srv.configured && (
        <div className="text-[11px] text-rose-500 dark:text-rose-400 mt-2">
          Не задан LLAMA_BASE_MODEL_GGUF в .env — запуск невозможен.
        </div>
      )}
      {(error || (srv?.last_error && !running)) && (
        <div
          className="text-[11px] text-rose-500 dark:text-rose-400 mt-2 truncate"
          title={error || srv?.last_error}
        >
          ⚠ {error || srv?.last_error}
        </div>
      )}
    </div>
  );
}
