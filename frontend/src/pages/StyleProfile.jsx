import React, { useEffect, useMemo, useState } from "react";
import { Sparkles, SlidersHorizontal, RotateCcw, Trash2 } from "lucide-react";
import { api } from "../lib/api.js";
import { useLang } from "../hooks/useLang.js";
import Page from "../components/Page.jsx";
import Tile from "../components/Tile.jsx";

const FIELDS = [
  ["avg_message_length", "number"],
  ["uses_emoji", "bool"],
  ["emoji_frequency", "number"],
  ["uses_lowercase", "bool"],
  ["punctuation_style", "string"],
  ["tone", "string"],
  ["avg_response_delay_minutes", "number"],
];

function ProfileEditor({ profile, setProfile }) {
  const { t } = useLang();
  const updateField = (key, value) =>
    setProfile((p) => ({ ...(p || {}), [key]: value }));
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-3">
      {FIELDS.map(([key, type]) => (
        <div key={key}>
          <div className="tile-label">{t(`style.fields.${key}`)}</div>
          {type === "bool" ? (
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={!!profile?.[key]}
                onChange={(e) => updateField(key, e.target.checked)}
              />
              {profile?.[key] ? t("common.yes") : t("common.no")}
            </label>
          ) : (
            <input
              className="input"
              type={type === "number" ? "number" : "text"}
              step="0.01"
              value={profile?.[key] ?? ""}
              onChange={(e) =>
                updateField(
                  key,
                  type === "number"
                    ? parseFloat(e.target.value) || 0
                    : e.target.value
                )
              }
            />
          )}
        </div>
      ))}
    </div>
  );
}

export default function StyleProfile() {
  const { t } = useLang();
  const [settings, setSettings] = useState({ persona_mode: "global" });
  const [globalProfile, setGlobalProfile] = useState({});
  const [personas, setPersonas] = useState([]);
  const [chats, setChats] = useState([]);
  const [selected, setSelected] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const refresh = async () => {
    setError("");
    const [s, gp, ps, cs] = await Promise.all([
      api.getSettings(),
      api.styleProfile(),
      api.personas(),
      api.chats(),
    ]);
    setSettings(s);
    setGlobalProfile(gp || {});
    setPersonas(ps || []);
    setChats(cs || []);
  };

  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, []);

  const saveMode = async (mode) => {
    setSaving(true);
    try {
      await api.saveSettings({ persona_mode: mode });
      setSettings((p) => ({ ...p, persona_mode: mode }));
    } finally {
      setSaving(false);
    }
  };

  const chatCards = useMemo(() => {
    const byId = new Map((personas || []).map((p) => [p.chat_id, p]));
    return (chats || []).map((c) => {
      const persona = byId.get(c.chat_id);
      return {
        chat_id: c.chat_id,
        chat_name: c.chat_name,
        updated_at: persona?.updated_at || null,
        profile: persona?.profile || null,
      };
    });
  }, [personas, chats]);

  const isGlobal = settings.persona_mode === "global";

  return (
    <Page>
      <Tile title={t("nav.style")} icon={SlidersHorizontal}>
        <div className="flex flex-wrap gap-2">
          <button
            className={isGlobal ? "btn-primary" : "btn-ghost"}
            onClick={() => saveMode("global")}
            disabled={saving}
          >
            {t("style.globalStyle")}
          </button>
          <button
            className={!isGlobal ? "btn-primary" : "btn-ghost"}
            onClick={() => saveMode("per_chat")}
            disabled={saving}
          >
            {t("style.perChatStyle")}
          </button>
        </div>
        {error && (
          <p className="text-xs text-rose-500 dark:text-rose-400 mt-2">
            {error}
          </p>
        )}
      </Tile>

      {isGlobal && (
        <Tile
          title={t("style.globalStyle")}
          icon={Sparkles}
          actions={
            <>
              <button
                className="btn-ghost"
                disabled={saving}
                onClick={async () => {
                  setSaving(true);
                  try {
                    setGlobalProfile(await api.reanalyzeStyle());
                  } finally {
                    setSaving(false);
                  }
                }}
              >
                <RotateCcw size={14} />
                {t("style.recalculate")}
              </button>
              <button
                className="btn-primary"
                disabled={saving}
                onClick={async () => {
                  setSaving(true);
                  try {
                    await api.saveStyle(globalProfile || {});
                  } finally {
                    setSaving(false);
                  }
                }}
              >
                {t("common.save")}
              </button>
            </>
          }
        >
          <ProfileEditor
            profile={globalProfile}
            setProfile={setGlobalProfile}
          />
        </Tile>
      )}

      {!isGlobal && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {chatCards.map((p) => {
              const active = selected?.chat_id === p.chat_id;
              return (
                <div
                  key={p.chat_id}
                  className={`tile cursor-pointer ${
                    active
                      ? "ring-2 ring-indigo-500 dark:ring-indigo-400"
                      : ""
                  }`}
                  onClick={() =>
                    setSelected({
                      ...p,
                      profile: p.profile || { ...globalProfile },
                    })
                  }
                >
                  <div className="font-semibold text-sm text-zinc-900 dark:text-slate-100 truncate mb-1.5">
                    {p.chat_name || p.chat_id}
                  </div>
                  <div className="text-xs text-zinc-500 dark:text-slate-400">
                    {t("style.tone", {
                      value: p.profile?.tone || t("common.dash"),
                    })}
                  </div>
                  <div className="text-xs text-zinc-500 dark:text-slate-400">
                    {t("style.avgLength", {
                      value: p.profile?.avg_message_length || 0,
                    })}
                  </div>
                  <div className="text-[11px] text-zinc-400 dark:text-slate-500 mt-1">
                    {t("style.updated", {
                      value: p.updated_at || t("common.none"),
                    })}
                  </div>
                </div>
              );
            })}
          </div>

          {!chatCards.length && (
            <Tile>
              <p className="text-xs text-zinc-400 dark:text-slate-500 py-2 text-center">
                {t("style.noChats")}
              </p>
            </Tile>
          )}

          {selected && (
            <Tile
              title={t("style.editing", {
                name: selected.chat_name || selected.chat_id,
              })}
              icon={Sparkles}
              actions={
                <>
                  <button
                    className="btn-ghost"
                    disabled={saving}
                    onClick={async () => {
                      setSaving(true);
                      try {
                        const p = await api.reanalyzePersona(
                          selected.chat_id
                        );
                        setSelected((s) => ({ ...s, profile: p }));
                        await refresh();
                      } finally {
                        setSaving(false);
                      }
                    }}
                  >
                    <RotateCcw size={14} />
                    {t("style.recalculate")}
                  </button>
                  <button
                    className="btn-danger"
                    disabled={saving}
                    onClick={async () => {
                      setSaving(true);
                      try {
                        await api.deletePersona(selected.chat_id);
                        setSelected(null);
                        await refresh();
                      } finally {
                        setSaving(false);
                      }
                    }}
                  >
                    <Trash2 size={14} />
                  </button>
                  <button
                    className="btn-primary"
                    disabled={saving}
                    onClick={async () => {
                      setSaving(true);
                      try {
                        await api.savePersona(
                          selected.chat_id,
                          selected.profile || {}
                        );
                        await refresh();
                      } finally {
                        setSaving(false);
                      }
                    }}
                  >
                    {t("common.save")}
                  </button>
                </>
              }
            >
              <ProfileEditor
                profile={selected.profile || {}}
                setProfile={(updater) =>
                  setSelected((prev) => ({
                    ...prev,
                    profile:
                      typeof updater === "function"
                        ? updater(prev.profile || {})
                        : updater,
                  }))
                }
              />
            </Tile>
          )}
        </>
      )}
    </Page>
  );
}
