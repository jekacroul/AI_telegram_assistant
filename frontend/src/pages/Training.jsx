import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";

function formatEta(seconds) {
  if (!seconds || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

const RU_MONTHS = [
  "янв", "фев", "мар", "апр", "май", "июн",
  "июл", "авг", "сен", "окт", "ноя", "дек",
];

function formatExportDate(d) {
  if (!d) return "—";
  const dt = new Date(d);
  if (isNaN(dt.getTime())) return d;
  return `${RU_MONTHS[dt.getMonth()]} ${dt.getFullYear()}`;
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

const PHASE_LABELS = {
  starting: "запуск...",
  stopping_llama_server: "выгрузка llama-server",
  loading_tokenizer: "загрузка токенизатора",
  loading_model: "загрузка модели",
  model_loaded: "модель загружена",
  preparing_trainer: "подготовка тренера",
  training: "обучение",
  merging_and_exporting_gguf: "экспорт GGUF",
  loading_base: "загрузка fp16 базы",
  loading_adapter: "загрузка адаптера",
  merging: "слияние весов",
  saving_merged: "сохранение модели",
  converting_to_gguf: "конвертация в GGUF",
  quantizing: "квантизация",
  converting_lora_to_gguf: "конвертация LoRA в GGUF",
  copying_to_lm_studio: "копирование в LM Studio",
  done: "успех",
  error: "ошибка",
  cancelled: "отменено",
};

export default function Training() {
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
  const [serverStatus, setServerStatus] = useState(null);
  const [ragStatus, setRagStatus] = useState(null);
  const [botEnabled, setBotEnabled] = useState(true);
  const [botWeight, setBotWeight] = useState(30);
  const [exportWeight, setExportWeight] = useState(70);
  const [totalMax, setTotalMax] = useState(10000);
  const [exportItems, setExportItems] = useState([
    { id: 1, path: "", status: "idle", error: "", preview: null },
  ]);
  const [cachedExports, setCachedExports] = useState([]);
  const [trainSource, setTrainSource] = useState("auto");
  const [ragSearch, setRagSearch] = useState({
    open: false,
    query: "",
    results: null,
    busy: false,
    error: "",
  });

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
      setRagStatus(await api.ragStatus());
    } catch {
      // ignore
    }
    try {
      const srv = await api.llamaServerStatus();
      setServerStatus(srv);
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
    const srvPoll = setInterval(async () => {
      try {
        const srv = await api.llamaServerStatus();
        setServerStatus(srv);
      } catch {
        // ignore
      }
    }, 3000);
    return () => {
      ev.close();
      ragEv.close();
      clearInterval(srvPoll);
    };
  }, []);

  async function serverStart() {
    setError("");
    try {
      const res = await api.llamaServerStart();
      if (!res.started) setError(res.reason || "не удалось запустить");
    } catch (e) {
      setError(e.message);
    }
  }

  async function serverStop() {
    setError("");
    try {
      await api.llamaServerStop();
    } catch (e) {
      setError(e.message);
    }
  }

  async function serverRestart() {
    setError("");
    try {
      await api.llamaServerRestart();
    } catch (e) {
      setError(e.message);
    }
  }

  async function setServerAutoResume(enabled) {
    setServerStatus((s) => (s ? { ...s, auto_resume: enabled } : s));
    try {
      await api.llamaServerSetAutoResume(enabled);
    } catch (e) {
      setError(e.message);
      setServerStatus((s) => (s ? { ...s, auto_resume: !enabled } : s));
    }
  }

  async function reindexRag() {
    setError("");
    try {
      const res = await api.ragIndexAll();
      if (!res.started) {
        setError(res.reason || "не удалось запустить индексацию");
        return;
      }
      // Optimistically flip the UI into "running" so the progress bar
      // appears immediately; the SSE stream takes over from here.
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
          error: v.error || "Невалидный файл",
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
      if (!res.started) setError(res.reason || "не удалось запустить");
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
        setError(res.reason || "не удалось запустить конвертацию");
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
        setError(res.reason || "не удалось запустить конвертацию");
        return;
      }
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  async function removeRun(run) {
    const confirmed = window.confirm(`Удалить v${run.version} из истории?`);
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
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat title="Сообщений" value={status?.messages_collected ?? "—"} />
        <Stat title="Пар для обучения" value={status?.training_pairs ?? "—"} />
        <Stat
          title="Последний запуск"
          value={
            status?.last_run?.finished_at
              ? new Date(status.last_run.finished_at).toLocaleString()
              : status?.last_run?.status || "—"
          }
        />
        <Stat
          title="Активный адаптер"
          value={status?.active_adapter ? `v${status.active_adapter.version}` : "—"}
        />
      </div>

      {voiceStats && (
        <div className="card">
          <div className="label">Голосовые сообщения</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2 text-sm">
            <div>
              <div className="text-muted text-xs">Получено</div>
              <div className="text-lg font-semibold">{voiceStats.voice_received}</div>
            </div>
            <div>
              <div className="text-muted text-xs">Транскрибировано</div>
              <div className="text-lg font-semibold">{voiceStats.voice_transcribed}</div>
            </div>
            <div>
              <div className="text-muted text-xs">Низкая уверенность</div>
              <div className="text-lg font-semibold">{voiceStats.voice_low_confidence}</div>
            </div>
            <div>
              <div className="text-muted text-xs">Средняя уверенность</div>
              <div className="text-lg font-semibold">
                {voiceStats.avg_confidence != null
                  ? `${Math.round((voiceStats.avg_confidence || 0) * 100)}%`
                  : "—"}
              </div>
            </div>
          </div>
        </div>
      )}

      {ragStatus && (
        <div className="card">
          <div className="flex flex-wrap items-center gap-3">
            <div className="label">Векторная память</div>
            <span
              className={`text-sm ${
                ragStatus.enabled ? "text-good" : "text-muted"
              }`}
            >
              {ragStatus.enabled ? "✅ Активна" : "❌ Выключена"}
            </span>
            {ragStatus.available && (
              <span className="text-xs text-muted">
                {ragStatus.model} · {ragStatus.device}
              </span>
            )}
          </div>

          {ragStatus.last_error && !ragStatus.available && (
            <div className="text-bad text-xs mt-2">
              Ошибка: {ragStatus.last_error}
            </div>
          )}

          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mt-3 text-sm">
            <div>
              <div className="text-muted text-xs">Проиндексировано</div>
              <div className="text-lg font-semibold">
                {(ragStatus.total_indexed ?? 0).toLocaleString("ru-RU")}
              </div>
            </div>
            <div>
              <div className="text-muted text-xs">Размер</div>
              <div className="text-lg font-semibold">
                {ragStatus.collection_size_mb ?? 0} MB
              </div>
            </div>
            <div>
              <div className="text-muted text-xs">Последняя индексация</div>
              <div className="text-lg font-semibold">
                {ragStatus.last_indexed_at
                  ? new Date(ragStatus.last_indexed_at).toLocaleString()
                  : "—"}
              </div>
            </div>
          </div>

          {ragStatus.indexing?.running && (
            <div className="mt-3">
              <div className="flex flex-wrap justify-between gap-2 text-xs text-muted">
                <span>
                  Индексирую сообщения: {ragStatus.indexing.indexed}/
                  {ragStatus.indexing.total || "—"}
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
                <div className="mt-1 h-2 bg-white/10 rounded">
                  <div
                    className="h-2 bg-accent rounded transition-all"
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
              Переиндексировать всё
            </button>
            <button
              className="btn-secondary"
              onClick={() =>
                setRagSearch((s) => ({ ...s, open: !s.open }))
              }
              disabled={!ragStatus.available}
            >
              {ragSearch.open ? "Скрыть тест поиска" : "Тест поиска"}
            </button>
          </div>

          {ragSearch.open && (
            <div className="mt-4 rounded-lg border border-white/10 bg-black/30 p-3">
              <div className="flex gap-2">
                <input
                  className="input flex-1"
                  placeholder="Введите фразу для поиска"
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
                  {ragSearch.busy ? "..." : "Найти"}
                </button>
              </div>
              {ragSearch.error && (
                <div className="text-bad text-sm mt-2">{ragSearch.error}</div>
              )}
              {ragSearch.results && ragSearch.results.length === 0 && (
                <div className="text-muted text-sm mt-2">
                  Ничего не найдено.
                </div>
              )}
              {ragSearch.results && ragSearch.results.length > 0 && (
                <div className="mt-3 space-y-2">
                  {ragSearch.results.map((r, i) => (
                    <div
                      key={i}
                      className="rounded-lg border border-white/10 p-2 text-sm"
                    >
                      <div className="flex justify-between gap-2 text-xs text-muted">
                        <span>
                          {r.sender_name || r.chat_name || "—"}
                          {r.chat_name && r.sender_name
                            ? ` · ${r.chat_name}`
                            : ""}
                        </span>
                        <span className="rounded-full bg-accent/20 text-accent px-2">
                          {r.similarity_score?.toFixed?.(2) ?? "—"}
                        </span>
                      </div>
                      <div className="mt-1 text-white/90">{r.text}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {serverStatus && (
        <div className="card">
          <div className="flex flex-wrap items-center gap-3">
            <div className="label">Llama Server</div>
            <span
              className={
                serverStatus.starting
                  ? "text-accent text-sm"
                  : serverStatus.stopping
                  ? "text-muted text-sm"
                  : serverStatus.running
                  ? "text-good text-sm"
                  : "text-muted text-sm"
              }
            >
              {serverStatus.starting
                ? "● запуск..."
                : serverStatus.stopping
                ? "● останавливается..."
                : serverStatus.running
                ? `● работает на :${serverStatus.port}`
                : "○ остановлен"}
            </span>
            {serverStatus.running && serverStatus.lora_path && (
              <span className="text-xs text-muted">
                LoRA: <code>{serverStatus.lora_path.split(/[\\/]/).pop()}</code>
              </span>
            )}
            {serverStatus.running && !serverStatus.lora_path && (
              <span className="text-xs text-muted">без адаптера (чистая база)</span>
            )}
            <div className="ml-auto flex gap-2">
              {!serverStatus.running && !serverStatus.starting && (
                <button className="btn-secondary" onClick={serverStart}>
                  Запустить
                </button>
              )}
              {serverStatus.running && (
                <button className="btn-secondary" onClick={serverRestart}>
                  Перезапустить
                </button>
              )}
              {serverStatus.running && (
                <button className="btn-danger" onClick={serverStop}>
                  Остановить
                </button>
              )}
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted mt-3 select-none">
            <input
              type="checkbox"
              checked={!!serverStatus.auto_resume}
              onChange={(e) => setServerAutoResume(e.target.checked)}
            />
            <span>
              Авто-подъём после обучения и Merge → GGUF (если был запущен до)
            </span>
          </label>
          {!serverStatus.configured && (
            <div className="text-bad text-xs mt-2">
              Не задан LLAMA_BASE_MODEL_GGUF в .env — сервер запустить нельзя.
            </div>
          )}
          {serverStatus.last_error && !serverStatus.running && (
            <div className="text-bad text-xs mt-2">
              Ошибка: {serverStatus.last_error}
            </div>
          )}
        </div>
      )}

      <div className="card">
        <div className="label">Отклонено фильтром качества</div>
        <div className="text-xl font-semibold mt-1">
          {qualityStats
            ? `${qualityStats.total_rejected} из ${qualityStats.total_generated}`
            : "—"}
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-sm">
          {qualityStats?.reasons?.length ? (
            qualityStats.reasons.map((item) => (
              <span
                key={item.reason}
                className="rounded-full bg-white/10 px-3 py-1 text-muted"
              >
                {reasonLabel(item.reason)}: {item.count}
              </span>
            ))
          ) : (
            <span className="text-muted">Нет отклонений</span>
          )}
        </div>
      </div>

      <div className="card space-y-4">
        <div className="label">Источники данных</div>

        <div className="rounded-lg border border-white/10 p-3 space-y-2">
          <div className="flex items-center gap-3">
            <span className="font-semibold">🤖 Пары из бота</span>
            <label className="flex items-center gap-2 text-sm text-muted ml-auto select-none">
              <input
                type="checkbox"
                checked={botEnabled}
                onChange={(e) => setBotEnabled(e.target.checked)}
              />
              включить
            </label>
          </div>
          <div className="text-sm text-muted">
            {(status?.training_pairs || 0).toLocaleString("ru-RU")} пар собрано
            ботом
          </div>
          {botEnabled && (
            <div>
              <div className="flex justify-between text-xs text-muted">
                <span>Вес</span>
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

        <div className="rounded-lg border border-white/10 p-3 space-y-3">
          <div className="font-semibold">📱 Экспорт Telegram</div>
          <div className="text-xs text-muted whitespace-pre-line bg-black/30 rounded p-2">
            {`Как экспортировать историю:
 1. Открой Telegram Desktop
 2. Настройки → Конфиденциальность и безопасность
 3. Экспорт данных Telegram
 4. Выбери: ☑ Личные сообщения, формат JSON
 5. Нажми Экспорт`}
          </div>

          {exportItems.map((item) => (
            <div key={item.id} className="space-y-2">
              <div className="flex gap-2">
                <input
                  className="input flex-1"
                  placeholder="Путь к result.json"
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
                  {item.status === "checking" ? "..." : "Проверить файл"}
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
                  <div className="text-good">✅ Файл успешно проверен</div>
                  <div>
                    📊 Найдено:{" "}
                    {(item.preview.total_pairs || 0).toLocaleString("ru-RU")} пар
                  </div>
                  <div>
                    📅 Период:{" "}
                    {formatExportDate(item.preview.date_range?.from)}–
                    {formatExportDate(item.preview.date_range?.to)}
                  </div>
                  <div>💬 Чатов: {item.preview.chats_count}</div>
                  <div>
                    📝 Средний ответ: {item.preview.avg_reply_length} слов
                  </div>
                  {item.preview.top_chats?.length > 0 && (
                    <div className="pt-1">
                      <div className="text-muted">Топ чаты:</div>
                      {item.preview.top_chats.map((c, i) => (
                        <div key={i}>
                          • {c.name} — {c.pairs} пар
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}

          <button className="btn-secondary" onClick={addExportItem}>
            + Добавить файл
          </button>

          <div>
            <div className="flex justify-between text-xs text-muted">
              <span>Вес экспорта</span>
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
              Ранее проверенные:
              <div className="flex flex-wrap gap-2 mt-1">
                {cachedExports.map((c, i) => (
                  <button
                    key={i}
                    className="rounded-full bg-white/10 px-2 py-0.5 hover:bg-white/20"
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

        <div className="rounded-lg border border-white/10 p-3 space-y-2">
          <div className="text-sm text-muted">Итого будет использовано:</div>
          <div className="space-y-1 text-sm">
            <div className="flex items-center gap-2">
              <div className="flex-1 h-3 bg-white/10 rounded overflow-hidden">
                <div
                  className="h-3 bg-accent transition-all"
                  style={{ width: `${combined.botPct}%` }}
                />
              </div>
              <span className="w-48 text-right">
                Из бота: {combined.bot} пар ({combined.botPct}%)
              </span>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex-1 h-3 bg-white/10 rounded overflow-hidden">
                <div
                  className="h-3 bg-good transition-all"
                  style={{ width: `${combined.exportPct}%` }}
                />
              </div>
              <span className="w-48 text-right">
                Экспорт: {combined.export} пар ({combined.exportPct}%)
              </span>
            </div>
          </div>
          <div className="border-t border-white/10 pt-2 font-semibold">
            Всего: ~{combined.total.toLocaleString("ru-RU")} пар
          </div>
          {lowBotShare && (
            <div className="text-bad text-sm">
              ⚠️ Мало пар из бота — твой текущий стиль может быть
              недопредставлен
            </div>
          )}
          <label className="flex items-center gap-2 text-xs text-muted">
            <span>Максимум примеров:</span>
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

      <div className="card flex flex-wrap gap-2 items-center">
        <button className="btn-secondary" onClick={build} disabled={busy}>
          {busy
            ? "Сборка..."
            : `Собрать датасет (${combined.total.toLocaleString("ru-RU")} пар)`}
        </button>
        <select
          className="input"
          value={trainSource}
          onChange={(e) => setTrainSource(e.target.value)}
          disabled={status?.running}
          title="Какие данные использовать для обучения"
        >
          <option value="auto">Обучать на: последняя сборка</option>
          <option value="bot">Обучать на: только пары бота</option>
        </select>
        <button
          className="btn-primary"
          disabled={!canTrain || busy}
          onClick={start}
          title={!canTrain ? "Нужно минимум 50 пар" : ""}
        >
          {status?.running ? "Идёт обучение..." : "Запустить fine-tuning"}
        </button>
        {status?.running && (
          <button className="btn-danger" onClick={cancel}>
            Отменить
          </button>
        )}
        {status?.active_adapter && (
          <button className="btn-secondary" onClick={deactivate}>
            Деактивировать адаптер
          </button>
        )}
        {datasetInfo && datasetInfo.total_pairs > 0 && (
          <span className="text-sm text-muted ml-2">
            Датасет: {datasetInfo.total_pairs} пар
            {datasetInfo.breakdown
              ? ` (бот ${datasetInfo.breakdown.bot}, экспорт ${datasetInfo.breakdown.export})`
              : datasetInfo.avg_output_len
                ? `, средняя длина ответа ${datasetInfo.avg_output_len}`
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
        <div className="card">
          <div className="flex flex-wrap gap-4 text-sm items-center">
            <span>
              Этап:{" "}
              <b>{PHASE_LABELS[progress.phase] || progress.phase}</b>
            </span>
            {progress.phase === "training" && (
              <>
                <span>Эпоха: {progress.epoch?.toFixed?.(2) ?? "—"}</span>
                <span>
                  Шаг: {progress.step ?? "—"} / {progress.max_steps ?? "—"}
                </span>
                <span>Loss: {progress.loss?.toFixed?.(4) ?? "—"}</span>
                <span>ETA: {formatEta(progress.eta_seconds)}</span>
                {percent != null && <span>{percent.toFixed(1)}%</span>}
              </>
            )}
            {progress.phase === "done" && progress.final_loss != null && (
              <span>Итоговый loss: <b>{progress.final_loss.toFixed(4)}</b></span>
            )}
            {progress.phase === "done" && progress.gguf_path && (
              <span className="text-good">
                GGUF: <code className="text-xs">{progress.gguf_path}</code>
                {progress.gguf_path.toLowerCase().includes(".lora.") && (
                  <span className="text-muted ml-2">
                    (LoRA-адаптер — в LM Studio подключай поверх базы через
                    Advanced → LoRA Adapters, не загружай как отдельную модель)
                  </span>
                )}
              </span>
            )}
            {progress.phase === "done" && progress.gguf_path === null && (
              <span className="text-bad">
                GGUF не сконвертирован: {progress.gguf_skip_reason || "настрой LLAMA_CPP_PATH в .env"}
              </span>
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
          {percent != null && (
            <div className="mt-3 h-2 bg-white/10 rounded">
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

      <div className="card">
        <div className="text-sm text-muted mb-2">История адаптеров</div>
        <table className="w-full text-sm">
          <thead className="text-muted">
            <tr className="text-left">
              <th className="py-1">Версия</th>
              <th>Дата</th>
              <th>Пар</th>
              <th>Loss</th>
              <th>Статус</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan="6" className="text-muted py-3">
                  Запусков ещё не было.
                </td>
              </tr>
            )}
            {runs.map((r) => (
              <React.Fragment key={r.id}>
                <tr className="border-t border-white/5">
                  <td className="py-2">v{r.version}</td>
                  <td>
                    {r.started_at ? new Date(r.started_at).toLocaleString() : "—"}
                  </td>
                  <td>{r.pair_count}</td>
                  <td>{r.final_loss?.toFixed?.(4) ?? "—"}</td>
                  <td>
                    {r.is_active ? (
                      <span className="text-good">активный</span>
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
                          Активировать
                        </button>
                      )}
                      {r.is_active && (
                        <button className="btn-secondary" onClick={deactivate}>
                          Деактивировать
                        </button>
                      )}
                      {r.status === "done" && (
                        <button
                          className="btn-secondary"
                          disabled={status?.running}
                          onClick={() => exportLoraGguf(r)}
                          title={
                            status?.running
                              ? "Дождись окончания текущего процесса"
                              : "Сконвертировать LoRA в отдельный GGUF (~50-200 МБ). В LM Studio открой базовую модель → Load → Advanced → LoRA Adapters → добавь этот файл. Как самостоятельная модель НЕ загружается."
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
                              ? "Дождись окончания текущего процесса"
                              : "Слить адаптер с базой fp16 и собрать единый GGUF (~12 ГБ, требует ~26 ГБ свободной RAM)"
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
                            ? "Загрузка..."
                            : runLogs[r.id]?.open
                              ? "Скрыть лог"
                              : "Лог"}
                        </button>
                      )}
                      {(r.status === "failed" || r.status === "cancelled") &&
                        !r.is_active && (
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
                {runLogs[r.id]?.open && (
                  <tr className="border-t border-white/5">
                    <td colSpan="6" className="pb-3">
                      <div className="mt-2 rounded-lg border border-white/10 bg-black/30 p-3">
                        <div className="flex flex-wrap gap-2 items-center text-xs text-muted mb-2">
                          <span>Лог запуска v{r.version}</span>
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
                            {r.status === "failed" ? "Ошибка: " : "Последняя строка: "}
                            {runLogs[r.id].data.error}
                          </div>
                        )}
                        {runLogs[r.id].data?.excerpt ? (
                          <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs text-white/90">
                            {runLogs[r.id].data.excerpt}
                          </pre>
                        ) : (
                          <div className="text-muted text-sm">
                            Не удалось найти лог запуска.
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
    </div>
  );
}

function reasonLabel(reason) {
  const labels = {
    too_short: "Слишком короткий ответ",
    language_mismatch: "Другой язык",
    identical_to_incoming: "Повтор входящего",
    ai_phrase: "AI-фраза",
    emoji_only: "Только emoji",
    no_variants: "Нет вариантов",
  };
  return labels[reason] || reason;
}

function Stat({ title, value }) {
  return (
    <div className="card">
      <div className="label">{title}</div>
      <div className="text-xl font-semibold mt-1 truncate">{value}</div>
    </div>
  );
}
