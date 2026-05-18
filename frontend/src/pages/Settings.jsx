import React, { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api.js";
import { useLang } from "../hooks/useLang.js";
import { useTheme } from "../hooks/useTheme.js";
import { LANGUAGES } from "../lib/i18n.js";
import Page from "../components/Page.jsx";

const dayValues = [0, 1, 2, 3, 4, 5, 6];

const timezoneOptions = [
  "Europe/Moscow",
  "Asia/Yekaterinburg",
  "Asia/Novosibirsk",
  "Europe/Kiev",
  "Asia/Almaty",
];

const delayBounds = { min: 30, max: 600 };

function formatDelayPreview(seconds) {
  const minutes = seconds / 60;
  if (Number.isInteger(minutes)) return `${minutes}`;
  return minutes.toFixed(1).replace(".", ",");
}

export default function Settings() {
  const { t, lang, setLang } = useLang();
  const { theme, isDark, toggle: toggleTheme } = useTheme();
  const [s, setS] = useState({
    auto_reply: false,
    monitored_chats: [],
    llm_model: "",
    group_reply_mode: "mention",
    quality_filter_enabled: true,
    auto_reconcile_queue: true,
  });
  const [chats, setChats] = useState([]);
  const [models, setModels] = useState([]);
  const [modelsError, setModelsError] = useState("");
  const [test, setTest] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notify, setNotify] = useState({ chat_id: "", enabled: true });
  const [delay, setDelay] = useState({
    delay_enabled: false,
    delay_min_seconds: 60,
    delay_max_seconds: 180,
  });
  const [schedule, setSchedule] = useState({
    enabled: false,
    timezone: "Europe/Moscow",
    days: [0, 1, 2, 3, 4, 5, 6],
    start: "09:00",
    end: "23:00",
    active: true,
    next_active_text: "",
  });
  const [notifySaving, setNotifySaving] = useState(false);
  const [notifyMsg, setNotifyMsg] = useState("");
  const [notifyError, setNotifyError] = useState("");
  const [admin, setAdmin] = useState({
    owner_chat_id: "",
    admin_notify_auto: true,
    admin_notify_pending: true,
  });
  const [adminSaving, setAdminSaving] = useState(false);
  const [adminMsg, setAdminMsg] = useState("");
  const [adminError, setAdminError] = useState("");
  const [whisper, setWhisper] = useState({
    whisper_enabled: true,
    whisper_model: "large-v3",
    whisper_language: "ru",
    voice_reply_mode: "text",
    whisper_lazy_load: false,
    device: "cpu",
    model_loaded: false,
    ffmpeg_available: true,
  });
  const [whisperSaving, setWhisperSaving] = useState(false);
  const [whisperMsg, setWhisperMsg] = useState("");
  const [whisperErr, setWhisperErr] = useState("");
  const [rag, setRag] = useState({
    rag_enabled: true,
    min_similarity: 0.6,
    max_results: 5,
    search_cross_chat: true,
    cross_chat_min_similarity: 0.7,
  });
  const [ragSaving, setRagSaving] = useState(false);
  const [ragMsg, setRagMsg] = useState("");
  const [ragErr, setRagErr] = useState("");

  const modelOptions = useMemo(() => {
    const list = [...models];
    if (s.llm_model && !list.includes(s.llm_model)) {
      list.unshift(s.llm_model);
    }
    return list;
  }, [models, s.llm_model]);

  const refresh = async () => {
    const [cs, st, sch, d] = await Promise.all([
      api.chats(),
      api.getSettings(),
      api.getSchedule(),
      api.getDelay(),
    ]);
    setChats(cs);
    setS((prev) => ({
      ...prev,
      auto_reply: !!st.auto_reply,
      monitored_chats: st.monitored_chats || [],
      llm_model: st.llm_model || "",
      group_reply_mode: st.group_reply_mode || "mention",
      quality_filter_enabled: st.quality_filter_enabled !== false,
      auto_reconcile_queue: st.auto_reconcile_queue !== false,
    }));
    setSchedule((prev) => ({
      ...prev,
      enabled: !!sch.enabled,
      timezone: sch.timezone || "Europe/Moscow",
      days: sch.days || [0, 1, 2, 3, 4, 5, 6],
      start: sch.start || "09:00",
      end: sch.end || "23:00",
      active: !!sch.active,
      next_active_text: sch.next_active_text || "",
    }));
    setDelay({
      delay_enabled: !!d.delay_enabled,
      delay_min_seconds: d.delay_min_seconds || 60,
      delay_max_seconds: d.delay_max_seconds || 180,
    });
    try {
      const m = await api.listModels();
      setModels(m.models || []);
      setModelsError("");
    } catch (e) {
      setModels([]);
      setModelsError(e.message || t("settings.modelsErrorShort"));
    }
    try {
      const n = await api.getNotifyChat();
      setNotify({
        chat_id: n.chat_id || "",
        enabled: n.enabled !== false,
      });
    } catch {
      // ignore
    }
    try {
      const w = await api.getWhisperSettings();
      setWhisper((prev) => ({ ...prev, ...w }));
    } catch {
      // ignore
    }
    try {
      const r = await api.getRagSettings();
      setRag((prev) => ({ ...prev, ...r }));
    } catch {
      // ignore
    }
    try {
      const a = await api.getAdminSettings();
      setAdmin({
        owner_chat_id: a.owner_chat_id || "",
        admin_notify_auto: a.admin_notify_auto !== false,
        admin_notify_pending: a.admin_notify_pending !== false,
      });
    } catch {
      // ignore
    }
  };

  async function saveAdmin() {
    setAdminSaving(true);
    setAdminError("");
    setAdminMsg("");
    try {
      await api.saveAdminSettings({
        owner_chat_id: admin.owner_chat_id.trim(),
        admin_notify_auto: !!admin.admin_notify_auto,
        admin_notify_pending: !!admin.admin_notify_pending,
      });
      setAdminMsg(t("common.saved"));
    } catch (e) {
      setAdminError(e.message);
    } finally {
      setAdminSaving(false);
    }
  }

  async function detectOwner() {
    setAdminError("");
    setAdminMsg("");
    try {
      const res = await api.detectOwner();
      setAdmin((prev) => ({ ...prev, owner_chat_id: String(res.chat_id || "") }));
      setAdminMsg(t("settings.chatIdDetected"));
    } catch (e) {
      setAdminError(e.message);
    }
  }

  async function testAdminNotify() {
    setAdminError("");
    setAdminMsg("");
    try {
      await api.adminNotifyTest();
      setAdminMsg(t("settings.testNotifySent"));
    } catch (e) {
      setAdminError(e.message);
    }
  }

  async function saveWhisper() {
    setWhisperSaving(true);
    setWhisperErr("");
    setWhisperMsg("");
    try {
      await api.saveWhisperSettings({
        whisper_enabled: !!whisper.whisper_enabled,
        whisper_model: whisper.whisper_model,
        whisper_language: whisper.whisper_language,
        voice_reply_mode: whisper.voice_reply_mode,
        whisper_lazy_load: !!whisper.whisper_lazy_load,
      });
      setWhisperMsg(t("common.saved"));
    } catch (e) {
      setWhisperErr(e.message);
    } finally {
      setWhisperSaving(false);
    }
  }

  async function saveRag() {
    setRagSaving(true);
    setRagErr("");
    setRagMsg("");
    try {
      await api.saveRagSettings({
        rag_enabled: !!rag.rag_enabled,
        min_similarity: Number(rag.min_similarity),
        max_results: Number(rag.max_results),
        search_cross_chat: !!rag.search_cross_chat,
        cross_chat_min_similarity: Number(rag.cross_chat_min_similarity),
      });
      setRagMsg(t("common.saved"));
    } catch (e) {
      setRagErr(e.message);
    } finally {
      setRagSaving(false);
    }
  }

  async function unloadWhisper() {
    setWhisperErr("");
    try {
      await api.whisperUnload();
      const w = await api.getWhisperSettings();
      setWhisper((prev) => ({ ...prev, ...w }));
    } catch (e) {
      setWhisperErr(e.message);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function save() {
    setSaving(true);
    setError("");
    try {
      await Promise.all([
        api.saveSettings(s),
        api.saveSchedule(schedule),
        api.saveDelay(delay),
      ]);
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function runTest() {
    setTest({ loading: true });
    try {
      const res = await api.testLLM(t("settings.testPrompt"));
      setTest({ loading: false, ...res });
    } catch (e) {
      setTest({ loading: false, error: e.message });
    }
  }

  async function saveNotify() {
    setNotifySaving(true);
    setNotifyError("");
    setNotifyMsg("");
    try {
      await api.saveNotifyChat({
        chat_id: notify.chat_id.trim(),
        enabled: !!notify.enabled,
      });
      setNotifyMsg(t("common.saved"));
    } catch (e) {
      setNotifyError(e.message);
    } finally {
      setNotifySaving(false);
    }
  }

  async function detectNotify() {
    setNotifyError("");
    setNotifyMsg("");
    try {
      const res = await api.detectNotifyChat();
      setNotify((prev) => ({ ...prev, chat_id: String(res.chat_id || "") }));
      setNotifyMsg(t("settings.chatIdDetected"));
    } catch (e) {
      setNotifyError(e.message);
    }
  }

  function toggleScheduleDay(day) {
    setSchedule((prev) => {
      const days = new Set(prev.days);
      if (days.has(day) && days.size > 1) days.delete(day);
      else days.add(day);
      return { ...prev, days: Array.from(days).sort() };
    });
  }

  function updateDelay(field, value) {
    const seconds = Number(value);
    setDelay((prev) => {
      const next = { ...prev, [field]: seconds };
      if (field === "delay_min_seconds" && seconds > next.delay_max_seconds) {
        next.delay_max_seconds = seconds;
      }
      if (field === "delay_max_seconds" && seconds < next.delay_min_seconds) {
        next.delay_min_seconds = seconds;
      }
      return next;
    });
  }

  function toggleChat(chat_id) {
    setS((prev) => {
      const set = new Set(prev.monitored_chats);
      if (set.has(chat_id)) set.delete(chat_id);
      else set.add(chat_id);
      return { ...prev, monitored_chats: Array.from(set) };
    });
  }

  return (
    <Page>
      <div className="tile">
        <div className="tile-label">{t("settings.appearance")}</div>
        <div className="grid md:grid-cols-2 gap-3 mt-3">
          <div>
            <div className="tile-label">{t("settings.themeLabel")}</div>
            <div className="flex gap-2 mt-2">
              {[
                { v: "light", label: t("settings.themeLight") },
                { v: "dark", label: t("settings.themeDark") },
              ].map((opt) => (
                <button
                  key={opt.v}
                  className={`btn-secondary ${
                    theme === opt.v ? "ring-2 ring-accent" : ""
                  }`}
                  onClick={() => {
                    if ((opt.v === "dark") !== isDark) toggleTheme();
                  }}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <div className="tile-label">{t("settings.languageLabel")}</div>
            <div className="flex gap-2 mt-2">
              {LANGUAGES.map((code) => (
                <button
                  key={code}
                  className={`btn-secondary ${
                    lang === code ? "ring-2 ring-accent" : ""
                  }`}
                  onClick={() => setLang(code)}
                >
                  {t(`language.${code}`)}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="tile">
        <div className="tile-label">{t("settings.lmModel")}</div>
        <select
          className="input mt-2"
          value={s.llm_model}
          onChange={(e) => setS({ ...s, llm_model: e.target.value })}
        >
          {modelOptions.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        {modelsError && (
          <div className="text-xs text-bad mt-1">
            {t("settings.modelsLoadError", { error: modelsError })}
          </div>
        )}
        {!modelsError && models.length === 0 && (
          <div className="text-xs text-muted mt-1">
            {t("settings.noModels", { OPENAI_BASE_URL: "{OPENAI_BASE_URL}" })}
          </div>
        )}
      </div>

      <div className="tile">
        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            className="mt-1"
            checked={s.auto_reply}
            onChange={(e) => setS({ ...s, auto_reply: e.target.checked })}
          />
          <div>
            <div className="text-sm font-medium">{t("settings.autoReply")}</div>
            <div className="text-xs text-muted">
              {t("settings.autoReplyDesc")}
            </div>
          </div>
        </label>
      </div>


      <div className="tile">
        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            className="mt-1"
            checked={s.quality_filter_enabled}
            onChange={(e) =>
              setS({ ...s, quality_filter_enabled: e.target.checked })
            }
          />
          <div>
            <div className="text-sm font-medium">
              {t("settings.qualityFilter")}
            </div>
            <div className="text-xs text-muted">
              {t("settings.qualityFilterDesc")}
            </div>
          </div>
        </label>
      </div>

      <div className="tile">
        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            className="mt-1"
            checked={s.auto_reconcile_queue}
            onChange={(e) =>
              setS({ ...s, auto_reconcile_queue: e.target.checked })
            }
          />
          <div>
            <div className="text-sm font-medium">
              {t("settings.autoReconcile")}
            </div>
            <div className="text-xs text-muted">
              {t("settings.autoReconcileDesc")}
            </div>
          </div>
        </label>
      </div>

      <div className="tile">
        <div className="tile-label">{t("settings.groupReplies")}</div>
        <div className="text-xs text-muted mt-1">
          {t("settings.groupRepliesDesc")}
        </div>
        <div className="space-y-1 mt-2">
          {[
            { v: "mention", label: t("settings.groupMention") },
            { v: "all", label: t("settings.groupAll") },
          ].map((opt) => (
            <label key={opt.v} className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="group_reply_mode"
                checked={s.group_reply_mode === opt.v}
                onChange={() => setS({ ...s, group_reply_mode: opt.v })}
              />
              {opt.label}
            </label>
          ))}
        </div>
      </div>

      <div className="tile">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="tile-label">{t("settings.schedule")}</div>
            <div className="text-xs text-muted mt-1">
              {t("settings.scheduleDesc")}
            </div>
          </div>
          <span
            className={`px-3 py-1 rounded-full text-xs ${
              schedule.active ? "bg-good/15 text-good" : "bg-bad/15 text-bad"
            }`}
          >
            {schedule.active
              ? t("settings.scheduleActive")
              : t("settings.scheduleInactive")}
          </span>
        </div>

        <label className="flex items-start gap-3 mt-4">
          <input
            type="checkbox"
            className="mt-1"
            checked={!!schedule.enabled}
            onChange={(e) =>
              setSchedule((prev) => ({ ...prev, enabled: e.target.checked }))
            }
          />
          <div>
            <div className="text-sm font-medium">
              {t("settings.limitHours")}
            </div>
            <div className="text-xs text-muted">
              {t("settings.limitHoursDesc")}
            </div>
          </div>
        </label>

        <div className="grid md:grid-cols-3 gap-3 mt-4">
          <div>
            <div className="tile-label">{t("settings.timezone")}</div>
            <select
              className="input mt-1"
              value={schedule.timezone}
              onChange={(e) =>
                setSchedule((prev) => ({ ...prev, timezone: e.target.value }))
              }
            >
              {timezoneOptions.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </select>
          </div>
          <div>
            <div className="tile-label">{t("settings.from")}</div>
            <input
              className="input mt-1"
              type="time"
              value={schedule.start}
              onChange={(e) =>
                setSchedule((prev) => ({ ...prev, start: e.target.value }))
              }
            />
          </div>
          <div>
            <div className="tile-label">{t("settings.to")}</div>
            <input
              className="input mt-1"
              type="time"
              value={schedule.end}
              onChange={(e) =>
                setSchedule((prev) => ({ ...prev, end: e.target.value }))
              }
            />
          </div>
        </div>

        <div className="mt-4">
          <div className="tile-label">{t("settings.weekdays")}</div>
          <div className="flex flex-wrap gap-2 mt-2">
            {dayValues.map((d) => (
              <label
                key={d}
                className="flex items-center gap-2 text-sm rounded-lg border border-line px-3 py-2"
              >
                <input
                  type="checkbox"
                  checked={schedule.days.includes(d)}
                  onChange={() => toggleScheduleDay(d)}
                />
                {t("settings.days")[d]}
              </label>
            ))}
          </div>
        </div>

        {!schedule.active && schedule.next_active_text && (
          <div className="text-sm text-muted mt-4">
            {t("settings.nextPeriod", { text: schedule.next_active_text })}
          </div>
        )}
      </div>

      <div className="tile">
        <div className="tile-label">{t("settings.replyDelay")}</div>
        <label className="flex items-start gap-3 mt-3">
          <input
            type="checkbox"
            className="mt-1"
            checked={!!delay.delay_enabled}
            onChange={(e) =>
              setDelay((prev) => ({ ...prev, delay_enabled: e.target.checked }))
            }
          />
          <div>
            <div className="text-sm font-medium">
              {t("settings.simulateThinking")}
            </div>
            <div className="text-xs text-muted">
              {t("settings.simulateThinkingDesc")}
            </div>
          </div>
        </label>

        <div className="grid md:grid-cols-2 gap-4 mt-4">
          <div>
            <div className="flex justify-between text-sm">
              <span>
                {t("settings.fromSec", { value: delay.delay_min_seconds })}
              </span>
              <span className="text-muted">{delayBounds.min}–{delayBounds.max}</span>
            </div>
            <input
              className="w-full mt-2"
              type="range"
              min={delayBounds.min}
              max={delayBounds.max}
              step="10"
              value={delay.delay_min_seconds}
              onChange={(e) =>
                updateDelay("delay_min_seconds", e.target.value)
              }
            />
          </div>
          <div>
            <div className="flex justify-between text-sm">
              <span>
                {t("settings.toSec", { value: delay.delay_max_seconds })}
              </span>
              <span className="text-muted">{delayBounds.min}–{delayBounds.max}</span>
            </div>
            <input
              className="w-full mt-2"
              type="range"
              min={delayBounds.min}
              max={delayBounds.max}
              step="10"
              value={delay.delay_max_seconds}
              onChange={(e) =>
                updateDelay("delay_max_seconds", e.target.value)
              }
            />
          </div>
        </div>

        <div className="text-sm text-muted mt-3">
          {t("settings.delayPreview", {
            min: formatDelayPreview(delay.delay_min_seconds),
            max: formatDelayPreview(delay.delay_max_seconds),
          })}
        </div>
      </div>


      <div className="tile">
        <div className="tile-label">{t("settings.monitoredChats")}</div>
        <div className="text-xs text-muted mt-1">
          {t("settings.monitoredChatsDesc")}
        </div>
        <div className="mt-2 space-y-1 max-h-72 overflow-auto">
          {chats.length === 0 && (
            <div className="text-sm text-muted">{t("settings.noChats")}</div>
          )}
          {chats.map((c) => (
            <label key={c.chat_id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={s.monitored_chats.includes(c.chat_id)}
                onChange={() => toggleChat(c.chat_id)}
              />
              <span>{c.chat_name || c.chat_id}</span>
              <span className="text-muted text-xs ml-auto">
                {t("settings.msgsAbbr", { count: c.count })}
              </span>
            </label>
          ))}
        </div>
      </div>

      <div className="flex gap-2">
        <button className="btn-primary" onClick={save} disabled={saving}>
          {t("common.save")}
        </button>
        <button className="btn-secondary" onClick={runTest}>
          {t("settings.testModel")}
        </button>
        {error && <span className="text-bad text-sm self-center">{error}</span>}
      </div>

      <div className="tile">
        <div className="tile-label">{t("settings.notifications")}</div>
        <label className="flex items-start gap-3 mt-2">
          <input
            type="checkbox"
            className="mt-1"
            checked={!!notify.enabled}
            onChange={(e) =>
              setNotify((prev) => ({ ...prev, enabled: e.target.checked }))
            }
          />
          <div>
            <div className="text-sm font-medium">
              {t("settings.notifyOnReply")}
            </div>
            <div className="text-xs text-muted">
              {t("settings.notifyOnReplyDesc")}
            </div>
          </div>
        </label>

        <div className="mt-3">
          <div className="tile-label">{t("settings.yourChatId")}</div>
          <div className="flex gap-2 mt-1">
            <input
              className="input flex-1"
              placeholder={t("settings.chatIdPlaceholder")}
              value={notify.chat_id}
              onChange={(e) =>
                setNotify((prev) => ({ ...prev, chat_id: e.target.value }))
              }
            />
            <button className="btn-secondary" onClick={detectNotify}>
              {t("settings.detectAuto")}
            </button>
          </div>
          <div className="text-xs text-muted mt-1">
            {t("settings.detectHint")}
          </div>
        </div>

        <div className="flex gap-2 mt-3 items-center">
          <button
            className="btn-primary"
            onClick={saveNotify}
            disabled={notifySaving}
          >
            {t("settings.saveNotifications")}
          </button>
          {notifyMsg && (
            <span className="text-good text-sm">{notifyMsg}</span>
          )}
          {notifyError && (
            <span className="text-bad text-sm">{notifyError}</span>
          )}
        </div>
      </div>

      <div className="tile">
        <div className="tile-label">{t("settings.adminPanel")}</div>
        <div className="text-xs text-muted mt-1">
          {t("settings.adminPanelDesc")}
        </div>

        <div className="mt-3">
          <div className="tile-label">OWNER_CHAT_ID</div>
          <div className="flex gap-2 mt-1">
            <input
              className="input flex-1"
              placeholder={t("settings.chatIdPlaceholder")}
              value={admin.owner_chat_id}
              onChange={(e) =>
                setAdmin((prev) => ({ ...prev, owner_chat_id: e.target.value }))
              }
            />
            <button className="btn-secondary" onClick={detectOwner}>
              {t("settings.detectAuto")}
            </button>
          </div>
        </div>

        <label className="flex items-start gap-3 mt-3">
          <input
            type="checkbox"
            className="mt-1"
            checked={!!admin.admin_notify_auto}
            onChange={(e) =>
              setAdmin((prev) => ({
                ...prev,
                admin_notify_auto: e.target.checked,
              }))
            }
          />
          <div>
            <div className="text-sm font-medium">
              {t("settings.adminNotifyAuto")}
            </div>
            <div className="text-xs text-muted">
              {t("settings.adminNotifyAutoDesc")}
            </div>
          </div>
        </label>

        <label className="flex items-start gap-3 mt-3">
          <input
            type="checkbox"
            className="mt-1"
            checked={!!admin.admin_notify_pending}
            onChange={(e) =>
              setAdmin((prev) => ({
                ...prev,
                admin_notify_pending: e.target.checked,
              }))
            }
          />
          <div>
            <div className="text-sm font-medium">
              {t("settings.adminNotifyPending")}
            </div>
            <div className="text-xs text-muted">
              {t("settings.adminNotifyPendingDesc")}
            </div>
          </div>
        </label>

        <div className="flex flex-wrap gap-2 mt-3 items-center">
          <button
            className="btn-primary"
            onClick={saveAdmin}
            disabled={adminSaving}
          >
            {t("common.save")}
          </button>
          <button className="btn-secondary" onClick={testAdminNotify}>
            {t("settings.sendTestNotify")}
          </button>
          {adminMsg && <span className="text-good text-sm">{adminMsg}</span>}
          {adminError && (
            <span className="text-bad text-sm">{adminError}</span>
          )}
        </div>
      </div>

      <div className="tile">
        <div className="flex items-center justify-between gap-3">
          <div className="tile-label">{t("settings.voiceMessages")}</div>
          <span
            className={`px-2 py-0.5 rounded-full text-xs ${
              whisper.device === "cuda"
                ? "bg-good/15 text-good"
                : "bg-bad/15 text-bad"
            }`}
          >
            {whisper.device === "cuda"
              ? t("settings.whisperCuda")
              : t("settings.whisperCpu")}
          </span>
        </div>

        {!whisper.ffmpeg_available && (
          <div className="text-xs text-bad mt-2">
            {t("settings.ffmpegWarning")}
          </div>
        )}

        <label className="flex items-start gap-3 mt-3">
          <input
            type="checkbox"
            className="mt-1"
            checked={!!whisper.whisper_enabled}
            onChange={(e) =>
              setWhisper((prev) => ({
                ...prev,
                whisper_enabled: e.target.checked,
              }))
            }
          />
          <div>
            <div className="text-sm font-medium">
              {t("settings.transcribeVoice")}
            </div>
            <div className="text-xs text-muted">
              {t("settings.transcribeVoiceDesc")}
            </div>
          </div>
        </label>

        {whisper.whisper_enabled && (
          <>
            <div className="grid md:grid-cols-2 gap-3 mt-4">
              <div>
                <div className="tile-label">{t("settings.modelLabel")}</div>
                <select
                  className="input mt-1"
                  value={whisper.whisper_model}
                  onChange={(e) =>
                    setWhisper((prev) => ({
                      ...prev,
                      whisper_model: e.target.value,
                    }))
                  }
                >
                  <option value="tiny">tiny (~1GB VRAM)</option>
                  <option value="base">base (~1GB VRAM)</option>
                  <option value="small">small (~1.5GB VRAM)</option>
                  <option value="medium">medium (~3GB VRAM)</option>
                  <option value="large-v3">large-v3 (~6GB VRAM)</option>
                </select>
              </div>
              <div>
                <div className="tile-label">
                  {t("settings.languageLabelVoice")}
                </div>
                <select
                  className="input mt-1"
                  value={whisper.whisper_language}
                  onChange={(e) =>
                    setWhisper((prev) => ({
                      ...prev,
                      whisper_language: e.target.value,
                    }))
                  }
                >
                  <option value="ru">{t("settings.langRu")}</option>
                  <option value="en">{t("settings.langEn")}</option>
                  <option value="auto">{t("settings.langAuto")}</option>
                </select>
              </div>
            </div>

            <label className="flex items-start gap-3 mt-4">
              <input
                type="checkbox"
                className="mt-1"
                checked={!!whisper.whisper_lazy_load}
                onChange={(e) =>
                  setWhisper((prev) => ({
                    ...prev,
                    whisper_lazy_load: e.target.checked,
                  }))
                }
              />
              <div>
                <div className="text-sm font-medium">
                  {t("settings.lazyLoad")}
                </div>
                <div className="text-xs text-muted">
                  {t("settings.lazyLoadDesc")}
                </div>
              </div>
            </label>

            <div className="mt-4">
              <div className="tile-label">{t("settings.voiceReaction")}</div>
              <div className="space-y-1 mt-2">
                {[
                  { v: "text", label: t("settings.voiceText") },
                  { v: "pending", label: t("settings.voicePending") },
                  { v: "skip", label: t("settings.voiceSkip") },
                ].map((opt) => (
                  <label
                    key={opt.v}
                    className="flex items-center gap-2 text-sm"
                  >
                    <input
                      type="radio"
                      name="voice_reply_mode"
                      checked={whisper.voice_reply_mode === opt.v}
                      onChange={() =>
                        setWhisper((prev) => ({
                          ...prev,
                          voice_reply_mode: opt.v,
                        }))
                      }
                    />
                    {opt.label}
                  </label>
                ))}
              </div>
            </div>
          </>
        )}

        <div className="flex flex-wrap gap-2 mt-4 items-center">
          <button
            className="btn-primary"
            onClick={saveWhisper}
            disabled={whisperSaving}
          >
            {t("common.save")}
          </button>
          {whisper.model_loaded && (
            <button className="btn-secondary" onClick={unloadWhisper}>
              {t("settings.unloadWhisper")}
            </button>
          )}
          {whisperMsg && (
            <span className="text-good text-sm">{whisperMsg}</span>
          )}
          {whisperErr && (
            <span className="text-bad text-sm">{whisperErr}</span>
          )}
        </div>
      </div>

      <div className="tile">
        <div className="tile-label">{t("settings.ragMemory")}</div>
        <label className="flex items-start gap-3 mt-3">
          <input
            type="checkbox"
            className="mt-1"
            checked={!!rag.rag_enabled}
            onChange={(e) =>
              setRag((prev) => ({ ...prev, rag_enabled: e.target.checked }))
            }
          />
          <div>
            <div className="text-sm font-medium">
              {t("settings.ragSearchAll")}
            </div>
            <div className="text-xs text-muted">
              {t("settings.ragSearchAllDesc")}
            </div>
          </div>
        </label>

        {rag.rag_enabled && (
          <>
            <div className="mt-4">
              <div className="flex justify-between text-sm">
                <span>
                  {t("settings.minSimilarity", {
                    value: Number(rag.min_similarity).toFixed(2),
                  })}
                </span>
                <span className="text-muted">0.40–0.90</span>
              </div>
              <input
                className="w-full mt-2"
                type="range"
                min="0.4"
                max="0.9"
                step="0.05"
                value={rag.min_similarity}
                onChange={(e) =>
                  setRag((prev) => ({
                    ...prev,
                    min_similarity: Number(e.target.value),
                  }))
                }
              />
              <div className="text-xs text-muted mt-1">
                {t("settings.minSimilarityDesc")}
              </div>
            </div>

            <div className="mt-4">
              <div className="tile-label">{t("settings.maxResults")}</div>
              <input
                className="input mt-1 w-32"
                type="number"
                min="1"
                max="10"
                value={rag.max_results}
                onChange={(e) =>
                  setRag((prev) => ({
                    ...prev,
                    max_results: e.target.value,
                  }))
                }
              />
            </div>

            <label className="flex items-start gap-3 mt-4">
              <input
                type="checkbox"
                className="mt-1"
                checked={!!rag.search_cross_chat}
                onChange={(e) =>
                  setRag((prev) => ({
                    ...prev,
                    search_cross_chat: e.target.checked,
                  }))
                }
              />
              <div>
                <div className="text-sm font-medium">
                  {t("settings.searchCrossChat")}
                </div>
                <div className="text-xs text-muted">
                  {t("settings.searchCrossChatDesc")}
                </div>
              </div>
            </label>

            {rag.search_cross_chat && (
              <div className="mt-4">
                <div className="flex justify-between text-sm">
                  <span>
                    {t("settings.crossChatThreshold", {
                      value: Number(
                        rag.cross_chat_min_similarity
                      ).toFixed(2),
                    })}
                  </span>
                  <span className="text-muted">0.50–0.95</span>
                </div>
                <input
                  className="w-full mt-2"
                  type="range"
                  min="0.5"
                  max="0.95"
                  step="0.05"
                  value={rag.cross_chat_min_similarity}
                  onChange={(e) =>
                    setRag((prev) => ({
                      ...prev,
                      cross_chat_min_similarity: Number(e.target.value),
                    }))
                  }
                />
              </div>
            )}
          </>
        )}

        <div className="flex flex-wrap gap-2 mt-4 items-center">
          <button
            className="btn-primary"
            onClick={saveRag}
            disabled={ragSaving}
          >
            {t("common.save")}
          </button>
          {ragMsg && <span className="text-good text-sm">{ragMsg}</span>}
          {ragErr && <span className="text-bad text-sm">{ragErr}</span>}
        </div>
      </div>

      {test && (
        <div className="tile">
          <div className="tile-label">{t("settings.testResult")}</div>
          {test.loading && <div className="text-sm text-muted">...</div>}
          {test.error && <div className="text-bad text-sm">{test.error}</div>}
          {test.variants && (
            <ul className="mt-2 text-sm space-y-1">
              {test.variants.map((v, i) => (
                <li key={i} className="text-fg">• {v}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Page>
  );
}
