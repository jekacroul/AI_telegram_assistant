import React, { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api.js";

const dayOptions = [
  { value: 0, label: "Пн" },
  { value: 1, label: "Вт" },
  { value: 2, label: "Ср" },
  { value: 3, label: "Чт" },
  { value: 4, label: "Пт" },
  { value: 5, label: "Сб" },
  { value: 6, label: "Вс" },
];

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
  const [s, setS] = useState({
    auto_reply: false,
    monitored_chats: [],
    llm_model: "",
    group_reply_mode: "mention",
    quality_filter_enabled: true,
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
      setModelsError(e.message || "не удалось получить список моделей");
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
      setAdminMsg("Сохранено");
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
      setAdminMsg("chat_id определён. Не забудь сохранить.");
    } catch (e) {
      setAdminError(e.message);
    }
  }

  async function testAdminNotify() {
    setAdminError("");
    setAdminMsg("");
    try {
      await api.adminNotifyTest();
      setAdminMsg("Тестовое уведомление отправлено.");
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
      setWhisperMsg("Сохранено");
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
      setRagMsg("Сохранено");
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
      const res = await api.testLLM("Привет, как дела?");
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
      setNotifyMsg("Сохранено");
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
      setNotifyMsg("chat_id определён. Не забудь сохранить.");
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
    <div className="space-y-4">
      <div className="card">
        <div className="label">Модель LM Studio</div>
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
            Не удалось загрузить список моделей: {modelsError}
          </div>
        )}
        {!modelsError && models.length === 0 && (
          <div className="text-xs text-muted mt-1">
            LM Studio не вернул моделей. Загрузи модель в LM Studio и убедись,
            что локальный сервер запущен на {`{OPENAI_BASE_URL}`}.
          </div>
        )}
      </div>

      <div className="card">
        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            className="mt-1"
            checked={s.auto_reply}
            onChange={(e) => setS({ ...s, auto_reply: e.target.checked })}
          />
          <div>
            <div className="text-sm font-medium">Авто-ответ</div>
            <div className="text-xs text-muted">
              ⚠️ Бот будет отвечать без твоего подтверждения
            </div>
          </div>
        </label>
      </div>


      <div className="card">
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
            <div className="text-sm font-medium">Фильтр качества</div>
            <div className="text-xs text-muted">
              Отбраковывает неудачные варианты ответа модели. Если выключить —
              бот отправит первый сгенерированный вариант без проверки.
            </div>
          </div>
        </label>
      </div>

      <div className="card">
        <div className="label">Ответы в группах</div>
        <div className="text-xs text-muted mt-1">
          Как бот реагирует на сообщения в групповых чатах.
        </div>
        <div className="space-y-1 mt-2">
          {[
            {
              v: "mention",
              label:
                "Только когда упомянули бота (@) или ответили на его сообщение",
            },
            { v: "all", label: "На все сообщения в группе" },
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

      <div className="card">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="label">Расписание</div>
            <div className="text-xs text-muted mt-1">
              Ограничивает только авто-ответы. Вне расписания сообщения
              попадут в ожидание.
            </div>
          </div>
          <span
            className={`px-3 py-1 rounded-full text-xs ${
              schedule.active ? "bg-good/15 text-good" : "bg-bad/15 text-bad"
            }`}
          >
            {schedule.active ? "Сейчас активен 🟢" : "Сейчас не активен 🔴"}
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
            <div className="text-sm font-medium">Ограничить часы работы</div>
            <div className="text-xs text-muted">
              Если выключено — бот отвечает всегда, как раньше.
            </div>
          </div>
        </label>

        <div className="grid md:grid-cols-3 gap-3 mt-4">
          <div>
            <div className="label">Часовой пояс</div>
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
            <div className="label">От</div>
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
            <div className="label">До</div>
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
          <div className="label">Дни недели</div>
          <div className="flex flex-wrap gap-2 mt-2">
            {dayOptions.map((d) => (
              <label
                key={d.value}
                className="flex items-center gap-2 text-sm rounded-lg border border-line px-3 py-2"
              >
                <input
                  type="checkbox"
                  checked={schedule.days.includes(d.value)}
                  onChange={() => toggleScheduleDay(d.value)}
                />
                {d.label}
              </label>
            ))}
          </div>
        </div>

        {!schedule.active && schedule.next_active_text && (
          <div className="text-sm text-muted mt-4">
            Следующий период: {schedule.next_active_text}
          </div>
        )}
      </div>

      <div className="card">
        <div className="label">Задержка ответа</div>
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
              Имитировать время обдумывания
            </div>
            <div className="text-xs text-muted">
              Если за это время придёт новое сообщение из того же чата, старый
              ответ отменится и бот подготовит новый по полной истории.
            </div>
          </div>
        </label>

        <div className="grid md:grid-cols-2 gap-4 mt-4">
          <div>
            <div className="flex justify-between text-sm">
              <span>От {delay.delay_min_seconds} сек</span>
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
              <span>До {delay.delay_max_seconds} сек</span>
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
          Бот будет отвечать через {formatDelayPreview(delay.delay_min_seconds)}–
          {formatDelayPreview(delay.delay_max_seconds)} минуты
        </div>
      </div>


      <div className="card">
        <div className="label">Чаты под наблюдением</div>
        <div className="text-xs text-muted mt-1">
          Если ничего не выбрано — отвечает во всех чатах.
        </div>
        <div className="mt-2 space-y-1 max-h-72 overflow-auto">
          {chats.length === 0 && (
            <div className="text-sm text-muted">Чатов ещё не было.</div>
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
                {c.count} сообщ.
              </span>
            </label>
          ))}
        </div>
      </div>

      <div className="flex gap-2">
        <button className="btn-primary" onClick={save} disabled={saving}>
          Сохранить
        </button>
        <button className="btn-secondary" onClick={runTest}>
          Test model
        </button>
        {error && <span className="text-bad text-sm self-center">{error}</span>}
      </div>

      <div className="card">
        <div className="label">Уведомления</div>
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
              Уведомлять когда бот отвечает
            </div>
            <div className="text-xs text-muted">
              Бот пришлёт сообщение в указанный чат после каждого авто-ответа.
            </div>
          </div>
        </label>

        <div className="mt-3">
          <div className="label">Ваш chat_id</div>
          <div className="flex gap-2 mt-1">
            <input
              className="input flex-1"
              placeholder="например, 123456789"
              value={notify.chat_id}
              onChange={(e) =>
                setNotify((prev) => ({ ...prev, chat_id: e.target.value }))
              }
            />
            <button className="btn-secondary" onClick={detectNotify}>
              Определить автоматически
            </button>
          </div>
          <div className="text-xs text-muted mt-1">
            Напишите боту /start в личку, затем нажмите «Определить автоматически».
          </div>
        </div>

        <div className="flex gap-2 mt-3 items-center">
          <button
            className="btn-primary"
            onClick={saveNotify}
            disabled={notifySaving}
          >
            Сохранить уведомления
          </button>
          {notifyMsg && (
            <span className="text-good text-sm">{notifyMsg}</span>
          )}
          {notifyError && (
            <span className="text-bad text-sm">{notifyError}</span>
          )}
        </div>
      </div>

      <div className="card">
        <div className="label">Admin Panel</div>
        <div className="text-xs text-muted mt-1">
          Бот принимает админ-команды только из указанного чата. Напишите
          /start боту в личку для активации.
        </div>

        <div className="mt-3">
          <div className="label">OWNER_CHAT_ID</div>
          <div className="flex gap-2 mt-1">
            <input
              className="input flex-1"
              placeholder="например, 123456789"
              value={admin.owner_chat_id}
              onChange={(e) =>
                setAdmin((prev) => ({ ...prev, owner_chat_id: e.target.value }))
              }
            />
            <button className="btn-secondary" onClick={detectOwner}>
              Определить автоматически
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
              Уведомления об авто-ответах
            </div>
            <div className="text-xs text-muted">
              Бот пришлёт уведомление с кнопками оценки после каждого
              авто-ответа.
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
              Уведомления о новых сообщениях
            </div>
            <div className="text-xs text-muted">
              Бот сообщит о сообщениях, попавших в очередь на ручной ответ.
            </div>
          </div>
        </label>

        <div className="flex flex-wrap gap-2 mt-3 items-center">
          <button
            className="btn-primary"
            onClick={saveAdmin}
            disabled={adminSaving}
          >
            Сохранить
          </button>
          <button className="btn-secondary" onClick={testAdminNotify}>
            Отправить тестовое уведомление
          </button>
          {adminMsg && <span className="text-good text-sm">{adminMsg}</span>}
          {adminError && (
            <span className="text-bad text-sm">{adminError}</span>
          )}
        </div>
      </div>

      <div className="card">
        <div className="flex items-center justify-between gap-3">
          <div className="label">Голосовые сообщения</div>
          <span
            className={`px-2 py-0.5 rounded-full text-xs ${
              whisper.device === "cuda"
                ? "bg-good/15 text-good"
                : "bg-bad/15 text-bad"
            }`}
          >
            {whisper.device === "cuda"
              ? "Whisper использует: CUDA ✅"
              : "CPU ⚠️ (медленно)"}
          </span>
        </div>

        {!whisper.ffmpeg_available && (
          <div className="text-xs text-bad mt-2">
            ⚠️ ffmpeg не установлен или не в PATH. Установи: <code>winget install ffmpeg</code> и перезапусти терминал.
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
            <div className="text-sm font-medium">Транскрибировать голосовые</div>
            <div className="text-xs text-muted">
              Использует локальную модель Whisper для перевода речи в текст.
            </div>
          </div>
        </label>

        {whisper.whisper_enabled && (
          <>
            <div className="grid md:grid-cols-2 gap-3 mt-4">
              <div>
                <div className="label">Модель</div>
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
                <div className="label">Язык</div>
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
                  <option value="ru">Русский</option>
                  <option value="en">English</option>
                  <option value="auto">Автоопределение</option>
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
                  Освобождать VRAM после транскрипции (lazy_load)
                </div>
                <div className="text-xs text-muted">
                  Whisper загружается перед обработкой и выгружается сразу
                  после. Полезно если 12GB VRAM делит с крупной LLM.
                </div>
              </div>
            </label>

            <div className="mt-4">
              <div className="label">Реакция на голосовые</div>
              <div className="space-y-1 mt-2">
                {[
                  { v: "text", label: "Отвечать текстом автоматически" },
                  {
                    v: "pending",
                    label: "Добавлять в очередь для ручного ответа",
                  },
                  { v: "skip", label: "Игнорировать голосовые" },
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
            Сохранить
          </button>
          {whisper.model_loaded && (
            <button className="btn-secondary" onClick={unloadWhisper}>
              Выгрузить Whisper из VRAM
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

      <div className="card">
        <div className="label">Векторная память (RAG)</div>
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
              Искать контекст по всей истории
            </div>
            <div className="text-xs text-muted">
              Бот находит релевантные сообщения из всей переписки, а не только
              последние 8.
            </div>
          </div>
        </label>

        {rag.rag_enabled && (
          <>
            <div className="mt-4">
              <div className="flex justify-between text-sm">
                <span>
                  Минимальная схожесть: {Number(rag.min_similarity).toFixed(2)}
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
                Ниже = больше результатов, но менее точные.
              </div>
            </div>

            <div className="mt-4">
              <div className="label">Максимум результатов</div>
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
                <div className="text-sm font-medium">Искать по всем чатам</div>
                <div className="text-xs text-muted">
                  Находит повторяющиеся темы у разных собеседников.
                </div>
              </div>
            </label>

            {rag.search_cross_chat && (
              <div className="mt-4">
                <div className="flex justify-between text-sm">
                  <span>
                    Порог для других чатов:{" "}
                    {Number(rag.cross_chat_min_similarity).toFixed(2)}
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
            Сохранить
          </button>
          {ragMsg && <span className="text-good text-sm">{ragMsg}</span>}
          {ragErr && <span className="text-bad text-sm">{ragErr}</span>}
        </div>
      </div>

      {test && (
        <div className="card">
          <div className="label">Результат теста</div>
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
    </div>
  );
}
