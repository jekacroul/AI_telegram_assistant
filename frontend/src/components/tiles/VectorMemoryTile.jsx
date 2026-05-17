import React, { useCallback, useEffect, useState } from "react";
import { GripVertical } from "lucide-react";
import { api } from "../../lib/api.js";
import { useLang } from "../../hooks/useLang.js";

function formatEta(seconds) {
  if (!seconds || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

export default function VectorMemoryTile({ dragHandleProps }) {
  const { t } = useLang();
  const [ragStatus, setRagStatus] = useState(null);
  const [error, setError] = useState("");
  const [ragSearch, setRagSearch] = useState({
    open: false,
    query: "",
    results: null,
    busy: false,
    error: "",
  });

  const load = useCallback(async () => {
    try {
      setRagStatus(await api.ragStatus());
    } catch {
      // ignore — keep last known state
    }
  }, []);

  useEffect(() => {
    load();
    const ragEv = new EventSource("/api/rag/index-progress");
    let ragWasRunning = false;
    ragEv.onmessage = (e) => {
      try {
        const p = JSON.parse(e.data);
        if (!p || typeof p.running === "undefined") return;
        setRagStatus((s) => (s ? { ...s, indexing: p } : s));
        if (ragWasRunning && !p.running) {
          api.ragStatus().then(setRagStatus).catch(() => {});
        }
        ragWasRunning = p.running;
      } catch {}
    };
    return () => ragEv.close();
  }, [load]);

  async function reindexRag() {
    setError("");
    try {
      const res = await api.ragIndexAll();
      if (!res.started) {
        setError(res.reason || t("training.failedIndex"));
        return;
      }
      setRagStatus((s) =>
        s
          ? {
              ...s,
              indexing: {
                running: true,
                indexed: 0,
                total: s.total_indexed || 0,
                percent: 0,
                eta_seconds: 0,
              },
            }
          : s,
      );
    } catch (e) {
      setError(e.message);
    }
  }

  async function runRagSearch() {
    if (!ragSearch.query.trim()) return;
    setRagSearch((s) => ({ ...s, busy: true, error: "", results: null }));
    try {
      const res = await api.ragSearch(ragSearch.query.trim(), null, 5);
      setRagSearch((s) => ({
        ...s,
        busy: false,
        results: res.results || [],
      }));
    } catch (e) {
      setRagSearch((s) => ({ ...s, busy: false, error: e.message }));
    }
  }

  return (
    <div className="tile flex flex-col">
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <span className="tile-label mb-0">{t("training.vectorMemory")}</span>
        <span
          {...dragHandleProps}
          className="cursor-grab active:cursor-grabbing text-zinc-300
                     dark:text-slate-600 hover:text-zinc-500
                     dark:hover:text-slate-400 touch-none"
        >
          <GripVertical size={16} />
        </span>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto pr-1">
        {!ragStatus ? (
          <div className="text-xs text-zinc-400 dark:text-slate-500 py-8 text-center">
            {t("tiles.loadingShort")}
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`text-sm font-semibold ${
                  ragStatus.enabled
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-zinc-400 dark:text-slate-500"
                }`}
              >
                {ragStatus.enabled
                  ? t("training.ragActive")
                  : t("training.ragDisabled")}
              </span>
              {ragStatus.available && (
                <span className="text-xs text-zinc-400 dark:text-slate-500">
                  {ragStatus.model} · {ragStatus.device}
                </span>
              )}
            </div>

            {ragStatus.last_error && !ragStatus.available && (
              <div className="text-rose-500 dark:text-rose-400 text-xs mt-2">
                {t("training.errorPrefix", { msg: ragStatus.last_error })}
              </div>
            )}

            <div className="grid grid-cols-3 gap-3 mt-3 text-sm">
              <div>
                <div className="text-zinc-400 dark:text-slate-500 text-xs">
                  {t("training.indexed")}
                </div>
                <div className="text-lg font-semibold text-zinc-900 dark:text-slate-100">
                  {(ragStatus.total_indexed ?? 0).toLocaleString()}
                </div>
              </div>
              <div>
                <div className="text-zinc-400 dark:text-slate-500 text-xs">
                  {t("training.size")}
                </div>
                <div className="text-lg font-semibold text-zinc-900 dark:text-slate-100">
                  {ragStatus.collection_size_mb ?? 0} MB
                </div>
              </div>
              <div>
                <div className="text-zinc-400 dark:text-slate-500 text-xs">
                  {t("training.lastIndexing")}
                </div>
                <div className="text-sm font-semibold text-zinc-900 dark:text-slate-100">
                  {ragStatus.last_indexed_at
                    ? new Date(ragStatus.last_indexed_at).toLocaleString()
                    : "—"}
                </div>
              </div>
            </div>

            {ragStatus.indexing?.running && (
              <div className="mt-3">
                <div className="flex flex-wrap justify-between gap-2 text-xs text-zinc-400 dark:text-slate-500">
                  <span>
                    {t("training.indexingMessages", {
                      indexed: ragStatus.indexing.indexed,
                      total: ragStatus.indexing.total || "—",
                    })}
                  </span>
                  <span>
                    {ragStatus.indexing.eta_seconds > 0 && (
                      <span className="mr-3">
                        ETA: {formatEta(ragStatus.indexing.eta_seconds)}
                      </span>
                    )}
                    {ragStatus.indexing.percent}%
                  </span>
                </div>
                {ragStatus.indexing.total > 0 ? (
                  <div className="mt-1 progress-bar">
                    <div
                      className="progress-fill bg-indigo-500 dark:bg-indigo-400"
                      style={{ width: `${ragStatus.indexing.percent}%` }}
                    />
                  </div>
                ) : (
                  <div className="mt-1 progress-indeterminate" />
                )}
              </div>
            )}

            <div className="flex flex-wrap gap-2 mt-4">
              <button
                className="btn-secondary"
                onClick={reindexRag}
                disabled={!ragStatus.available || ragStatus.indexing?.running}
              >
                {t("training.reindexAll")}
              </button>
              <button
                className="btn-secondary"
                onClick={() => setRagSearch((s) => ({ ...s, open: !s.open }))}
                disabled={!ragStatus.available}
              >
                {ragSearch.open
                  ? t("training.hideSearchTest")
                  : t("training.searchTest")}
              </button>
            </div>

            {error && (
              <div className="text-rose-500 dark:text-rose-400 text-xs mt-2">
                {error}
              </div>
            )}

            {ragSearch.open && (
              <div
                className="mt-4 rounded-lg border border-light-border
                           dark:border-dark-border
                           bg-light-card2 dark:bg-dark-card2 p-3"
              >
                <div className="flex gap-2">
                  <input
                    className="input flex-1"
                    placeholder={t("training.searchPlaceholder")}
                    value={ragSearch.query}
                    onChange={(e) =>
                      setRagSearch((s) => ({ ...s, query: e.target.value }))
                    }
                    onKeyDown={(e) => {
                      if (e.key === "Enter") runRagSearch();
                    }}
                  />
                  <button
                    className="btn-primary"
                    onClick={runRagSearch}
                    disabled={ragSearch.busy}
                  >
                    {ragSearch.busy ? "..." : t("training.find")}
                  </button>
                </div>
                {ragSearch.error && (
                  <div className="text-rose-500 dark:text-rose-400 text-sm mt-2">
                    {ragSearch.error}
                  </div>
                )}
                {ragSearch.results && ragSearch.results.length === 0 && (
                  <div className="text-zinc-400 dark:text-slate-500 text-sm mt-2">
                    {t("training.nothingFound")}
                  </div>
                )}
                {ragSearch.results && ragSearch.results.length > 0 && (
                  <div className="mt-3 space-y-2">
                    {ragSearch.results.map((r, i) => (
                      <div
                        key={i}
                        className="rounded-lg border border-light-border
                                   dark:border-dark-border p-2 text-sm"
                      >
                        <div className="flex justify-between gap-2 text-xs text-zinc-400 dark:text-slate-500">
                          <span>
                            {r.sender_name || r.chat_name || "—"}
                            {r.chat_name && r.sender_name
                              ? ` · ${r.chat_name}`
                              : ""}
                          </span>
                          <span className="badge badge-zinc font-mono">
                            {r.similarity_score?.toFixed?.(2) ?? "—"}
                          </span>
                        </div>
                        <div className="mt-1 text-zinc-700 dark:text-slate-200">
                          {r.text}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
