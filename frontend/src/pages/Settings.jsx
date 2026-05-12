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
  };

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
                className="flex items-center gap-2 text-sm rounded-lg border border-white/10 px-3 py-2"
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

      {test && (
        <div className="card">
          <div className="label">Результат теста</div>
          {test.loading && <div className="text-sm text-muted">...</div>}
          {test.error && <div className="text-bad text-sm">{test.error}</div>}
          {test.variants && (
            <ul className="mt-2 text-sm space-y-1">
              {test.variants.map((v, i) => (
                <li key={i} className="text-white">• {v}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
