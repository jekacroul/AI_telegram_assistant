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
    interval_hours: 24,
    last_run_at: null,
  });
  const [selectedChatId, setSelectedChatId] = useState(null);
  const [versions, setVersions] = useState([]);
  const [selectedBackup, setSelectedBackup] = useState(null);
  const [backupData, setBackupData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const excludedSet = useMemo(
    () => new Set((settings.excluded_chats || []).map((x) => Number(x))),
    [settings.excluded_chats]
  );

  const loadChats = useCallback(async () => {
    try {
      const [c, s] = await Promise.all([api.dialogsChats(), api.dialogsSettings()]);
      setChats(c);
      setSettings(s);
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

  async function saveInterval(hours) {
    const value = Math.max(1, parseInt(hours, 10) || 24);
    setSettings((s) => ({ ...s, interval_hours: value }));
    try {
      await api.saveDialogsSettings({ interval_hours: value });
    } catch (e) {
      setError(e.message || String(e));
    }
  }

  const selectedChat = chats.find((c) => c.chat_id === selectedChatId);

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col">
          <div className="text-sm">
            Резервные копии диалогов — снэпшоты по версиям.
          </div>
          <div className="text-xs text-muted">
            Последний запуск: {formatDate(settings.last_run_at) || "—"} ·
            интервал:{" "}
            <input
              type="number"
              min={1}
              className="input inline-block w-20 ml-1 mr-1 py-0.5 text-xs"
              defaultValue={settings.interval_hours}
              onBlur={(e) => saveInterval(e.target.value)}
            />{" "}
            ч
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
                    className="input py-1 text-sm w-32"
                    value={selectedBackup || ""}
                    onChange={(e) => selectBackup(Number(e.target.value))}
                    disabled={versions.length === 0}
                  >
                    {versions.length === 0 && (
                      <option value="">нет копий</option>
                    )}
                    {versions.map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.version} · {formatDate(v.created_at)}
                      </option>
                    ))}
                  </select>
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
                        <div className="whitespace-pre-wrap break-words">
                          {m.text}
                        </div>
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
    </div>
  );
}
