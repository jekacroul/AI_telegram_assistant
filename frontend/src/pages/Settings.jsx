import React, { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api.js";

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

  const modelOptions = useMemo(() => {
    const list = [...models];
    if (s.llm_model && !list.includes(s.llm_model)) {
      list.unshift(s.llm_model);
    }
    return list;
  }, [models, s.llm_model]);

  const refresh = async () => {
    const [cs, st] = await Promise.all([api.chats(), api.getSettings()]);
    setChats(cs);
    setS((prev) => ({
      ...prev,
      auto_reply: !!st.auto_reply,
      monitored_chats: st.monitored_chats || [],
      llm_model: st.llm_model || "",
    }));
    try {
      const m = await api.listModels();
      setModels(m.models || []);
      setModelsError("");
    } catch (e) {
      setModels([]);
      setModelsError(e.message || "не удалось получить список моделей");
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  async function save() {
    setSaving(true);
    setError("");
    try {
      await api.saveSettings(s);
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
