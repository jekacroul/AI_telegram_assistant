import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api.js";

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString();
}

function formatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function Dialogs() {
  const [chats, setChats] = useState([]);
  const [settings, setSettings] = useState({
    excluded_chats: [],
    interval_minutes: 1440,
    last_run_at: null,
  });
  const [selectedChatId, setSelectedChatId] = useState(null);
  const [versions, setVersions] = useState([]);
  const [selectedBackup, setSelectedBackup] = useState(null);
  const [backupData, setBackupData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [intervalDraft, setIntervalDraft] = useState("1440");
  const [savingInterval, setSavingInterval] = useState(false);
  const [intervalSaved, setIntervalSaved] = useState(false);
  const [mediaPreview, setMediaPreview] = useState(null);

  const excludedSet = useMemo(
    () => new Set((settings.excluded_chats || []).map((x) => Number(x))),
    [settings.excluded_chats]
  );

  const loadChats = useCallback(async () => {
    try {
      const [c, s] = await Promise.all([api.dialogsChats(), api.dialogsSettings()]);
      setChats(c);
      setSettings(s);
      setIntervalDraft(String(s.interval_minutes ?? 1440));
    } catch (e) {
      setError(e.message || String(e));
    }
  }, []);

  const loadVersions = useCallback(async (chatId) => {
    if (chatId == null) {
      setVersions([]);
      setSelectedBackup(null);
      setBackupData(null);
      return;
    }
    setLoading(true);
    try {
      const v = await api.dialogsVersions(chatId);
      setVersions(v);
      if (v.length > 0) {
        const top = v[0];
        setSelectedBackup(top.id);
        const data = await api.dialogsBackup(top.id);
        setBackupData(data);
      } else {
        setSelectedBackup(null);
        setBackupData(null);
      }
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadChats();
  }, [loadChats]);

  useEffect(() => {
    loadVersions(selectedChatId);
  }, [selectedChatId, loadVersions]);

  async function selectBackup(id) {
    setSelectedBackup(id);
    setLoading(true);
    try {
      const data = await api.dialogsBackup(id);
      setBackupData(data);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  async function toggleExcluded(chatId) {
    const set = new Set(excludedSet);
    if (set.has(Number(chatId))) set.delete(Number(chatId));
    else set.add(Number(chatId));
    const next = Array.from(set);
    setSettings((s) => ({ ...s, excluded_chats: next }));
    try {
      await api.saveDialogsSettings({ excluded_chats: next });
      loadChats();
    } catch (e) {
      setError(e.message || String(e));
    }
  }

  async function runBackup() {
    setBusy(true);
    setError("");
    try {
      await api.dialogsRunBackup();
      await loadChats();
      if (selectedChatId != null) await loadVersions(selectedChatId);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveInterval() {
    const parsed = parseInt(intervalDraft, 10);
    if (!Number.isFinite(parsed) || parsed < 1) {
      setError("Интервал должен быть целым числом ≥ 1");
      return;
    }
    setSavingInterval(true);
    setError("");
    try {
      await api.saveDialogsSettings({ interval_minutes: parsed });
      setSettings((s) => ({ ...s, interval_minutes: parsed }));
      setIntervalDraft(String(parsed));
      setIntervalSaved(true);
      setTimeout(() => setIntervalSaved(false), 1500);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setSavingInterval(false);
    }
  }

  async function deleteSelectedBackup() {
    if (!selectedBackup) return;
    const version = versions.find((v) => v.id === selectedBackup);
    const label = version ? `v${version.version}` : "выбранную версию";
    if (!window.confirm(`Удалить ${label} резервной копии?`)) return;
    setBusy(true);
    setError("");
    try {
      await api.deleteDialogsBackup(selectedBackup);
      await loadChats();
      if (selectedChatId != null) await loadVersions(selectedChatId);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function deleteChatHistory() {
    if (selectedChatId == null || !selectedChat) return;
    const name = selectedChat.chat_name || `chat ${selectedChat.chat_id}`;
    if (
      !window.confirm(
        `Удалить всю историю и чат «${name}»? Будут удалены все сообщения и резервные копии. Это действие нельзя отменить.`
      )
    ) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.deleteDialogsHistory(selectedChatId);
      setSelectedChatId(null);
      setVersions([]);
      setSelectedBackup(null);
      setBackupData(null);
      await loadChats();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  function exportSelectedBackup() {
    if (!selectedBackup) return;
    window.location.href = api.dialogsBackupExportUrl(selectedBackup);
  }

  const intervalDirty =
    String(settings.interval_minutes ?? "") !== intervalDraft.trim();

  const selectedChat = chats.find((c) => c.chat_id === selectedChatId);
  const selectedVersion = versions.find((v) => v.id === selectedBackup);

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <div className="text-sm">
            Резервные копии диалогов — снэпшоты по версиям.
          </div>
          <div className="text-xs text-muted">
            Последний запуск: {formatDate(settings.last_run_at) || "—"}
          </div>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-xs text-muted">Интервал, мин:</span>
            <input
              type="number"
              min={1}
              className="input w-24 py-1 text-xs"
              value={intervalDraft}
              onChange={(e) => setIntervalDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && intervalDirty) saveInterval();
              }}
            />
            <button
              className="btn-secondary disabled:opacity-50"
              onClick={saveInterval}
              disabled={!intervalDirty || savingInterval}
            >
              {savingInterval ? "Сохраняю…" : "Сохранить"}
            </button>
            {intervalSaved && (
              <span className="text-xs text-good">Сохранено</span>
            )}
            {intervalDirty && !savingInterval && !intervalSaved && (
              <span className="text-xs text-muted">не сохранено</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={runBackup}
            disabled={busy}
            className="btn-primary disabled:opacity-50"
          >
            {busy ? "Идёт бэкап…" : "Создать копии сейчас"}
          </button>
        </div>
      </div>

      {error && (
        <div className="card border-bad/40 text-sm text-bad">{error}</div>
      )}

      <div className="grid grid-cols-12 gap-3 h-[70vh]">
        <div className="col-span-4 card overflow-y-auto p-0">
          <div className="px-3 py-2 text-xs text-muted border-b border-white/5 sticky top-0 bg-panel">
            Чаты ({chats.length})
          </div>
          {chats.length === 0 && (
            <div className="p-4 text-sm text-muted">
              Сообщений ещё нет — собирай чаты через бота.
            </div>
          )}
          {chats.map((c) => {
            const isExcluded = excludedSet.has(Number(c.chat_id));
            const isActive = c.chat_id === selectedChatId;
            return (
              <div
                key={c.chat_id}
                className={`flex items-center gap-2 px-3 py-2 cursor-pointer border-b border-white/5 ${
                  isActive ? "bg-accent/20" : "hover:bg-white/5"
                }`}
                onClick={() => setSelectedChatId(c.chat_id)}
              >
                <div className="flex-1 min-w-0">
                  <div className="text-sm truncate">
                    {c.chat_name || `chat ${c.chat_id}`}
                  </div>
                  <div className="text-xs text-muted truncate">
                    {c.message_count} сообщ. ·{" "}
                    {c.versions > 0
                      ? `v${c.latest_version} (${c.versions} верс.)`
                      : "нет копий"}
                  </div>
                </div>
                <button
                  className={`text-xs px-2 py-1 rounded ${
                    isExcluded
                      ? "bg-bad/30 text-bad"
                      : "bg-white/5 text-muted hover:text-white"
                  }`}
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleExcluded(c.chat_id);
                  }}
                  title={
                    isExcluded
                      ? "Чат в исключениях — не бэкапить"
                      : "Исключить из бэкапа"
                  }
                >
                  {isExcluded ? "искл." : "вкл."}
                </button>
              </div>
            );
          })}
        </div>

        <div className="col-span-8 card flex flex-col p-0 overflow-hidden">
          {selectedChat ? (
            <>
              <div className="px-4 py-2 border-b border-white/5 flex flex-wrap items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="text-sm truncate">
                    {selectedChat.chat_name || `chat ${selectedChat.chat_id}`}
                  </div>
                  <div className="text-xs text-muted">
                    chat_id: {selectedChat.chat_id}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-xs text-muted">Версия</label>
                  <select
                    className="input flex-none py-1 text-sm"
                    style={{ width: "5rem" }}
                    value={selectedBackup || ""}
                    onChange={(e) => selectBackup(Number(e.target.value))}
                    disabled={versions.length === 0 || busy}
                    title={
                      selectedVersion ? formatDate(selectedVersion.created_at) : ""
                    }
                  >
                    {versions.length === 0 && (
                      <option value="">нет копий</option>
                    )}
                    {versions.map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.version}
                      </option>
                    ))}
                  </select>
                  <button
                    className="btn-secondary disabled:opacity-50"
                    onClick={exportSelectedBackup}
                    disabled={!selectedBackup || busy}
                  >
                    Скачать TXT
                  </button>
                  <button
                    className="btn-secondary disabled:opacity-50"
                    onClick={deleteSelectedBackup}
                    disabled={!selectedBackup || busy}
                    title="Удалить выбранную версию резервной копии"
                  >
                    Удалить версию
                  </button>
                  <button
                    className="btn-secondary text-bad disabled:opacity-50"
                    onClick={deleteChatHistory}
                    disabled={busy}
                    title="Удалить все сообщения и все версии резервных копий этого чата"
                  >
                    Удалить историю
                  </button>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto px-4 py-3 space-y-1 bg-bg/50">
                {loading && (
                  <div className="text-xs text-muted">Загрузка…</div>
                )}
                {!loading && backupData && backupData.messages.length === 0 && (
                  <div className="text-xs text-muted">
                    В этой копии нет сообщений.
                  </div>
                )}
                {!loading && !backupData && versions.length === 0 && (
                  <div className="text-xs text-muted">
                    Для этого чата ещё нет резервных копий. Нажми «Создать копии
                    сейчас».
                  </div>
                )}
                {backupData &&
                  backupData.messages.map((m) => (
                    <div
                      key={m.id}
                      className={`flex ${
                        m.is_mine ? "justify-end" : "justify-start"
                      }`}
                    >
                      <div
                        className={`max-w-[70%] rounded-lg px-3 py-2 text-sm ${
                          m.is_mine
                            ? "bg-accent/80 text-white"
                            : "bg-panel border border-white/10 text-white"
                        }`}
                      >
                        {!m.is_mine && (
                          <div className="text-[10px] text-muted mb-0.5">
                            {m.sender_name || "собеседник"}
                          </div>
                        )}
                        {m.text && (
                          <div className="whitespace-pre-wrap break-words">
                            {m.text}
                          </div>
                        )}
                        {m.media_type === "photo" && m.media_path && (
                          <button className="mt-2 block" onClick={() => setMediaPreview(m)}>
                            <img
                              src={m.media_path}
                              alt="backup photo"
                              className="max-h-44 rounded-md border border-white/10 object-cover"
                            />
                          </button>
                        )}
                        {(m.media_type === "video" || m.media_type === "video_note") &&
                          m.media_path && (
                            <button className="mt-2 block" onClick={() => setMediaPreview(m)}>
                              <video
                                src={m.media_path}
                                className="max-h-44 rounded-md border border-white/10"
                              />
                            </button>
                          )}
                        {m.media_private && !m.media_path && (
                          <div className="mt-2 text-[11px] text-white/70">
                            Приватное {m.media_type === "photo" ? "фото" : "видео"} (одноразовое)
                          </div>
                        )}
                        <div className="text-[10px] text-white/60 mt-1 text-right">
                          {formatTime(m.timestamp)}
                        </div>
                      </div>
                    </div>
                  ))}
              </div>
            </>
          ) : (
            <div className="p-6 text-sm text-muted">
              Выбери чат слева, чтобы увидеть его резервную копию.
            </div>
          )}
        </div>
      </div>
      {mediaPreview?.media_path && (
        <div
          className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center p-4"
          onClick={() => setMediaPreview(null)}
        >
          {mediaPreview.media_type === "photo" ? (
            <img
              src={mediaPreview.media_path}
              alt="fullscreen media"
              className="max-w-full max-h-full object-contain"
            />
          ) : (
            <video
              src={mediaPreview.media_path}
              controls
              autoPlay
              className="max-w-full max-h-full"
            />
          )}
        </div>
      )}
    </div>
  );
}
