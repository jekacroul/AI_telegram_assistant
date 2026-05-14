import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";

function formatBytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatDuration(ms) {
  if (!ms || ms < 0) return "—";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  return `${m}m ${rem}s`;
}

const STATUS_LABEL = {
  running: "идёт",
  done: "успешно",
  failed: "ошибка",
  cancelled: "отменено",
};

const PHASE_LABEL = {
  starting: "Подготовка",
  copying: "Копирование",
  done: "Завершено",
  failed: "Ошибка",
  cancelled: "Отменено",
};

export default function Replication() {
  const [status, setStatus] = useState(null);
  const [runs, setRuns] = useState([]);
  const [progress, setProgress] = useState(null);
  const [form, setForm] = useState({
    enabled: false,
    destination: "",
    interval_minutes: 1440,
    keep_last: 10,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [confirmToken, setConfirmToken] = useState("CONFIRM-DELETE");

  const refresh = async () => {
    const [st, rs] = await Promise.all([
      api.replicationStatus(),
      api.replicationRuns(),
    ]);
    setStatus(st);
    setRuns(rs);
    if (st?.delete_confirm_token) setConfirmToken(st.delete_confirm_token);
    if (st?.settings) {
      setForm((prev) => ({
        ...prev,
        enabled: st.settings.enabled,
        destination: st.settings.destination,
        interval_minutes: st.settings.interval_minutes,
        keep_last: st.settings.keep_last,
      }));
    }
    if (st?.last_event && Object.keys(st.last_event).length) {
      setProgress(st.last_event);
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
          data.phase === "failed" ||
          data.phase === "cancelled"
        ) {
          refresh();
        }
      } catch {}
    };
    return () => ev.close();
  }, []);

  async function saveSettings() {
    setError("");
    setInfo("");
    setBusy(true);
    try {
      await api.saveReplicationSettings({
        enabled: form.enabled,
        destination: form.destination,
        interval_minutes: Number(form.interval_minutes),
        keep_last: Number(form.keep_last),
      });
      setInfo("Настройки сохранены");
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function startNow() {
    setError("");
    setInfo("");
    try {
      await api.startReplication();
    } catch (e) {
      setError(e.message);
    }
  }

  async function cancelNow() {
    try {
      await api.cancelReplication();
    } catch (e) {
      setError(e.message);
    }
  }

  async function toggleProtect(run) {
    try {
      await api.setReplicationProtected(run.id, !run.protected);
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function removeRun(run) {
    if (run.protected) {
      setError(
        "Запись защищена от удаления. Снимите защиту, прежде чем удалять.",
      );
      return;
    }
    const ok = window.confirm(
      `Удалить реплику #${run.id} от ${new Date(
        run.started_at,
      ).toLocaleString()}?\n\nФайл и запись будут безвозвратно удалены.`,
    );
    if (!ok) return;
    setError("");
    try {
      await api.deleteReplicationRun(run.id, confirmToken);
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  const lastRun = runs[0];
  const running = status?.running || progress?.phase === "copying" || progress?.phase === "starting";

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat
          title="Реплик в истории"
          value={runs.length}
        />
        <Stat
          title="Последний запуск"
          value={
            lastRun?.finished_at
              ? new Date(lastRun.finished_at).toLocaleString()
              : lastRun
                ? STATUS_LABEL[lastRun.status] || lastRun.status
                : "—"
          }
        />
        <Stat
          title="Статус"
          value={running ? "идёт репликация..." : STATUS_LABEL[lastRun?.status] || "—"}
        />
        <Stat
          title="Авто-репликация"
          value={status?.settings?.enabled ? `каждые ${status.settings.interval_minutes} мин` : "выключена"}
        />
      </div>

      <div className="card space-y-3">
        <div className="text-sm font-semibold">Настройки</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="text-sm flex flex-col gap-1">
            <span className="text-muted">Путь назначения</span>
            <input
              className="bg-bg border border-white/10 rounded px-3 py-2 text-sm"
              value={form.destination}
              onChange={(e) =>
                setForm((p) => ({ ...p, destination: e.target.value }))
              }
              placeholder="./backups/replicas"
            />
            <span className="text-xs text-muted">
              Файлы реплик будут сохраняться как
              <code className="mx-1">replica_YYYYMMDD_HHMMSS.db</code> в этой папке.
            </span>
          </label>
          <label className="text-sm flex flex-col gap-1">
            <span className="text-muted">Интервал, минут (мин 5)</span>
            <input
              type="number"
              min="5"
              className="bg-bg border border-white/10 rounded px-3 py-2 text-sm"
              value={form.interval_minutes}
              onChange={(e) =>
                setForm((p) => ({ ...p, interval_minutes: e.target.value }))
              }
            />
          </label>
          <label className="text-sm flex flex-col gap-1">
            <span className="text-muted">Хранить последних реплик</span>
            <input
              type="number"
              min="1"
              max="200"
              className="bg-bg border border-white/10 rounded px-3 py-2 text-sm"
              value={form.keep_last}
              onChange={(e) =>
                setForm((p) => ({ ...p, keep_last: e.target.value }))
              }
            />
            <span className="text-xs text-muted">
              Старые файлы автоматически удаляются. Записи в истории остаются.
            </span>
          </label>
          <label className="text-sm flex items-center gap-2 mt-1">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) =>
                setForm((p) => ({ ...p, enabled: e.target.checked }))
              }
            />
            <span>Включить автоматическую репликацию</span>
          </label>
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          <button className="btn-secondary" onClick={saveSettings} disabled={busy}>
            Сохранить настройки
          </button>
          <button
            className="btn-primary"
            onClick={startNow}
            disabled={running}
          >
            {running ? "Идёт репликация..." : "Реплицировать сейчас"}
          </button>
          {running && (
            <button className="btn-danger" onClick={cancelNow}>
              Отменить
            </button>
          )}
          {info && <span className="text-good text-sm ml-2">{info}</span>}
          {error && <span className="text-bad text-sm ml-2">{error}</span>}
        </div>
      </div>

      {progress && (
        <div className="card">
          <div className="flex flex-wrap gap-4 text-sm">
            <span>
              Фаза: <b>{PHASE_LABEL[progress.phase] || progress.phase || "—"}</b>
            </span>
            {progress.bytes_total > 0 && (
              <>
                <span>
                  Скопировано: {formatBytes(progress.bytes_copied)} /{" "}
                  {formatBytes(progress.bytes_total)}
                </span>
                <span>
                  {Math.round(
                    ((progress.bytes_copied || 0) / progress.bytes_total) * 100,
                  )}
                  %
                </span>
              </>
            )}
            {progress.phase === "done" && (
              <span className="text-good">
                Готово за {formatDuration(progress.duration_ms)}
              </span>
            )}
            {progress.phase === "failed" && (
              <span className="text-bad">
                Ошибка: {progress.error || "unknown"}
              </span>
            )}
            {progress.phase === "cancelled" && (
              <span className="text-muted">Отменено пользователем</span>
            )}
          </div>
          {progress.bytes_total > 0 && progress.phase !== "failed" && (
            <div className="mt-2 h-2 bg-white/10 rounded">
              <div
                className={`h-2 rounded ${
                  progress.phase === "done" ? "bg-good" : "bg-accent"
                }`}
                style={{
                  width: `${Math.min(
                    100,
                    ((progress.bytes_copied || 0) / progress.bytes_total) * 100,
                  )}%`,
                }}
              />
            </div>
          )}
          {progress.destination && (
            <div className="text-xs text-muted mt-2 break-all">
              Файл: <code>{progress.destination}</code>
            </div>
          )}
          {progress.rotated > 0 && (
            <div className="text-xs text-muted mt-1">
              Удалено старых файлов: {progress.rotated}
            </div>
          )}
        </div>
      )}

      <div className="card">
        <div className="text-sm text-muted mb-2">История реплик</div>
        <table className="w-full text-sm">
          <thead className="text-muted">
            <tr className="text-left">
              <th className="py-1">#</th>
              <th>Начало</th>
              <th>Длительность</th>
              <th>Размер</th>
              <th>Источник</th>
              <th>Статус</th>
              <th>Защита</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan="8" className="text-muted py-3">
                  Реплик пока не было.
                </td>
              </tr>
            )}
            {runs.map((r) => (
              <tr key={r.id} className="border-t border-white/5 align-top">
                <td className="py-2">{r.id}</td>
                <td>
                  {r.started_at
                    ? new Date(r.started_at).toLocaleString()
                    : "—"}
                </td>
                <td>{formatDuration(r.duration_ms)}</td>
                <td>{formatBytes(r.bytes_copied)}</td>
                <td>
                  <span className="text-xs text-muted">
                    {r.triggered_by === "scheduled" ? "по расписанию" : "вручную"}
                  </span>
                </td>
                <td>
                  {r.status === "done" && (
                    <span className="text-good">успешно</span>
                  )}
                  {r.status === "running" && (
                    <span className="text-accent">идёт</span>
                  )}
                  {r.status === "failed" && (
                    <span className="text-bad" title={r.error_message || ""}>
                      ошибка
                    </span>
                  )}
                  {r.status === "cancelled" && (
                    <span className="text-muted">отменено</span>
                  )}
                </td>
                <td>
                  <button
                    className="text-xs underline text-muted hover:text-white"
                    onClick={() => toggleProtect(r)}
                    title="Защищённую запись нельзя случайно удалить"
                  >
                    {r.protected ? "🔒 защищена" : "🔓 без защиты"}
                  </button>
                </td>
                <td className="text-right">
                  <button
                    className="btn-danger"
                    disabled={r.protected || r.status === "running"}
                    onClick={() => removeRun(r)}
                    title={
                      r.protected
                        ? "Снимите защиту, чтобы удалить"
                        : "Удалить файл и запись"
                    }
                  >
                    Удалить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card text-xs text-muted">
        <div className="font-semibold text-white mb-1">Защита от случайных удалений</div>
        <ul className="list-disc pl-5 space-y-1">
          <li>Новые реплики создаются с включённой защитой (🔒). Удалить такую запись нельзя.</li>
          <li>Чтобы удалить — сначала снимите защиту, а затем подтвердите удаление в диалоге.</li>
          <li>Последнюю успешную реплику удалить нельзя — на диске всегда остаётся хотя бы одна копия.</li>
          <li>При удалении сервер требует токен подтверждения (<code>{confirmToken}</code>), который UI отправляет автоматически.</li>
        </ul>
      </div>
    </div>
  );
}

function Stat({ title, value }) {
  return (
    <div className="card">
      <div className="label">{title}</div>
      <div className="text-xl font-semibold mt-1 truncate">{value}</div>
    </div>
  );
}
