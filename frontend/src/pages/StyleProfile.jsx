import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";

const FIELDS = [
  ["avg_message_length", "Средняя длина сообщения", "number"],
  ["uses_emoji", "Использует эмодзи", "bool"],
  ["emoji_frequency", "Частота эмодзи", "number"],
  ["uses_lowercase", "Пишет в нижнем регистре", "bool"],
  ["punctuation_style", "Стиль пунктуации", "string"],
  ["tone", "Тон", "string"],
  ["avg_response_delay_minutes", "Средняя задержка ответа (мин)", "number"],
];

const LIST_FIELDS = [
  ["common_words", "Частые слова"],
  ["greeting_patterns", "Приветствия"],
  ["farewell_patterns", "Прощания"],
];

export default function StyleProfile() {
  const [profile, setProfile] = useState(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const refresh = async () => {
    try {
      const p = await api.styleProfile();
      setProfile(p && Object.keys(p).length ? p : { samples: [] });
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  function updateField(key, value) {
    setProfile((prev) => ({ ...(prev || {}), [key]: value }));
  }

  function updateList(key, idx, value) {
    setProfile((prev) => {
      const arr = [...((prev || {})[key] || [])];
      arr[idx] = value;
      return { ...(prev || {}), [key]: arr };
    });
  }

  function addToList(key) {
    setProfile((prev) => ({
      ...(prev || {}),
      [key]: [...((prev || {})[key] || []), ""],
    }));
  }

  function removeFromList(key, idx) {
    setProfile((prev) => {
      const arr = [...((prev || {})[key] || [])];
      arr.splice(idx, 1);
      return { ...(prev || {}), [key]: arr };
    });
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      await api.saveStyle(profile);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function reanalyze() {
    setSaving(true);
    try {
      const p = await api.reanalyzeStyle();
      setProfile(p);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  if (!profile) return <div className="card text-muted">Загрузка...</div>;

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <button className="btn-primary" onClick={save} disabled={saving}>
          Сохранить
        </button>
        <button className="btn-secondary" onClick={reanalyze} disabled={saving}>
          Пересоздать из БД
        </button>
        {error && <span className="text-bad text-sm self-center">{error}</span>}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {FIELDS.map(([key, label, type]) => (
          <div key={key} className="card">
            <div className="label">{label}</div>
            {type === "bool" ? (
              <label className="flex items-center gap-2 mt-2 text-sm">
                <input
                  type="checkbox"
                  checked={!!profile[key]}
                  onChange={(e) => updateField(key, e.target.checked)}
                />
                {profile[key] ? "да" : "нет"}
              </label>
            ) : (
              <input
                className="input mt-1"
                type={type === "number" ? "number" : "text"}
                step="0.01"
                value={profile[key] ?? ""}
                onChange={(e) =>
                  updateField(
                    key,
                    type === "number" ? parseFloat(e.target.value) || 0 : e.target.value
                  )
                }
              />
            )}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {LIST_FIELDS.map(([key, label]) => (
          <div key={key} className="card">
            <div className="label flex items-center justify-between">
              <span>{label}</span>
              <button className="text-accent text-xs" onClick={() => addToList(key)}>
                + добавить
              </button>
            </div>
            <div className="mt-2 space-y-1">
              {(profile[key] || []).map((v, i) => (
                <div key={i} className="flex gap-1">
                  <input
                    className="input"
                    value={v}
                    onChange={(e) => updateList(key, i, e.target.value)}
                  />
                  <button
                    className="btn-secondary"
                    onClick={() => removeFromList(key, i)}
                  >
                    ✕
                  </button>
                </div>
              ))}
              {(profile[key] || []).length === 0 && (
                <div className="text-sm text-muted">пусто</div>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="label">Примеры моих сообщений</div>
        <div className="mt-2 space-y-1 text-sm">
          {(profile.samples || []).map((s, i) => (
            <div key={i} className="text-muted border-l-2 border-white/10 pl-2">
              {s}
            </div>
          ))}
          {!(profile.samples || []).length && (
            <div className="text-muted">пусто</div>
          )}
        </div>
      </div>
    </div>
  );
}
