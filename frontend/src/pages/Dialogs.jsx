import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api } from "../lib/api.js";
import { MessagesSquare } from "lucide-react";
import { useLang } from "../hooks/useLang.js";
import Page from "../components/Page.jsx";
import Tile from "../components/Tile.jsx";

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
function isMediaPlaceholder(text) {
  return ["(фото)", "(видео)", "(кружок)", "(photo)", "(video)"].includes(
    (text || "").trim().toLowerCase()
  );
}

const AVATAR_COLORS = [
  "bg-rose-500",
  "bg-orange-500",
  "bg-amber-500",
  "bg-emerald-500",
  "bg-teal-500",
  "bg-sky-500",
  "bg-indigo-500",
  "bg-violet-500",
  "bg-pink-500",
];

function avatarColor(id) {
  return AVATAR_COLORS[Math.abs(Number(id) || 0) % AVATAR_COLORS.length];
}

function initials(name) {
  const parts = (name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return ((parts[0][0] || "") + (parts[1]?.[0] || "")).toUpperCase();
}

function sameDay(a, b) {
  if (!a || !b) return false;
  return new Date(a).toDateString() === new Date(b).toDateString();
}

function formatDay(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function Avatar({ name, id, size = "w-10 h-10" }) {
  return (
    <div
      className={`${size} ${avatarColor(
        id
      )} rounded-full flex items-center justify-center
         text-white text-sm font-semibold flex-shrink-0 select-none`}
    >
      {initials(name)}
    </div>
  );
}

export default function Dialogs() {
  const { t } = useLang();
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
  const messagesRef = useRef(null);

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

  useEffect(() => {
    const el = messagesRef.current;
    if (el && !loading) el.scrollTop = el.scrollHeight;
  }, [backupData, loading]);

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
      setError(t("dialogs.intervalInvalid"));
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
    const label = version ? `v${version.version}` : t("dialogs.selectedVersion");
    if (!window.confirm(t("dialogs.confirmDeleteBackup", { label }))) return;
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
    if (!window.confirm(t("dialogs.confirmDeleteHistory", { name }))) {
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
    <Page>
      <Tile
        title={t("nav.dialogs")}
        icon={MessagesSquare}
        actions={
          <button
            onClick={runBackup}
            disabled={busy}
            className="btn-primary disabled:opacity-50"
          >
            {busy ? t("dialogs.backupRunning") : t("dialogs.runBackup")}
          </button>
        }
      >
        <div className="flex flex-col gap-1">
          <div className="text-sm text-zinc-700 dark:text-slate-200">
            {t("dialogs.intro")}
          </div>
          <div className="text-xs text-muted">
            {t("dialogs.lastRun", {
              date: formatDate(settings.last_run_at) || t("common.dash"),
            })}
          </div>
          <div className="flex items-center gap-2 mt-2">
            <span className="text-xs text-muted">
              {t("dialogs.intervalMin")}
            </span>
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
              className="btn-ghost disabled:opacity-50"
              onClick={saveInterval}
              disabled={!intervalDirty || savingInterval}
            >
              {savingInterval ? t("common.saving") : t("common.save")}
            </button>
            {intervalSaved && (
              <span className="text-xs text-good">{t("common.saved")}</span>
            )}
            {intervalDirty && !savingInterval && !intervalSaved && (
              <span className="text-xs text-muted">
                {t("dialogs.notSaved")}
              </span>
            )}
          </div>
        </div>
      </Tile>

      {error && (
        <div
          className="tile flex items-center gap-2 text-sm
                     text-rose-600 dark:text-rose-300
                     border-rose-300 dark:border-rose-900/60"
        >
          {error}
        </div>
      )}

      <div className="grid grid-cols-12 gap-3 h-[70vh]">
        <div className="col-span-4 tile overflow-y-auto p-0">
          <div className="px-3 py-2 text-xs text-muted border-b border-line sticky top-0 bg-panel">
            {t("dialogs.chatsCount", { count: chats.length })}
          </div>
          {chats.length === 0 && (
            <div className="p-4 text-sm text-muted">
              {t("dialogs.noMessages")}
            </div>
          )}
          {chats.map((c) => {
            const isExcluded = excludedSet.has(Number(c.chat_id));
            const isActive = c.chat_id === selectedChatId;
            const name = c.chat_name || `chat ${c.chat_id}`;
            return (
              <div
                key={c.chat_id}
                className={`flex items-center gap-2.5 px-3 py-2 cursor-pointer
                  border-b border-line transition-colors ${
                    isActive ? "bg-accent/20" : "hover:bg-surface"
                  }`}
                onClick={() => setSelectedChatId(c.chat_id)}
              >
                <Avatar name={name} id={c.chat_id} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate text-fg">
                    {name}
                  </div>
                  <div className="text-xs text-muted truncate">
                    {c.chat_username && (
                      <span className="text-accent">@{c.chat_username}</span>
                    )}
                    {c.chat_username ? " · " : ""}
                    {t("dialogs.msgsAbbr", { count: c.message_count })}
                    {c.versions > 0 ? ` · v${c.latest_version}` : ""}
                  </div>
                </div>
                <button
                  className={`text-[11px] px-2 py-1 rounded-md flex-shrink-0 ${
                    isExcluded
                      ? "bg-bad/20 text-bad"
                      : "bg-surface text-muted hover:text-fg"
                  }`}
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleExcluded(c.chat_id);
                  }}
                  title={
                    isExcluded
                      ? t("dialogs.excludedTitle")
                      : t("dialogs.excludeTitle")
                  }
                >
                  {isExcluded ? t("dialogs.excluded") : t("dialogs.included")}
                </button>
              </div>
            );
          })}
        </div>

        <div className="col-span-8 tile flex flex-col p-0 overflow-hidden">
          {selectedChat ? (
            <>
              <div className="px-4 py-2.5 border-b border-line flex flex-wrap items-center gap-3">
                <Avatar
                  name={
                    selectedChat.chat_name || `chat ${selectedChat.chat_id}`
                  }
                  id={selectedChat.chat_id}
                  size="w-9 h-9"
                />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold truncate text-fg">
                    {selectedChat.chat_name || `chat ${selectedChat.chat_id}`}
                  </div>
                  <div className="text-xs text-muted truncate">
                    {selectedChat.chat_username
                      ? `@${selectedChat.chat_username}`
                      : `chat_id: ${selectedChat.chat_id}`}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-xs text-muted">
                    {t("dialogs.version")}
                  </label>
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
                      <option value="">{t("dialogs.noBackups")}</option>
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
                    {t("dialogs.downloadTxt")}
                  </button>
                  <button
                    className="btn-secondary disabled:opacity-50"
                    onClick={deleteSelectedBackup}
                    disabled={!selectedBackup || busy}
                    title={t("dialogs.deleteVersionTitle")}
                  >
                    {t("dialogs.deleteVersion")}
                  </button>
                  <button
                    className="btn-secondary text-bad disabled:opacity-50"
                    onClick={deleteChatHistory}
                    disabled={busy}
                    title={t("dialogs.deleteHistoryTitle")}
                  >
                    {t("dialogs.deleteHistory")}
                  </button>
                </div>
              </div>
              <div
                ref={messagesRef}
                className="flex-1 overflow-y-auto px-4 py-3
                           bg-light-bg dark:bg-dark-bg"
              >
                {loading && (
                  <div className="text-xs text-muted">
                    {t("common.loading")}
                  </div>
                )}
                {!loading && backupData && backupData.messages.length === 0 && (
                  <div className="text-xs text-muted">
                    {t("dialogs.emptyBackup")}
                  </div>
                )}
                {!loading && !backupData && versions.length === 0 && (
                  <div className="text-xs text-muted">
                    {t("dialogs.noBackupsHint")}
                  </div>
                )}
                {!loading &&
                  backupData &&
                  backupData.messages.map((m, i) => {
                    const prev = backupData.messages[i - 1];
                    const showDay =
                      !prev || !sameDay(prev.timestamp, m.timestamp);
                    const grouped =
                      prev &&
                      !showDay &&
                      prev.is_mine === m.is_mine &&
                      prev.sender_id === m.sender_id;
                    const hasText =
                      m.text && !(m.media_path && isMediaPlaceholder(m.text));
                    return (
                      <React.Fragment key={m.id}>
                        {showDay && (
                          <div className="flex justify-center my-3">
                            <span
                              className="px-3 py-0.5 rounded-full text-[11px]
                                         text-muted bg-surface border border-line"
                            >
                              {formatDay(m.timestamp)}
                            </span>
                          </div>
                        )}
                        <div
                          className={`flex ${
                            m.is_mine ? "justify-end" : "justify-start"
                          } ${grouped ? "mt-0.5" : "mt-2"}`}
                        >
                          <div
                            className={`max-w-[72%] rounded-2xl px-3 py-1.5
                              text-sm shadow-sm ${
                                m.is_mine
                                  ? "bg-accent text-accent-fg rounded-br-md"
                                  : "bg-light-card dark:bg-dark-card border border-line text-fg rounded-bl-md"
                              }`}
                          >
                            {!m.is_mine && !grouped && (
                              <div className="text-[11px] font-semibold mb-0.5 text-accent">
                                {m.sender_name || t("dialogs.interlocutor")}
                              </div>
                            )}
                            {hasText && (
                              <div className="whitespace-pre-wrap break-words">
                                {m.text}
                              </div>
                            )}
                            {m.media_type === "photo" && m.media_path && (
                              <button
                                className="mt-1 block"
                                onClick={() => setMediaPreview(m)}
                              >
                                <img
                                  src={m.media_path}
                                  alt="backup photo"
                                  className="max-h-44 rounded-lg object-cover"
                                />
                              </button>
                            )}
                            {(m.media_type === "video" ||
                              m.media_type === "video_note") &&
                              m.media_path && (
                                <button
                                  className="mt-1 block"
                                  onClick={() => setMediaPreview(m)}
                                >
                                  <video
                                    src={m.media_path}
                                    className="max-h-44 rounded-lg"
                                  />
                                </button>
                              )}
                            {m.media_private && !m.media_path && (
                              <div className="mt-1 text-[11px] opacity-70">
                                {t("dialogs.privateMedia", {
                                  kind:
                                    m.media_type === "photo"
                                      ? t("dialogs.photo")
                                      : t("dialogs.video"),
                                })}
                              </div>
                            )}
                            <div
                              className={`text-[10px] mt-0.5 text-right ${
                                m.is_mine
                                  ? "text-accent-fg/60"
                                  : "text-muted"
                              }`}
                            >
                              {formatTime(m.timestamp)}
                            </div>
                          </div>
                        </div>
                      </React.Fragment>
                    );
                  })}
              </div>
            </>
          ) : (
            <div className="p-6 text-sm text-muted">
              {t("dialogs.selectChat")}
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
    </Page>
  );
}
