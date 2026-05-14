import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(value < 10 ? 2 : 1)} ${units[i]}`;
}

function formatDuration(ms) {
  if (!ms || ms <= 0) return "—";
  if (ms < 1000) return `${ms} мс`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} с`;
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  return `${m}м ${rem}с`;
}

const STATUS_LABELS = {
  running: { text: "идёт", cls: "text-accent" },
  done: { text: "успех", cls: "text-good" },
  error: { text: "ошибка", cls: "text-bad" },
  cancelled: { text: "отменено", cls: "text-muted" },
};

const PHASE_LABELS = {
  starting: "запуск...",
  copying: "копирование",
  done: "успех",
  error: "ошибка",
  cancelled: "отменено",
};

export default function Replication() {
  const [status, setStatus] = useState(null);
  const [runs, setRuns] = useState([]);
  const [progress, setProgress] = useState(null);
  const [form, setForm] = useState({
    enabled: false,
    interval_minutes: 60,
    target_dir: "",
    retention: 10,
    delete_protection: true,
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
      });
      setSavedMsg("Сохранено");
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
      if (!res.started) setError(res.reason || "не удалось запустить");
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
      const confirmText = `Включена защита от случайных удалений.\n\nУдалить реплику от ${
        run.started_at ? new Date(run.started_at).toLocaleString() : "—"
      }?\nФайл будет удалён безвозвратно.`;
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
          title="Исходная БД"
          value={formatBytes(status?.source_bytes || 0)}
          hint={status?.source_path}
        />
        <Stat
          title="Реплик хранится"
          value={runs.filter((r) => r.exists).length}
          hint={`всего записей: ${runs.length}`}
        />
        <Stat
          title="Последняя репликация"
          value={
            status?.last_run?.finished_at
              ? new Date(status.last_run.finished_at).toLocaleString()
              : status?.last_run?.status || "—"
          }
          hint={
            status?.last_run?.status
              ? STATUS_LABELS[status.last_run.status]?.text
              : ""
          }
        />
        <Stat
          title="Защита от удаления"
          value={form.delete_protection ? "включена" : "выключена"}
          hint={form.delete_protection ? "подтверждение обязательно" : "выкл"}
        />
      </div>

      <form className="card space-y-4" onSubmit={saveSettings}>
        <div className="label">Настройки репликации</div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) =>
                setForm({ ...form, enabled: e.target.checked })
              }
            />
            <span>Автоматическая репликация по расписанию</span>
          </label>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.delete_protection}
              onChange={(e) =>
                setForm({ ...form, delete_protection: e.target.checked })
              }
            />
            <span>Защита от случайных удалений реплик</span>
          </label>

          <label className="text-sm space-y-1">
            <div className="text-muted text-xs">Интервал (минут)</div>
            <input
              type="number"
              min="1"
              max="10080"
              className="w-full bg-bg border border-white/10 rounded px-3 py-2"
              value={form.interval_minutes}
              onChange={(e) =>
                setForm({ ...form, interval_minutes: e.target.value })
              }
            />
          </label>

          <label className="text-sm space-y-1">
            <div className="text-muted text-xs">
              Сколько копий хранить (старые удаляются автоматически)
            </div>
            <input
              type="number"
              min="1"
              max="1000"
              className="w-full bg-bg border border-white/10 rounded px-3 py-2"
              value={form.retention}
              onChange={(e) =>
                setForm({ ...form, retention: e.target.value })
              }
            />
          </label>

          <label className="text-sm space-y-1 md:col-span-2">
            <div className="text-muted text-xs">
              Папка для реплик (относительно корня проекта или абсолютный путь)
            </div>
            <input
              type="text"
              placeholder="./replicas"
              className="w-full bg-bg border border-white/10 rounded px-3 py-2"
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
            {saving ? "Сохраняю..." : "Сохранить настройки"}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={runNow}
            disabled={busy || isRunning}
          >
            {isRunning ? "Идёт репликация..." : "Запустить сейчас"}
          </button>
          {isRunning && (
            <button
              type="button"
              className="btn-danger"
              onClick={cancel}
            >
              Отменить
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
              Этап:{" "}
              <b>{PHASE_LABELS[progress.phase] || progress.phase}</b>
            </span>
            {progress.phase === "copying" && (
              <>
                <span>
                  Скопировано:{" "}
                  {formatBytes(progress.copied_bytes || 0)} /{" "}
                  {formatBytes(progress.total_bytes || 0)}
                </span>
                <span>
                  Страниц: {progress.copied_pages || 0} /{" "}
                  {progress.total_pages || 0}
                </span>
                {percent != null && <span>{percent.toFixed(1)}%</span>}
              </>
            )}
            {progress.phase === "done" && (
              <>
                <span className="text-good">
                  Реплика создана:{" "}
                  <code className="text-xs">{progress.target}</code>
                </span>
                <span>Размер: {formatBytes(progress.copied_bytes || 0)}</span>
                <span>Время: {formatDuration(progress.duration_ms)}</span>
                {progress.pruned > 0 && (
                  <span className="text-muted">
                    удалено старых: {progress.pruned}
                  </span>
                )}
              </>
            )}
            {progress.phase === "error" && (
              <span className="text-bad">
                Ошибка: {progress.error || "неизвестная ошибка"}
              </span>
            )}
            {progress.phase === "cancelled" && (
              <span className="text-muted">Отменено пользователем</span>
            )}
          </div>
          {progress.phase === "copying" && percent != null && (
            <div className="mt-3 h-2 bg-white/10 rounded">
              <div
                className="h-2 bg-accent rounded transition-all"
                style={{ width: `${percent}%` }}
              />
            </div>
          )}
        </div>
      )}

      <div className="card">
        <div className="text-sm text-muted mb-2">История репликаций</div>
        <table className="w-full text-sm">
          <thead className="text-muted">
            <tr className="text-left">
              <th className="py-1">Начало</th>
              <th>Длительность</th>
              <th>Размер</th>
              <th>Триггер</th>
              <th>Статус</th>
              <th>Файл</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan="7" className="text-muted py-3">
                  Репликаций ещё не было.
                </td>
              </tr>
            )}
            {runs.map((r) => {
              const st = STATUS_LABELS[r.status] || {
                text: r.status,
                cls: "text-muted",
              };
              const logState = runLogs[r.id];
              return (
                <React.Fragment key={r.id}>
                  <tr className="border-t border-white/5 align-top">
                    <td className="py-2">
                      {r.started_at
                        ? new Date(r.started_at).toLocaleString()
                        : "—"}
                    </td>
                    <td>{formatDuration(r.duration_ms)}</td>
                    <td>{formatBytes(r.copied_bytes || 0)}</td>
                    <td className="text-muted">
                      {r.trigger === "schedule" ? "по расписанию" : "вручную"}
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
                          файл отсутствует
                        </div>
                      )}
                      {r.protected && (
                        <div className="text-muted text-xs mt-1">защищена</div>
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
                              ? "Загрузка..."
                              : logState?.open
                                ? "Скрыть лог"
                                : "Лог"}
                          </button>
                        )}
                        {r.status !== "running" && (
                          <button
                            className="btn-danger"
                            onClick={() => removeRun(r)}
                          >
                            Удалить
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {logState?.open && (
                    <tr className="border-t border-white/5">
                      <td colSpan="7" className="pb-3">
                        <div className="mt-2 rounded-lg border border-white/10 bg-black/30 p-3">
                          <div className="flex flex-wrap gap-2 items-center text-xs text-muted mb-2">
                            <span>Лог репликации #{r.id}</span>
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
                                ? "Ошибка: "
                                : "Резюме: "}
                              {logState.data.error}
                            </div>
                          )}
                          {logState.data?.excerpt ? (
                            <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs text-white/90">
                              {logState.data.excerpt}
                            </pre>
                          ) : (
                            <div className="text-muted text-sm">
                              Записи в логе не найдены.
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
