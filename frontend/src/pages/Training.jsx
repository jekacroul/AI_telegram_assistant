import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { useLang } from "../hooks/useLang.js";
import Page from "../components/Page.jsx";

function formatEta(seconds) {
  if (!seconds || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

function formatExportDate(d, t) {
  if (!d) return "—";
  const dt = new Date(d);
  if (isNaN(dt.getTime())) return d;
  const months = t("training.months");
  return `${months[dt.getMonth()]} ${dt.getFullYear()}`;
}

function computeCombined(botCount, exportCount, botW, expW, totalMax) {
  const wBot = botCount > 0 ? botW : 0;
  const wExp = exportCount > 0 ? expW : 0;
  const totalW = wBot + wExp;
  if (totalW <= 0) {
    return { total: 0, bot: 0, export: 0, botPct: 0, exportPct: 0 };
  }
  const fBot = wBot / totalW;
  const fExp = wExp / totalW;
  const limits = [Math.max(0, totalMax)];
  if (fBot > 0) limits.push(Math.floor(botCount / fBot));
  if (fExp > 0) limits.push(Math.floor(exportCount / fExp));
  const total = Math.min(...limits);
  const bot = Math.min(botCount, Math.round(total * fBot));
  const exp = Math.min(exportCount, Math.round(total * fExp));
  const sum = bot + exp;
  return {
    total: sum,
    bot,
    export: exp,
    botPct: sum > 0 ? Math.round((bot / sum) * 100) : 0,
    exportPct: sum > 0 ? Math.round((exp / sum) * 100) : 0,
  };
}

function phaseLabel(phase, t) {
  const text = t(`training.phases.${phase}`);
  return text === `training.phases.${phase}` ? phase : text;
}

export default function Training() {
  const { t } = useLang();
  const [status, setStatus] = useState(null);
  const [runs, setRuns] = useState([]);
  const [progress, setProgress] = useState(null);
  const [datasetInfo, setDatasetInfo] = useState(null);
  const [qualityStats, setQualityStats] = useState(null);
  const [voiceStats, setVoiceStats] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [runLogs, setRunLogs] = useState({});
  const [loadingLogId, setLoadingLogId] = useState(null);
  const [botEnabled, setBotEnabled] = useState(true);
  const [botWeight, setBotWeight] = useState(30);
  const [exportWeight, setExportWeight] = useState(70);
  const [totalMax, setTotalMax] = useState(10000);
  const [exportItems, setExportItems] = useState([
    { id: 1, path: "", status: "idle", error: "", preview: null },
  ]);
  const [cachedExports, setCachedExports] = useState([]);
  const [trainSource, setTrainSource] = useState("auto");

  const refresh = async () => {
    const [st, rs, qs] = await Promise.all([
      api.trainingStatus(),
      api.trainingRuns(),
      api.qualityStats(),
    ]);
    setStatus(st);
    setRuns(rs);
    setQualityStats(qs);
    try {
      const vs = await api.voiceStats();
      setVoiceStats(vs);
    } catch {
      // ignore
    }
    try {
      setCachedExports(await api.cachedExports());
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    refresh();
    const ev = new EventSource("/api/training/progress");
    ev.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data);
        const data = payload.data || payload;
        setProgress(data);
        if (data.phase === "done" || data.phase === "error" || data.phase === "cancelled") {
          refresh();
        }
      } catch {}
    };
    return () => {
      ev.close();
    };
  }, []);

  function addExportItem() {
    setExportItems((items) => [
      ...items,
      { id: Date.now(), path: "", status: "idle", error: "", preview: null },
    ]);
  }

  function removeExportItem(id) {
    setExportItems((items) => items.filter((it) => it.id !== id));
  }

  function updateExportItem(id, patch) {
    setExportItems((items) =>
      items.map((it) => (it.id === id ? { ...it, ...patch } : it)),
    );
  }

  function addCachedExport(path) {
    setExportItems((items) => {
      if (items.some((it) => it.path === path)) return items;
      const empty = items.find((it) => !it.path.trim());
      if (empty) {
        return items.map((it) =>
          it.id === empty.id ? { ...it, path } : it,
        );
      }
      return [
        ...items,
        { id: Date.now(), path, status: "idle", error: "", preview: null },
      ];
    });
  }

  async function checkExport(item) {
    const path = item.path.trim();
    if (!path) return;
    updateExportItem(item.id, { status: "checking", error: "", preview: null });
    try {
      const v = await api.validateExport(path);
      if (!v.valid) {
        updateExportItem(item.id, {
          status: "error",
          error: v.error || t("training.invalidFile"),
        });
        return;
      }
      const preview = await api.parseExport(path);
      updateExportItem(item.id, { status: "ok", preview, error: "" });
      api.cachedExports().then(setCachedExports).catch(() => {});
    } catch (e) {
      updateExportItem(item.id, { status: "error", error: e.message });
    }
  }

  async function build() {
    setError("");
    setBusy(true);
    try {
      const validExports = exportItems.filter(
        (it) => it.status === "ok" && it.path.trim(),
      );
      const useConfig = validExports.length > 0 || !botEnabled;
      const config = useConfig
        ? {
            use_bot_pairs: botEnabled,
            telegram_export_paths: validExports.map((it) => it.path.trim()),
            weights: { bot: botWeight / 100, export: exportWeight / 100 },
            total_max_samples: Number(totalMax) || 10000,
          }
        : null;
      const res = await api.buildDataset(config);
      setDatasetInfo(res);
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    setBusy(true);
    setError("");
    try {
      const res = await api.startTraining(trainSource);
      if (!res.started) setError(res.reason || t("training.failedStart"));
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    await api.cancelTraining();
  }

  async function activate(id) {
    await api.activateAdapter(id);
    refresh();
  }

  async function deactivate() {
    await api.deactivateAdapter();
    refresh();
  }

  async function exportGguf(run) {
    setError("");
    try {
      const res = await api.exportGguf(run.id);
      if (!res.started) {
        setError(res.reason || t("training.failedConvert"));
        return;
      }
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function exportLoraGguf(run) {
    setError("");
    try {
      const res = await api.exportLoraGguf(run.id);
      if (!res.started) {
        setError(res.reason || t("training.failedConvert"));
        return;
      }
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function removeRun(run) {
    const confirmed = window.confirm(t("training.confirmDeleteRun", { version: run.version }));
    if (!confirmed) return;
    setError("");
    try {
      await api.deleteTrainingRun(run.id);
      setRunLogs((prev) => {
        const next = { ...prev };
        delete next[run.id];
        return next;
      });
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function toggleLog(run) {
    if (runLogs[run.id]?.open) {
      setRunLogs((prev) => ({
        ...prev,
        [run.id]: { ...prev[run.id], open: false },
      }));
      return;
    }

    setLoadingLogId(run.id);
    setError("");
    try {
      const logInfo = runLogs[run.id]?.data || (await api.trainingRunErrorLog(run.id));
      setRunLogs((prev) => ({
        ...prev,
        [run.id]: { open: true, data: logInfo },
      }));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingLogId(null);
    }
  }

  const canTrain = (status?.training_pairs || 0) >= 50 && !status?.running;

  const botCount = botEnabled ? status?.training_pairs || 0 : 0;
  const exportCount = exportItems
    .filter((it) => it.status === "ok" && it.preview)
    .reduce((sum, it) => sum + (it.preview.total_pairs || 0), 0);
  const combined = computeCombined(
    botCount,
    exportCount,
    botWeight,
    exportWeight,
    Number(totalMax) || 10000,
  );
  const lowBotShare =
    botEnabled && combined.total > 0 && combined.bot / combined.total < 0.1;

  return (
    <Page>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat
          title={t("training.messages")}
          value={status?.messages_collected ?? "—"}
        />
        <Stat
          title={t("training.trainingPairs")}
          value={status?.training_pairs ?? "—"}
        />
        <Stat
          title={t("training.lastRun")}
          value={
            status?.last_run?.finished_at
              ? new Date(status.last_run.finished_at).toLocaleString()
              : status?.last_run?.status || "—"
          }
        />
        <Stat
          title={t("training.activeAdapter")}
          value={status?.active_adapter ? `v${status.active_adapter.version}` : "—"}
        />
      </div>

      {voiceStats && (
        <div className="tile">
          <div className="tile-label">{t("training.voiceMessages")}</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2 text-sm">
            <div>
              <div className="text-muted text-xs">
                {t("training.voiceReceived")}
              </div>
              <div className="text-lg font-semibold">{voiceStats.voice_received}</div>
            </div>
            <div>
              <div className="text-muted text-xs">
                {t("training.voiceTranscribed")}
              </div>
              <div className="text-lg font-semibold">{voiceStats.voice_transcribed}</div>
            </div>
            <div>
              <div className="text-muted text-xs">
                {t("training.voiceLowConfidence")}
              </div>
              <div className="text-lg font-semibold">{voiceStats.voice_low_confidence}</div>
            </div>
            <div>
              <div className="text-muted text-xs">
                {t("training.voiceAvgConfidence")}
              </div>
              <div className="text-lg font-semibold">
                {voiceStats.avg_confidence != null
                  ? `${Math.round((voiceStats.avg_confidence || 0) * 100)}%`
                  : "—"}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="tile">
        <div className="tile-label">{t("training.rejectedByQuality")}</div>
        <div className="text-xl font-semibold mt-1">
          {qualityStats
            ? t("training.rejectedOf", {
                rejected: qualityStats.total_rejected,
                generated: qualityStats.total_generated,
              })
            : "—"}
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-sm">
          {qualityStats?.reasons?.length ? (
            qualityStats.reasons.map((item) => (
              <span
                key={item.reason}
                className="rounded-full bg-surface px-3 py-1 text-muted"
              >
                {reasonLabel(item.reason, t)}: {item.count}
              </span>
            ))
          ) : (
            <span className="text-muted">{t("training.noRejections")}</span>
          )}
        </div>
      </div>

      <div className="tile space-y-4">
        <div className="tile-label">{t("training.dataSources")}</div>

        <div className="rounded-lg border border-line p-3 space-y-2">
          <div className="flex items-center gap-3">
            <span className="font-semibold">{t("training.botPairs")}</span>
            <label className="flex items-center gap-2 text-sm text-muted ml-auto select-none">
              <input
                type="checkbox"
                checked={botEnabled}
                onChange={(e) => setBotEnabled(e.target.checked)}
              />
              {t("training.enable")}
            </label>
          </div>
          <div className="text-sm text-muted">
            {t("training.botPairsCollected", {
              count: (status?.training_pairs || 0).toLocaleString(),
            })}
          </div>
          {botEnabled && (
            <div>
              <div className="flex justify-between text-xs text-muted">
                <span>{t("training.weight")}</span>
                <span>{botWeight}%</span>
              </div>
              <input
                type="range"
                min="0"
                max="100"
                value={botWeight}
                onChange={(e) => setBotWeight(Number(e.target.value))}
                className="w-full"
              />
            </div>
          )}
        </div>

        <div className="rounded-lg border border-line p-3 space-y-3">
          <div className="font-semibold">{t("training.telegramExport")}</div>
          <div className="text-xs text-muted whitespace-pre-line bg-bg rounded p-2">
            {t("training.exportInstructions")}
          </div>

          {exportItems.map((item) => (
            <div key={item.id} className="space-y-2">
              <div className="flex gap-2">
                <input
                  className="input flex-1"
                  placeholder={t("training.resultJsonPath")}
                  value={item.path}
                  onChange={(e) =>
                    updateExportItem(item.id, { path: e.target.value })
                  }
                />
                <button
                  className="btn-secondary"
                  onClick={() => checkExport(item)}
                  disabled={item.status === "checking" || !item.path.trim()}
                >
                  {item.status === "checking"
                    ? "..."
                    : t("training.checkFile")}
                </button>
                {exportItems.length > 1 && (
                  <button
                    className="btn-danger"
                    onClick={() => removeExportItem(item.id)}
                  >
                    ✕
                  </button>
                )}
              </div>
              {item.status === "error" && (
                <div className="text-bad text-sm">{item.error}</div>
              )}
              {item.status === "ok" && item.preview && (
                <div className="rounded-lg border border-good/40 bg-good/5 p-3 text-sm space-y-1">
                  <div className="text-good">{t("training.fileValid")}</div>
                  <div>
                    {t("training.foundPairs", {
                      count: (item.preview.total_pairs || 0).toLocaleString(),
                    })}
                  </div>
                  <div>
                    {t("training.period", {
                      from: formatExportDate(item.preview.date_range?.from, t),
                      to: formatExportDate(item.preview.date_range?.to, t),
                    })}
                  </div>
                  <div>
                    {t("training.chatsCount", {
                      count: item.preview.chats_count,
                    })}
                  </div>
                  <div>
                    {t("training.avgReply", {
                      count: item.preview.avg_reply_length,
                    })}
                  </div>
                  {item.preview.top_chats?.length > 0 && (
                    <div className="pt-1">
                      <div className="text-muted">
                        {t("training.topChats")}
                      </div>
                      {item.preview.top_chats.map((c, i) => (
                        <div key={i}>
                          •{" "}
                          {t("training.topChatRow", {
                            name: c.name,
                            pairs: c.pairs,
                          })}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}

          <button className="btn-secondary" onClick={addExportItem}>
            {t("training.addFile")}
          </button>

          <div>
            <div className="flex justify-between text-xs text-muted">
              <span>{t("training.exportWeight")}</span>
              <span>{exportWeight}%</span>
            </div>
            <input
              type="range"
              min="0"
              max="100"
              value={exportWeight}
              onChange={(e) => setExportWeight(Number(e.target.value))}
              className="w-full"
            />
          </div>

          {cachedExports.length > 0 && (
            <div className="text-xs text-muted">
              {t("training.previouslyChecked")}
              <div className="flex flex-wrap gap-2 mt-1">
                {cachedExports.map((c, i) => (
                  <button
                    key={i}
                    className="rounded-full bg-surface px-2 py-0.5 hover:bg-surface"
                    onClick={() => addCachedExport(c.path)}
                    title={c.path}
                  >
                    {c.path.split(/[\\/]/).pop()} ({c.pairs_count})
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-line p-3 space-y-2">
          <div className="text-sm text-muted">
            {t("training.totalWillUse")}
          </div>
          <div className="space-y-1 text-sm">
            <div className="flex items-center gap-2">
              <div className="flex-1 h-3 bg-surface rounded overflow-hidden">
                <div
                  className="h-3 bg-accent transition-all"
                  style={{ width: `${combined.botPct}%` }}
                />
              </div>
              <span className="w-48 text-right">
                {t("training.fromBot", {
                  count: combined.bot,
                  pct: combined.botPct,
                })}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex-1 h-3 bg-surface rounded overflow-hidden">
                <div
                  className="h-3 bg-good transition-all"
                  style={{ width: `${combined.exportPct}%` }}
                />
              </div>
              <span className="w-48 text-right">
                {t("training.fromExport", {
                  count: combined.export,
                  pct: combined.exportPct,
                })}
              </span>
            </div>
          </div>
          <div className="border-t border-line pt-2 font-semibold">
            {t("training.grandTotal", {
              count: combined.total.toLocaleString(),
            })}
          </div>
          {lowBotShare && (
            <div className="text-bad text-sm">{t("training.lowBotShare")}</div>
          )}
          <label className="flex items-center gap-2 text-xs text-muted">
            <span>{t("training.maxSamples")}</span>
            <input
              type="number"
              min="100"
              step="500"
              className="input w-32"
              value={totalMax}
              onChange={(e) => setTotalMax(e.target.value)}
            />
          </label>
        </div>
      </div>

      <div className="tile flex flex-wrap gap-2 items-center">
        <button className="btn-secondary" onClick={build} disabled={busy}>
          {busy
            ? t("training.building")
            : t("training.buildDataset", {
                count: combined.total.toLocaleString(),
              })}
        </button>
        <select
          className="input"
          value={trainSource}
          onChange={(e) => setTrainSource(e.target.value)}
          disabled={status?.running}
          title={t("training.trainSourceTitle")}
        >
          <option value="auto">{t("training.trainOnAuto")}</option>
          <option value="bot">{t("training.trainOnBot")}</option>
        </select>
        <button
          className="btn-primary"
          disabled={!canTrain || busy}
          onClick={start}
          title={!canTrain ? t("training.needPairs") : ""}
        >
          {status?.running
            ? t("training.trainingRunning")
            : t("training.startTraining")}
        </button>
        {status?.running && (
          <button className="btn-danger" onClick={cancel}>
            {t("training.cancel")}
          </button>
        )}
        {status?.active_adapter && (
          <button className="btn-secondary" onClick={deactivate}>
            {t("training.deactivateAdapter")}
          </button>
        )}
        {datasetInfo && datasetInfo.total_pairs > 0 && (
          <span className="text-sm text-muted ml-2">
            {t("training.datasetInfo", { count: datasetInfo.total_pairs })}
            {datasetInfo.breakdown
              ? t("training.datasetBreakdown", {
                  bot: datasetInfo.breakdown.bot,
                  export: datasetInfo.breakdown.export,
                })
              : datasetInfo.avg_output_len
                ? t("training.datasetAvgLen", {
                    len: datasetInfo.avg_output_len,
                  })
                : ""}
          </span>
        )}
        {datasetInfo?.warnings?.map((w, i) => (
          <span key={i} className="text-bad text-sm ml-2">
            ⚠️ {w}
          </span>
        ))}
        {error && <span className="text-bad text-sm ml-2">{error}</span>}
      </div>

      {progress && (() => {
        const percent =
          progress.phase === "training" && progress.max_steps > 0
            ? Math.min(
                100,
                Math.max(
                  0,
                  ((progress.step || 0) / progress.max_steps) * 100
                )
              )
            : null;
        const isRunningPhase =
          progress.phase &&
          progress.phase !== "done" &&
          progress.phase !== "error" &&
          progress.phase !== "cancelled";
        const showIndeterminate = isRunningPhase && percent == null;
        return (
        <div className="tile">
          <div className="flex flex-wrap gap-4 text-sm items-center">
            <span>
              {t("training.phaseLabel")}
              <b>{phaseLabel(progress.phase, t)}</b>
            </span>
            {progress.phase === "training" && (
              <>
                <span>
                  {t("training.epoch", {
                    value: progress.epoch?.toFixed?.(2) ?? "—",
                  })}
                </span>
                <span>
                  {t("training.step", {
                    step: progress.step ?? "—",
                    max: progress.max_steps ?? "—",
                  })}
                </span>
                <span>
                  {t("training.loss", {
                    value: progress.loss?.toFixed?.(4) ?? "—",
                  })}
                </span>
                <span>
                  {t("training.eta", { value: formatEta(progress.eta_seconds) })}
                </span>
                {percent != null && <span>{percent.toFixed(1)}%</span>}
              </>
            )}
            {progress.phase === "done" && progress.final_loss != null && (
              <span>
                {t("training.finalLoss")}
                <b>{progress.final_loss.toFixed(4)}</b>
              </span>
            )}
            {progress.phase === "done" && progress.gguf_path && (
              <span className="text-good">
                GGUF: <code className="text-xs">{progress.gguf_path}</code>
                {progress.gguf_path.toLowerCase().includes(".lora.") && (
                  <span className="text-muted ml-2">
                    {t("training.ggufLoraNote")}
                  </span>
                )}
              </span>
            )}
            {progress.phase === "done" && progress.gguf_path === null && (
              <span className="text-bad">
                {t("training.ggufNotConverted", {
                  reason:
                    progress.gguf_skip_reason || t("training.ggufConfigHint"),
                })}
              </span>
            )}
            {progress.phase === "error" && (
              <span className="text-bad">
                {t("training.errorPrefix", {
                  msg: progress.error || t("training.unknownError"),
                })}
              </span>
            )}
            {progress.phase === "cancelled" && (
              <span className="text-muted">
                {t("training.cancelledByUser")}
              </span>
            )}
          </div>
          {percent != null && (
            <div className="mt-3 h-2 bg-surface rounded">
              <div
                className="h-2 bg-accent rounded transition-all"
                style={{ width: `${percent}%` }}
              />
            </div>
          )}
          {showIndeterminate && (
            <div className="mt-3 progress-indeterminate" />
          )}
        </div>
        );
      })()}

      <div className="tile">
        <div className="text-sm text-muted mb-2">
          {t("training.adapterHistory")}
        </div>
        <table className="w-full text-sm">
          <thead className="text-muted">
            <tr className="text-left">
              <th className="py-1">{t("training.thVersion")}</th>
              <th>{t("training.thDate")}</th>
              <th>{t("training.thPairs")}</th>
              <th>{t("training.thLoss")}</th>
              <th>{t("training.thStatus")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan="6" className="text-muted py-3">
                  {t("training.noRuns")}
                </td>
              </tr>
            )}
            {runs.map((r) => (
              <React.Fragment key={r.id}>
                <tr className="border-t border-line">
                  <td className="py-2">v{r.version}</td>
                  <td>
                    {r.started_at ? new Date(r.started_at).toLocaleString() : "—"}
                  </td>
                  <td>{r.pair_count}</td>
                  <td>{r.final_loss?.toFixed?.(4) ?? "—"}</td>
                  <td>
                    {r.is_active ? (
                      <span className="text-good">{t("training.active")}</span>
                    ) : (
                      r.status
                    )}
                  </td>
                  <td className="text-right">
                    <div className="flex justify-end gap-2 flex-wrap">
                      {!r.is_active && r.status === "done" && (
                        <button
                          className="btn-secondary"
                          onClick={() => activate(r.id)}
                        >
                          {t("training.activate")}
                        </button>
                      )}
                      {r.is_active && (
                        <button className="btn-secondary" onClick={deactivate}>
                          {t("training.deactivate")}
                        </button>
                      )}
                      {r.status === "done" && (
                        <button
                          className="btn-secondary"
                          disabled={status?.running}
                          onClick={() => exportLoraGguf(r)}
                          title={
                            status?.running
                              ? t("training.waitProcess")
                              : t("training.loraGgufTitle")
                          }
                        >
                          LoRA → GGUF
                        </button>
                      )}
                      {r.status === "done" && (
                        <button
                          className="btn-secondary"
                          disabled={status?.running}
                          onClick={() => exportGguf(r)}
                          title={
                            status?.running
                              ? t("training.waitProcess")
                              : t("training.mergeGgufTitle")
                          }
                        >
                          Merge → GGUF
                        </button>
                      )}
                      {r.status !== "running" && (
                        <button
                          className="btn-secondary"
                          onClick={() => toggleLog(r)}
                        >
                          {loadingLogId === r.id
                            ? t("training.loadingLog")
                            : runLogs[r.id]?.open
                              ? t("training.hideLog")
                              : t("training.log")}
                        </button>
                      )}
                      {(r.status === "failed" || r.status === "cancelled") &&
                        !r.is_active && (
                        <button
                          className="btn-danger"
                          onClick={() => removeRun(r)}
                        >
                          {t("common.delete")}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
                {runLogs[r.id]?.open && (
                  <tr className="border-t border-line">
                    <td colSpan="6" className="pb-3">
                      <div className="mt-2 rounded-lg border border-line bg-bg p-3">
                        <div className="flex flex-wrap gap-2 items-center text-xs text-muted mb-2">
                          <span>{t("training.runLog", { version: r.version })}</span>
                          {runLogs[r.id].data?.log_path && (
                            <code className="break-all">{runLogs[r.id].data.log_path}</code>
                          )}
                        </div>
                        {runLogs[r.id].data?.error && (
                          <div
                            className={`text-sm mb-2 ${
                              r.status === "failed" ? "text-bad" : "text-muted"
                            }`}
                          >
                            {r.status === "failed"
                              ? t("training.errorPrefix", { msg: "" })
                              : t("training.lastLine")}
                            {runLogs[r.id].data.error}
                          </div>
                        )}
                        {runLogs[r.id].data?.excerpt ? (
                          <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs text-fg/90">
                            {runLogs[r.id].data.excerpt}
                          </pre>
                        ) : (
                          <div className="text-muted text-sm">
                            {t("training.logNotFound")}
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </Page>
  );
}

function reasonLabel(reason, t) {
  const text = t(`training.reasons.${reason}`);
  return text === `training.reasons.${reason}` ? reason : text;
}

function Stat({ title, value }) {
  return (
    <div className="tile">
      <div className="tile-label">{title}</div>
      <div className="text-xl font-semibold mt-1 truncate">{value}</div>
    </div>
  );
}
