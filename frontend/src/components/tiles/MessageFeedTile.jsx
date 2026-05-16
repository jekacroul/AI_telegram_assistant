import React from "react";
import { GripVertical, Mic } from "lucide-react";
import Badge from "../Badge.jsx";

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

export default function MessageFeedTile({ dragHandleProps, messages = [], onSelect }) {
  return (
    <div className="tile">
      <div className="flex items-center justify-between mb-3">
        <span className="tile-label mb-0">Лента сообщений</span>
        <div className="flex items-center gap-2">
          <Badge variant="zinc">{messages.length}</Badge>
          <span
            {...dragHandleProps}
            className="cursor-grab active:cursor-grabbing text-zinc-300
                       dark:text-slate-600 hover:text-zinc-500
                       dark:hover:text-slate-400 touch-none"
          >
            <GripVertical size={16} />
          </span>
        </div>
      </div>

      <div className="space-y-1.5 max-h-[340px] overflow-y-auto pr-1">
        {messages.length === 0 && (
          <div className="text-xs text-zinc-400 dark:text-slate-500 py-6 text-center">
            Сообщений пока нет
          </div>
        )}
        {messages.map((m) => {
          const canReply = !m.is_mine && !m.replied;
          const badge = m.is_mine
            ? null
            : m.replied
            ? { variant: "green", text: "отвечено" }
            : { variant: "amber", text: "ожидает" };
          return (
            <div
              key={m.id}
              className={`msg-row ${canReply && onSelect ? "" : "cursor-default"}`}
              onClick={() => canReply && onSelect?.(m)}
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
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-zinc-800 dark:text-slate-200 truncate">
                    {m.sender_name || "—"}
                  </span>
                  <span className="text-[10px] text-zinc-400 dark:text-slate-500 flex-shrink-0">
                    {timeLabel(m.timestamp)}
                  </span>
                </div>
                <div className="flex items-center gap-2 mt-0.5">
                  {m.is_voice ? (
                    <span className="flex items-center gap-1 text-[11px] text-sky-600 dark:text-sky-400">
                      <Mic size={11} /> голосовое
                    </span>
                  ) : (
                    <span className="text-[11px] text-zinc-500 dark:text-slate-400 truncate">
                      {m.text || "—"}
                    </span>
                  )}
                </div>
              </div>
              {badge && (
                <Badge variant={badge.variant} className="flex-shrink-0">
                  {badge.text}
                </Badge>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
