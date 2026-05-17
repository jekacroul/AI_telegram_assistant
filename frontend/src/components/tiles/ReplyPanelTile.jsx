import React, { useState } from "react";
import { GripVertical, Mic, Inbox, CheckCheck } from "lucide-react";
import Toggle from "../Toggle.jsx";
import { useLang } from "../../hooks/useLang.js";

const AVATARS = [
  "bg-sky-100 dark:bg-sky-950/40 text-sky-600 dark:text-sky-400",
  "bg-emerald-100 dark:bg-emerald-950/40 text-emerald-600 dark:text-emerald-400",
  "bg-violet-100 dark:bg-violet-950/40 text-violet-600 dark:text-violet-400",
  "bg-amber-100 dark:bg-amber-950/40 text-amber-600 dark:text-amber-400",
];

function initials(name) {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  return (parts[0][0] + (parts[1]?.[0] || "")).toUpperCase();
}

function avatarClass(name) {
  let h = 0;
  for (let i = 0; i < (name || "").length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  return AVATARS[Math.abs(h) % AVATARS.length];
}

function timeLabel(ts) {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ReplyPanelTile({
  dragHandleProps,
  status = {},
  messages = [],
  count,
  onSelect,
  onToggleAuto,
  onClearQueue,
}) {
  const { t } = useLang();
  const pending = messages || [];
  const total = count ?? pending.length;
  const [clearing, setClearing] = useState(false);
  const [clearMsg, setClearMsg] = useState("");

  async function handleClear() {
    if (clearing || !onClearQueue) return;
    setClearing(true);
    setClearMsg("");
    try {
      const res = await onClearQueue();
      const cleared = res?.cleared ?? 0;
      setClearMsg(
        cleared > 0
          ? t("tiles.clearQueueDone") + cleared
          : t("tiles.clearQueueEmpty"),
      );
    } catch (e) {
      setClearMsg(e.message || "error");
    } finally {
      setClearing(false);
    }
  }

  return (
    <div className="tile flex flex-col">
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <span className="tile-label mb-0">{t("tiles.replyQueue")}</span>
        <span
          {...dragHandleProps}
          className="cursor-grab active:cursor-grabbing text-zinc-300
                     dark:text-slate-600 hover:text-zinc-500
                     dark:hover:text-slate-400 touch-none"
        >
          <GripVertical size={16} />
        </span>
      </div>

      <div className="flex items-center justify-between gap-3 mb-3 flex-shrink-0">
        <div className="flex items-center gap-2.5">
          <div
            className="w-11 h-11 rounded-xl flex items-center justify-center
                       bg-indigo-50 dark:bg-indigo-950/40
                       text-indigo-600 dark:text-indigo-400"
          >
            <Inbox size={20} />
          </div>
          <div>
            <div className="text-2xl font-bold leading-none text-zinc-900 dark:text-slate-100">
              {total}
            </div>
            <div className="text-xs text-zinc-400 dark:text-slate-500 mt-0.5">
              {t("tiles.waitingReply")}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-medium text-zinc-500 dark:text-slate-400">
            {t("tiles.autoReply")}
          </span>
          <Toggle checked={!!status.auto_reply} onChange={onToggleAuto} />
        </div>
      </div>

      {total > 0 && (
        <div className="flex items-center justify-between gap-2 mb-2 flex-shrink-0">
          <button
            className="btn-ghost text-[11px]"
            onClick={handleClear}
            disabled={clearing}
            title={t("tiles.clearQueueHint")}
          >
            <CheckCheck size={13} />
            {t("tiles.clearQueue")}
          </button>
          {clearMsg && (
            <span className="text-[10px] text-zinc-400 dark:text-slate-500 truncate">
              {clearMsg}
            </span>
          )}
        </div>
      )}

      <div className="space-y-1.5 flex-1 min-h-0 overflow-y-auto pr-1">
        {pending.length === 0 && (
          <div className="text-xs text-zinc-400 dark:text-slate-500 py-8 text-center">
            {t("tiles.queueEmpty")}
          </div>
        )}
        {pending.map((m) => {
          const uname = (m.chat_username || "").trim();
          let chatLabel = "";
          if (m.chat_name && m.chat_name !== m.sender_name) {
            chatLabel = m.chat_name;
          }
          if (uname)
            chatLabel = chatLabel ? `${chatLabel} · @${uname}` : `@${uname}`;

          return (
            <div
              key={m.id}
              className="msg-row"
              onClick={() => onSelect?.(m)}
            >
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center
                  flex-shrink-0 text-[11px] font-semibold ${avatarClass(
                    m.sender_name
                  )}`}
              >
                {initials(m.sender_name)}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-1.5">
                  <span className="text-xs font-semibold text-zinc-800 dark:text-slate-200 truncate flex-shrink-0 max-w-[55%]">
                    {m.sender_name || "—"}
                  </span>
                  {chatLabel && (
                    <span className="text-[10px] text-zinc-400 dark:text-slate-500 truncate">
                      {t("tiles.inChat")}
                      {chatLabel}
                    </span>
                  )}
                  <span className="text-[10px] text-zinc-400 dark:text-slate-500 flex-shrink-0 ml-auto">
                    {timeLabel(m.timestamp)}
                  </span>
                </div>
                <div className="mt-0.5">
                  {m.is_voice ? (
                    <span className="flex items-center gap-1 text-[11px] text-sky-600 dark:text-sky-400">
                      <Mic size={11} /> {t("tiles.voice")}
                    </span>
                  ) : (
                    <span className="text-[11px] text-zinc-500 dark:text-slate-400 truncate block">
                      {m.text || "—"}
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
