import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { useLang } from "../hooks/useLang.js";

function formatBytes(bytes, t) {
  if (!bytes || bytes <= 0) return t("replication.zeroBytes");
  const units = t("replication.bytes");
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(value < 10 ? 2 : 1)} ${units[i]}`;
}

function formatDuration(ms, t) {
  if (!ms || ms <= 0) return "—";
  if (ms < 1000) return t("replication.ms", { value: ms });
  const s = ms / 1000;
  if (s < 60) return t("replication.sec", { value: s.toFixed(1) });
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  return t("replication.minSec", { m, s: rem });
}

const STATUS_CLS = {
  running: "text-accent",
  done: "text-good",
  error: "text-bad",
  cancelled: "text-muted",
  deleted: "text-bad",
};

function statusLabel(status, t) {
  const text = t(`replication.statuses.${status}`);
  return {
    text: text === `replication.statuses.${status}` ? status : text,
    cls: STATUS_CLS[status] || "text-muted",
  };
}

function phaseLabel(phase, t) {
  const text = t(`replication.phases.${phase}`);
  return text === `replication.phases.${phase}` ? phase : text;
}

export default function Replication() {
  const { t } = useLang();
  const [status, setStatus] = useState(null);
  const [runs, setRuns] = useState([]);
  const [progress, setProgress] = useState(null);
  const [form, setForm] = useState({
    enabled: false,
    interval_minutes: 60,
    target_dir: "",
    retention: 10,
    delete_protection: true,
    list_limit: 10,
  });
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [savedMsg, setSavedMsg] = useState("");
  const [error, setError] = useState("");
  const [runLogs, setRunLogs] = useState({});
  const [loadingLogId, setLoadingLogId] = useState(null);

  const refresh = async () => {
    try {
      const [st, rs] = await Promise.all([
        api.replicationStatus(),
        api.replicationRuns(),
      ]);
      setStatus(st);
      setRuns(rs);
      if (st?.settings) {
        setForm((prev) => ({
          ...prev,
          enabled: !!st.settings.enabled,
          interval_minutes: st.settings.interval_minutes,
          target_dir: st.settings.target_dir || "",
          retention: st.settings.retention,
          delete_protection: !!st.settings.delete_protection,
          list_limit: st.settings.list_limit ?? prev.list_limit,
        }));
      }
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    refresh();
    const ev = new EventSource("/api/replication/progress");
    ev.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data);
        const data = payload.data || payload;
        setProgress(data);
        if (
          data.phase === "done" ||
          data.phase === "error" ||
          data.phase === "cancelled"
        ) {
          refresh();
        }
      } catch {}
    };
    return () => ev.close();
  }, []);

  async function saveSettings(e) {
    e?.preventDefault?.();
    setSaving(true);
    setError("");
    setSavedMsg("");
    try {
      await api.saveReplicationSettings({
        enabled: form.enabled,
        interval_minutes: Number(form.interval_minutes),
        target_dir: form.target_dir,
        retention: Number(form.retention),
        delete_protection: form.delete_protection,
        list_limit: Number(form.list_limit),
      });
      setSavedMsg(t("common.saved"));
      setTimeout(() => setSavedMsg(""), 2000);
      refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function runNow() {
    setBusy(true);
    setError("");
    setProgress(null);
    try {
      const res = await api.runReplication();
      if (!res.started) setError(res.reason || t("replication.failedStart"));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    try {
      await api.cancelReplication();
    } catch (err) {
      setError(err.message);
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
      const data =
        runLogs[run.id]?.data || (await api.replicationRunLog(run.id));
      setRunLogs((prev) => ({
        ...prev,
        [run.id]: { open: true, data },
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingLogId(null);
    }
  }

  async function removeRun(run) {
    const isProtected = run.protected && form.delete_protection;
    if (isProtected) {
      const confirmText = t("replication.confirmDelete", {
        date: run.started_at
          ? new Date(run.started_at).toLocaleString()
          : "—",
      });
      if (!window.confirm(confirmText)) return;
    }
    setError("");
    try {
      await api.deleteReplicationRun(run.id, isProtected);
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  const isRunning =
    status?.running ||
    (progress &&
      progress.phase &&
      progress.phase !== "done" &&
      progress.phase !== "error" &&
      progress.phase !== "cancelled");

  const percent =
    progress?.percent != null
      ? Math.min(100, Math.max(0, progress.percent))
      : null;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat
          title={t("replication.sourceDb")}
          value={formatBytes(status?.source_bytes || 0, t)}
          hint={status?.source_path}
        />
        <Stat
          title={t("replication.replicasStored")}
          value={runs.filter((r) => r.exists).length}
          hint={t("replication.totalRecords", { count: runs.length })}
        />
        <Stat
          title={t("replication.lastReplication")}
          value={
            status?.last_run?.finished_at
              ? new Date(status.last_run.finished_at).toLocaleString()
              : status?.last_run?.status || "—"
          }
          hint={
            status?.last_run?.status
              ? statusLabel(status.last_run.status, t).text
              : ""
          }
        />
        <Stat
          title={t("replication.deleteProtection")}
          value={
            form.delete_protection
              ? t("replication.enabled")
              : t("replication.disabled")
          }
          hint={
            form.delete_protection
              ? t("replication.confirmRequired")
              : t("replication.off")
          }
        />
      </div>

      <form className="card space-y-4" onSubmit={saveSettings}>
        <div className="label">{t("replication.settings")}</div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) =>
                setForm({ ...form, enabled: e.target.checked })
              }
            />
            <span>{t("replication.autoSchedule")}</span>
          </label>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.delete_protection}
              onChange={(e) =>
                setForm({ ...form, delete_protection: e.target.checked })
              }
            />
            <span>{t("replication.protectReplicas")}</span>
          </label>

          <label className="text-sm space-y-1">
            <div className="text-muted text-xs">
              {t("replication.intervalMinutes")}
            </div>
            <input
              type="number"
              min="1"
              max="10080"
              className="w-full bg-bg border border-line rounded px-3 py-2"
              value={form.interval_minutes}
              onChange={(e) =>
                setForm({ ...form, interval_minutes: e.target.value })
              }
            />
          </label>

          <label className="text-sm space-y-1">
            <div className="text-muted text-xs">
              {t("replication.retentionLabel")}
            </div>
            <input
              type="number"
              min="1"
              max="1000"
              className="w-full bg-bg border border-line rounded px-3 py-2"
              value={form.retention}
              onChange={(e) =>
                setForm({ ...form, retention: e.target.value })
              }
            />
          </label>

          <label className="text-sm space-y-1">
            <div className="text-muted text-xs">
              {t("replication.listLimitLabel")}
            </div>
            <input
              type="number"
              min="1"
              max="1000"
              className="w-full bg-bg border border-line rounded px-3 py-2"
              value={form.list_limit}
              onChange={(e) =>
                setForm({ ...form, list_limit: e.target.value })
              }
            />
          </label>

          <label className="text-sm space-y-1 md:col-span-2">
            <div className="text-muted text-xs">
              {t("replication.targetDirLabel")}
            </div>
            <input
              type="text"
              placeholder="./replicas"
              className="w-full bg-bg border border-line rounded px-3 py-2"
              value={form.target_dir}
              onChange={(e) =>
                setForm({ ...form, target_dir: e.target.value })
              }
            />
          </label>
        </div>

        <div className="flex flex-wrap gap-2 items-center">
          <button
            type="submit"
            className="btn-primary"
            disabled={saving}
          >
            {saving ? t("common.saving") : t("replication.saveSettings")}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={runNow}
            disabled={busy || isRunning}
          >
            {isRunning
              ? t("replication.replicationRunning")
              : t("replication.runNow")}
          </button>
          {isRunning && (
            <button
              type="button"
              className="btn-danger"
              onClick={cancel}
            >
              {t("replication.cancel")}
            </button>
          )}
          {savedMsg && (
            <span className="text-good text-sm ml-2">{savedMsg}</span>
          )}
          {error && (
            <span className="text-bad text-sm ml-2">{error}</span>
          )}
        </div>
      </form>

      {progress && (
        <div className="card">
          <div className="flex flex-wrap gap-4 text-sm items-center">
            <span>
              {t("replication.phaseLabel")}
              <b>{phaseLabel(progress.phase, t)}</b>
            </span>
            {progress.phase === "copying" && (
              <>
                <span>
                  {t("replication.copied", {
                    copied: formatBytes(progress.copied_bytes || 0, t),
                    total: formatBytes(progress.total_bytes || 0, t),
                  })}
                </span>
                <span>
                  {t("replication.pages", {
                    copied: progress.copied_pages || 0,
                    total: progress.total_pages || 0,
                  })}
                </span>
                {percent != null && <span>{percent.toFixed(1)}%</span>}
              </>
            )}
            {progress.phase === "done" && (
              <>
                <span className="text-good">
                  {t("replication.replicaCreated")}
                  <code className="text-xs">{progress.target}</code>
                </span>
                <span>
                  {t("replication.sizeLabel", {
                    value: formatBytes(progress.copied_bytes || 0, t),
                  })}
                </span>
                <span>
                  {t("replication.timeLabel", {
                    value: formatDuration(progress.duration_ms, t),
                  })}
                </span>
                {progress.pruned > 0 && (
                  <span className="text-muted">
                    {t("replication.prunedOld", { count: progress.pruned })}
                  </span>
                )}
              </>
            )}
            {progress.phase === "error" && (
              <span className="text-bad">
                {t("replication.errorPrefix")}
                {progress.error || t("replication.unknownError")}
              </span>
            )}
            {progress.phase === "cancelled" && (
              <span className="text-muted">
                {t("replication.cancelledByUser")}
              </span>
            )}
          </div>
          {progress.phase === "copying" && percent != null && (
            <div className="mt-3 h-2 bg-surface rounded">
              <div
                className="h-2 bg-accent rounded transition-all"
                style={{ width: `${percent}%` }}
              />
            </div>
          )}
        </div>
      )}

      <div className="card">
        <div className="text-sm text-muted mb-2">
          {t("replication.history")}
        </div>
        <table className="w-full text-sm">
          <thead className="text-muted">
            <tr className="text-left">
              <th className="py-1">{t("replication.thStart")}</th>
              <th>{t("replication.thDuration")}</th>
              <th>{t("replication.thSize")}</th>
              <th>{t("replication.thTrigger")}</th>
              <th>{t("replication.thStatus")}</th>
              <th>{t("replication.thFile")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan="7" className="text-muted py-3">
                  {t("replication.noRuns")}
                </td>
              </tr>
            )}
            {runs.map((r) => {
              const st = statusLabel(r.status, t);
              const logState = runLogs[r.id];
              return (
                <React.Fragment key={r.id}>
                  <tr className="border-t border-line align-top">
                    <td className="py-2">
                      {r.started_at
                        ? new Date(r.started_at).toLocaleString()
                        : "—"}
                    </td>
                    <td>{formatDuration(r.duration_ms, t)}</td>
                    <td>{formatBytes(r.copied_bytes || 0, t)}</td>
                    <td className="text-muted">
                      {r.trigger === "schedule"
                        ? t("replication.bySchedule")
                        : t("replication.manual")}
                    </td>
                    <td>
                      <span className={st.cls}>{st.text}</span>
                      {r.error && (
                        <div className="text-bad text-xs mt-1 break-all">
                          {r.error}
                        </div>
                      )}
                    </td>
                    <td className="text-xs">
                      <code className="break-all">{r.target_path}</code>
                      {!r.exists && r.status === "done" && (
                        <div className="text-muted text-xs mt-1">
                          {t("replication.fileMissing")}
                        </div>
                      )}
                      {r.protected && (
                        <div className="text-muted text-xs mt-1">
                          {t("replication.protectedLabel")}
                        </div>
                      )}
                    </td>
                    <td className="text-right">
                      <div className="flex justify-end gap-2 flex-wrap">
                        {r.status !== "running" && (
                          <button
                            className="btn-secondary"
                            onClick={() => toggleLog(r)}
                          >
                            {loadingLogId === r.id
                              ? t("replication.loadingLog")
                              : logState?.open
                                ? t("replication.hideLog")
                                : t("replication.log")}
                          </button>
                        )}
                        {r.status !== "running" && (
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
                  {logState?.open && (
                    <tr className="border-t border-line">
                      <td colSpan="7" className="pb-3">
                        <div className="mt-2 rounded-lg border border-line bg-bg p-3">
                          <div className="flex flex-wrap gap-2 items-center text-xs text-muted mb-2">
                            <span>{t("replication.runLog", { id: r.id })}</span>
                            {logState.data?.log_path && (
                              <code className="break-all">
                                {logState.data.log_path}
                              </code>
                            )}
                          </div>
                          {logState.data?.error && (
                            <div
                              className={`text-sm mb-2 ${
                                r.status === "error"
                                  ? "text-bad"
                                  : "text-muted"
                              }`}
                            >
                              {r.status === "error"
                                ? t("replication.errorPrefix")
                                : t("replication.summary")}
                              {logState.data.error}
                            </div>
                          )}
                          {logState.data?.excerpt ? (
                            <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs text-fg/90">
                              {logState.data.excerpt}
                            </pre>
                          ) : (
                            <div className="text-muted text-sm">
                              {t("replication.noLog")}
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ title, value, hint }) {
  return (
    <div className="card">
      <div className="label">{title}</div>
      <div className="text-xl font-semibold mt-1 truncate">{value}</div>
      {hint && (
        <div className="text-muted text-xs mt-1 truncate" title={hint}>
          {hint}
        </div>
      )}
    </div>
  );
}
