import React, { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api.js";
import { useLang } from "../hooks/useLang.js";
import { useTheme } from "../hooks/useTheme.js";
import {
  Palette,
  Cpu,
  Users,
  CalendarClock,
  Timer,
  MessageSquare,
  Bell,
  ShieldCheck,
  Mic,
  Database,
  FlaskConical,
  SlidersHorizontal,
  MessageCircle,
} from "lucide-react";
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

const SETTINGS_TABS = [
  { id: "general", icon: SlidersHorizontal },
  { id: "replies", icon: MessageCircle },
  { id: "voice", icon: Mic },
  { id: "memory", icon: Database },
  { id: "calendar", icon: CalendarClock },
  { id: "notifications", icon: Bell },
];

const WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

export default function Settings() {
  const { t, lang, setLang } = useLang();
  const { theme, isDark, toggle: toggleTheme } = useTheme();
  const [tab, setTab] = useState("general");
  const [s, setS] = useState({
    auto_reply: false,
    monitored_chats: [],
    llm_model: "",
    group_reply_mode: "mention",
    quality_filter_enabled: true,
    auto_reconcile_queue: true,
    summary_enabled: true,
    reply_settle_seconds: 12,
    reply_history_limit: 20,
    reply_temperatures: [0.7, 0.85, 1.0],
  });
  const [meetingKw, setMeetingKw] = useState({ builtin: [], user: [] });
  const [newKw, setNewKw] = useState("");
  const [missedCands, setMissedCands] = useState([]);
  const [kwSaving, setKwSaving] = useState(false);
  const [kwErr, setKwErr] = useState("");
  const [chats, setChats] = useState([]);
  const [models, setModels] = useState([]);
  const [modelsError, setModelsError] = useState("");
  const [test, setTest] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
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
  const [cal, setCal] = useState({
    caldav_enabled: false,
    caldav_url: "https://caldav.icloud.com",
    caldav_username: "",
    caldav_password: "",
    caldav_calendar_name: "",
    caldav_has_password: false,
    connected: false,
    calendar: null,
    caldav_work_start: 9,
    caldav_work_end: 20,
    caldav_work_days: [0, 1, 2, 3, 4],
    caldav_slot_duration: 60,
    caldav_lookahead_days: 7,
    caldav_propose_slots: true,
    caldav_auto_create: true,
    caldav_notify: true,
  });
  const [calTest, setCalTest] = useState(null);
  const [calTesting, setCalTesting] = useState(false);
  const [calSaving, setCalSaving] = useState(false);
  const [calMsg, setCalMsg] = useState("");
  const [calErr, setCalErr] = useState("");

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
      summary_enabled: st.summary_enabled !== false,
      reply_settle_seconds:
        st.reply_settle_seconds ?? 12,
      reply_history_limit: st.reply_history_limit ?? 20,
      reply_temperatures:
        Array.isArray(st.reply_temperatures) &&
        st.reply_temperatures.length === 3
          ? st.reply_temperatures.map((v) => Number(v))
          : [0.7, 0.85, 1.0],
    }));
    try {
      const kw = await api.meetingKeywords();
      setMeetingKw({
        builtin: Array.isArray(kw.builtin) ? kw.builtin : [],
        user: Array.isArray(kw.user) ? kw.user : [],
      });
    } catch {
      // ignore
    }
    try {
      const cands = await api.missedMeetingCandidates(15);
      setMissedCands(Array.isArray(cands) ? cands : []);
    } catch {
      // ignore
    }
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
    try {
      const c = await api.getCalendarSettings();
      setCal((prev) => ({ ...prev, ...c, caldav_password: "" }));
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

  async function testCalendar() {
    setCalTesting(true);
    setCalTest(null);
    setCalErr("");
    setCalMsg("");
    try {
      const res = await api.calendarConnectTest();
      setCalTest(res);
    } catch (e) {
      setCalTest({ success: false, error: e.message });
    } finally {
      setCalTesting(false);
    }
  }

  async function saveCalendar() {
    setCalSaving(true);
    setCalErr("");
    setCalMsg("");
    try {
      const payload = {
        caldav_enabled: !!cal.caldav_enabled,
        caldav_work_start: Number(cal.caldav_work_start),
        caldav_work_end: Number(cal.caldav_work_end),
        caldav_work_days: cal.caldav_work_days,
        caldav_slot_duration: Number(cal.caldav_slot_duration),
        caldav_lookahead_days: Number(cal.caldav_lookahead_days),
        caldav_propose_slots: !!cal.caldav_propose_slots,
        caldav_auto_create: !!cal.caldav_auto_create,
        caldav_notify: !!cal.caldav_notify,
      };
      const res = await api.saveCalendarSettings(payload);
      setCal((prev) => ({ ...prev, ...res }));
      setCalMsg(t("common.saved"));
    } catch (e) {
      setCalErr(e.message);
    } finally {
      setCalSaving(false);
    }
  }

  function toggleWorkDay(day) {
    setCal((prev) => {
      const days = new Set(prev.caldav_work_days);
      if (days.has(day)) days.delete(day);
      else days.add(day);
      return { ...prev, caldav_work_days: Array.from(days).sort((a, b) => a - b) };
    });
  }

  async function persistMeetingKeywords(next) {
    setKwSaving(true);
    setKwErr("");
    try {
      const res = await api.saveMeetingKeywords(next);
      setMeetingKw({
        builtin: Array.isArray(res.builtin) ? res.builtin : meetingKw.builtin,
        user: Array.isArray(res.user) ? res.user : next,
      });
    } catch (e) {
      setKwErr(e.message);
    } finally {
      setKwSaving(false);
    }
  }

  async function addMeetingKeyword(text) {
    const v = (text || "").trim().toLowerCase();
    if (!v) return;
    if (meetingKw.user.includes(v)) {
      setNewKw("");
      return;
    }
    const next = [...meetingKw.user, v];
    setNewKw("");
    await persistMeetingKeywords(next);
  }

  async function removeMeetingKeyword(phrase) {
    const next = meetingKw.user.filter((p) => p !== phrase);
    await persistMeetingKeywords(next);
  }

  function setReplyTemperature(idx, value) {
    const v = Math.max(0, Math.min(2, Number(value)));
    setS((prev) => {
      const next = [...(prev.reply_temperatures || [0.7, 0.85, 1.0])];
      next[idx] = v;
      return { ...prev, reply_temperatures: next };
    });
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
      <div
        className="flex flex-wrap gap-1 p-1 rounded-xl
                   bg-light-card2 dark:bg-dark-card2
                   border border-light-border dark:border-dark-border"
      >
        {SETTINGS_TABS.map((tb) => (
          <button
            key={tb.id}
            onClick={() => setTab(tb.id)}
            className={`flex items-center gap-2 px-3 h-8 rounded-lg text-sm
              font-medium transition-colors ${
                tab === tb.id
                  ? "bg-light-card dark:bg-dark-card text-indigo-600 dark:text-indigo-400 shadow-sm"
                  : "text-zinc-500 dark:text-slate-400 hover:text-zinc-700 dark:hover:text-slate-200"
              }`}
          >
            <tb.icon size={14} />
            {t(`settings.tabs.${tb.id}`)}
          </button>
        ))}
      </div>

      {tab === "general" && (
        <>
      <div className="tile">
        <div className="flex items-center gap-2 mb-1">
          <Palette size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
          <span className="tile-label mb-0">{t("settings.appearance")}</span>
        </div>
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
        <div className="flex items-center gap-2 mb-1">
          <Cpu size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
          <span className="tile-label mb-0">{t("settings.lmModel")}</span>
        </div>
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

        </>
      )}

      {tab === "replies" && (
        <>
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
        <div className="flex items-center gap-2 mb-1">
          <MessageCircle size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
          <span className="tile-label mb-0">
            {t("settings.conversationContext")}
          </span>
        </div>
        <div className="text-xs text-muted mt-1">
          {t("settings.conversationContextDesc")}
        </div>

        <label className="flex items-start gap-3 mt-4">
          <input
            type="checkbox"
            className="mt-1"
            checked={s.summary_enabled}
            onChange={(e) =>
              setS({ ...s, summary_enabled: e.target.checked })
            }
          />
          <div>
            <div className="text-sm font-medium">
              {t("settings.useSummary")}
            </div>
            <div className="text-xs text-muted">
              {t("settings.useSummaryDesc")}
            </div>
          </div>
        </label>

        <div className="mt-4">
          <div className="flex justify-between text-sm">
            <span>
              {s.reply_settle_seconds > 0
                ? t("settings.settleWindow", {
                    value: s.reply_settle_seconds,
                  })
                : t("settings.settleWindowOff")}
            </span>
            <span className="text-muted">0–120</span>
          </div>
          <input
            className="w-full mt-2"
            type="range"
            min={0}
            max={120}
            step="1"
            value={s.reply_settle_seconds}
            onChange={(e) =>
              setS({
                ...s,
                reply_settle_seconds: Number(e.target.value),
              })
            }
          />
          <div className="text-xs text-muted mt-1">
            {t("settings.settleWindowDesc")}
          </div>
        </div>

        <div className="mt-4">
          <div className="flex justify-between text-sm">
            <span>
              {t("settings.historyDepth", {
                value: s.reply_history_limit,
              })}
            </span>
            <span className="text-muted">2–80</span>
          </div>
          <input
            className="w-full mt-2"
            type="range"
            min={2}
            max={80}
            step="1"
            value={s.reply_history_limit}
            onChange={(e) =>
              setS({
                ...s,
                reply_history_limit: Number(e.target.value),
              })
            }
          />
          <div className="text-xs text-muted mt-1">
            {t("settings.historyDepthDesc")}
          </div>
        </div>
      </div>

      <div className="tile">
        <div className="flex items-center gap-2 mb-1">
          <FlaskConical size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
          <span className="tile-label mb-0">Температуры генерации</span>
        </div>
        <div className="text-xs text-muted mt-1">
          Для каждого ответа модель генерирует 3 варианта с разной
          температурой и выбирает лучший. Низкая температура — строже
          держит инструкции, высокая — живее, но менее предсказуемо.
        </div>
        <div className="grid grid-cols-3 gap-3 mt-3">
          {[0, 1, 2].map((i) => (
            <div key={i}>
              <div className="tile-label">Вариант {i + 1}</div>
              <input
                className="input mt-1"
                type="number"
                min={0}
                max={2}
                step={0.05}
                value={(s.reply_temperatures || [0.7, 0.85, 1.0])[i]}
                onChange={(e) => setReplyTemperature(i, e.target.value)}
              />
            </div>
          ))}
        </div>
        <div className="text-xs text-muted mt-2">
          По умолчанию: 0.7 / 0.85 / 1.0. Для Qwen2.5 или похожих
          инструкционных моделей попробуй 0.5 / 0.7 / 0.85 — стабильнее
          следуют системному промпту.
        </div>
      </div>

      <div className="tile">
        <div className="flex items-center gap-2 mb-1">
          <CalendarClock size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
          <span className="tile-label mb-0">Распознавание встреч</span>
        </div>
        <div className="text-xs text-muted mt-1">
          Фразы, по которым бот понимает, что речь о встрече, и подключает
          календарь. Встроенные нельзя редактировать, но можно добавить
          свои.
        </div>

        <div className="mt-3">
          <div className="tile-label">Свои фразы</div>
          <div className="flex flex-wrap gap-2 mt-2">
            {meetingKw.user.length === 0 && (
              <span className="text-xs text-muted">
                Пока ничего не добавлено.
              </span>
            )}
            {meetingKw.user.map((p) => (
              <span
                key={p}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-md
                           bg-indigo-500/10 text-indigo-600 dark:text-indigo-300
                           text-xs"
              >
                {p}
                <button
                  className="text-bad ml-1"
                  onClick={() => removeMeetingKeyword(p)}
                  disabled={kwSaving}
                  title="Удалить"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
          <div className="flex gap-2 mt-2">
            <input
              className="input flex-1"
              placeholder="например: пересечёмся в баре"
              value={newKw}
              onChange={(e) => setNewKw(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") addMeetingKeyword(newKw);
              }}
            />
            <button
              className="btn-secondary"
              onClick={() => addMeetingKeyword(newKw)}
              disabled={kwSaving || !newKw.trim()}
            >
              Добавить
            </button>
          </div>
          {kwErr && <div className="text-bad text-xs mt-1">{kwErr}</div>}
        </div>

        {missedCands.length > 0 && (
          <div className="mt-4">
            <div className="tile-label">
              Возможно пропущенные сообщения
            </div>
            <div className="text-xs text-muted mt-1">
              Сообщения, где упоминалось время, но бот не понял про встречу.
              Нажми «использовать» — фраза попадёт в свои.
            </div>
            <div className="space-y-1 mt-2 max-h-48 overflow-auto">
              {missedCands.map((c) => (
                <div
                  key={c.id}
                  className="flex items-start gap-2 text-xs p-2 rounded-md
                             bg-light-card2 dark:bg-dark-card2 border
                             border-light-border dark:border-dark-border"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-muted">
                      {c.sender_name || c.chat_name}
                    </div>
                    <div className="truncate">«{c.text}»</div>
                  </div>
                  <button
                    className="btn-secondary text-xs px-2 py-1"
                    onClick={() => addMeetingKeyword(c.text)}
                    disabled={kwSaving}
                  >
                    использовать
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        <details className="mt-4 text-xs">
          <summary className="cursor-pointer text-muted">
            Встроенные фразы ({meetingKw.builtin.length})
          </summary>
          <div className="flex flex-wrap gap-1 mt-2">
            {meetingKw.builtin.map((p) => (
              <span
                key={p}
                className="px-2 py-0.5 rounded-md bg-light-card2
                           dark:bg-dark-card2 text-muted"
              >
                {p}
              </span>
            ))}
          </div>
        </details>
      </div>

      <div className="tile">
        <div className="flex items-center gap-2 mb-1">
          <Users size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
          <span className="tile-label mb-0">{t("settings.groupReplies")}</span>
        </div>
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
            <div className="flex items-center gap-2">
              <CalendarClock size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
              <span className="tile-label mb-0">{t("settings.schedule")}</span>
            </div>
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
        <div className="flex items-center gap-2 mb-1">
          <Timer size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
          <span className="tile-label mb-0">{t("settings.replyDelay")}</span>
        </div>
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
        <div className="flex items-center gap-2 mb-1">
          <MessageSquare size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
          <span className="tile-label mb-0">{t("settings.monitoredChats")}</span>
        </div>
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

        </>
      )}

      {(tab === "general" || tab === "replies") && (
        <div className="flex gap-2">
          <button className="btn-primary" onClick={save} disabled={saving}>
            {t("common.save")}
          </button>
          <button className="btn-secondary" onClick={runTest}>
            {t("settings.testModel")}
          </button>
          {error && (
            <span className="text-bad text-sm self-center">{error}</span>
          )}
        </div>
      )}

      {tab === "calendar" && (
        <>
      <div className="tile">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <CalendarClock
              size={14}
              className="text-indigo-500 dark:text-indigo-400 flex-shrink-0"
            />
            <span className="tile-label mb-0">Подключение CalDAV</span>
          </div>
          {cal.connected ? (
            <span className="text-xs font-medium px-2 py-1 rounded-md bg-good/15 text-good">
              Подключён{cal.calendar ? ` · ${cal.calendar}` : ""}
            </span>
          ) : (
            <span className="text-xs font-medium px-2 py-1 rounded-md bg-bad/15 text-bad">
              Не подключён
            </span>
          )}
        </div>

        <label className="flex items-start gap-3 mt-3">
          <input
            type="checkbox"
            className="mt-1"
            checked={!!cal.caldav_enabled}
            onChange={(e) =>
              setCal((p) => ({ ...p, caldav_enabled: e.target.checked }))
            }
          />
          <div>
            <div className="text-sm font-medium">Включить интеграцию календаря</div>
            <div className="text-xs text-muted">
              Бот сможет предлагать свободные слоты и создавать встречи.
            </div>
          </div>
        </label>

        <div className="mt-3 rounded-lg border border-light-border dark:border-dark-border
                        bg-light-card2 dark:bg-dark-card2 p-3">
          <div className="text-xs text-muted">
            Данные подключения задаются в файле{" "}
            <code className="font-mono">.env</code> и не редактируются здесь:
          </div>
          <div className="mt-2 space-y-1 text-sm">
            <div className="flex justify-between gap-3">
              <span className="text-muted font-mono text-xs">CALDAV_URL</span>
              <span className="truncate">{cal.caldav_url || "—"}</span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-muted font-mono text-xs">CALDAV_USERNAME</span>
              <span className="truncate">{cal.caldav_username || "—"}</span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-muted font-mono text-xs">CALDAV_PASSWORD</span>
              <span>{cal.caldav_has_password ? "задан" : "не задан"}</span>
            </div>
          </div>
          <div className="text-xs text-muted mt-2">
            Для iCloud создайте пароль приложения на{" "}
            <a
              href="https://appleid.apple.com"
              target="_blank"
              rel="noreferrer"
              className="text-indigo-500 dark:text-indigo-400 underline"
            >
              appleid.apple.com
            </a>{" "}
            и пропишите его в <code className="font-mono">CALDAV_PASSWORD</code>.
          </div>
        </div>

        <div className="flex flex-wrap gap-2 mt-3 items-center">
          <button
            className="btn-secondary"
            onClick={testCalendar}
            disabled={calTesting}
          >
            {calTesting ? "Проверка…" : "Проверить подключение"}
          </button>
          <button
            className="btn-primary"
            onClick={saveCalendar}
            disabled={calSaving}
          >
            {t("common.save")}
          </button>
          {calMsg && <span className="text-good text-sm">{calMsg}</span>}
          {calErr && <span className="text-bad text-sm">{calErr}</span>}
        </div>
        {calTest && (
          <div
            className={`text-sm mt-2 ${
              calTest.success ? "text-good" : "text-bad"
            }`}
          >
            {calTest.success
              ? `✅ Подключено${
                  calTest.calendar_name ? ` · ${calTest.calendar_name}` : ""
                } · Найдено ${calTest.events_count ?? 0} событий сегодня`
              : `❌ Ошибка подключения · ${calTest.error || "проверьте данные"}`}
          </div>
        )}
      </div>

      <div className="tile">
        <div className="flex items-center gap-2 mb-1">
          <Timer size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
          <span className="tile-label mb-0">Рабочие часы</span>
        </div>
        <div className="grid grid-cols-2 gap-3 mt-3">
          <div>
            <div className="tile-label">С</div>
            <select
              className="input mt-1"
              value={cal.caldav_work_start}
              onChange={(e) =>
                setCal((p) => ({ ...p, caldav_work_start: Number(e.target.value) }))
              }
            >
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>
                  {String(h).padStart(2, "0")}:00
                </option>
              ))}
            </select>
          </div>
          <div>
            <div className="tile-label">До</div>
            <select
              className="input mt-1"
              value={cal.caldav_work_end}
              onChange={(e) =>
                setCal((p) => ({ ...p, caldav_work_end: Number(e.target.value) }))
              }
            >
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>
                  {String(h).padStart(2, "0")}:00
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="mt-3">
          <div className="tile-label">Рабочие дни</div>
          <div className="flex flex-wrap gap-2 mt-2">
            {WEEKDAY_LABELS.map((label, day) => (
              <button
                key={day}
                type="button"
                className={`btn-secondary ${
                  cal.caldav_work_days.includes(day) ? "ring-2 ring-accent" : ""
                }`}
                onClick={() => toggleWorkDay(day)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 mt-3">
          <div>
            <div className="tile-label">Длительность слота</div>
            <select
              className="input mt-1"
              value={cal.caldav_slot_duration}
              onChange={(e) =>
                setCal((p) => ({
                  ...p,
                  caldav_slot_duration: Number(e.target.value),
                }))
              }
            >
              <option value={30}>30 мин</option>
              <option value={60}>60 мин</option>
              <option value={90}>90 мин</option>
              <option value={120}>2 часа</option>
            </select>
          </div>
          <div>
            <div className="tile-label">Горизонт планирования</div>
            <select
              className="input mt-1"
              value={cal.caldav_lookahead_days}
              onChange={(e) =>
                setCal((p) => ({
                  ...p,
                  caldav_lookahead_days: Number(e.target.value),
                }))
              }
            >
              <option value={3}>3 дня</option>
              <option value={7}>7 дней</option>
              <option value={14}>14 дней</option>
            </select>
          </div>
        </div>
      </div>

      <div className="tile">
        <div className="flex items-center gap-2 mb-1">
          <SlidersHorizontal size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
          <span className="tile-label mb-0">Поведение</span>
        </div>
        {[
          {
            key: "caldav_propose_slots",
            title: "Предлагать слоты при запросе встречи",
            desc: "Бот добавит свободное время в ответ на «давай встретимся».",
          },
          {
            key: "caldav_auto_create",
            title: "Автоматически создавать событие при подтверждении",
            desc: "Когда собеседник соглашается, встреча появится в календаре.",
          },
          {
            key: "caldav_notify",
            title: "Уведомлять о созданных событиях в Telegram",
            desc: "Админ-бот пришлёт уведомление о новой встрече.",
          },
        ].map((item) => (
          <label key={item.key} className="flex items-start gap-3 mt-3">
            <input
              type="checkbox"
              className="mt-1"
              checked={!!cal[item.key]}
              onChange={(e) =>
                setCal((p) => ({ ...p, [item.key]: e.target.checked }))
              }
            />
            <div>
              <div className="text-sm font-medium">{item.title}</div>
              <div className="text-xs text-muted">{item.desc}</div>
            </div>
          </label>
        ))}
        <div className="flex flex-wrap gap-2 mt-3 items-center">
          <button
            className="btn-primary"
            onClick={saveCalendar}
            disabled={calSaving}
          >
            {t("common.save")}
          </button>
          {calMsg && <span className="text-good text-sm">{calMsg}</span>}
          {calErr && <span className="text-bad text-sm">{calErr}</span>}
        </div>
      </div>
        </>
      )}

      {tab === "notifications" && (
        <>
      <div className="tile">
        <div className="flex items-center gap-2 mb-1">
          <ShieldCheck size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
          <span className="tile-label mb-0">{t("settings.adminPanel")}</span>
        </div>
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
        </>
      )}

      {tab === "voice" && (
        <>
      <div className="tile">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Mic size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
            <span className="tile-label mb-0">{t("settings.voiceMessages")}</span>
          </div>
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
        </>
      )}

      {tab === "memory" && (
        <>
      <div className="tile">
        <div className="flex items-center gap-2 mb-1">
          <Database size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
          <span className="tile-label mb-0">{t("settings.ragMemory")}</span>
        </div>
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
        </>
      )}

      {(tab === "general" || tab === "replies") && test && (
        <div className="tile">
          <div className="flex items-center gap-2 mb-1">
            <FlaskConical size={14} className="text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
            <span className="tile-label mb-0">{t("settings.testResult")}</span>
          </div>
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
