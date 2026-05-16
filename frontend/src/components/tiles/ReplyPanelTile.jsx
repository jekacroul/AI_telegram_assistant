import React from "react";
import { GripVertical, Inbox } from "lucide-react";
import StatusPill from "../StatusPill.jsx";
import Toggle from "../Toggle.jsx";

export default function ReplyPanelTile({
  dragHandleProps,
  status = {},
  ragOk,
  whisperOk,
  pendingCount = 0,
  onToggleAuto,
}) {
  return (
    <div className="tile">
      <div className="flex items-center justify-between mb-3">
        <span className="tile-label mb-0">Очередь ответов</span>
        <span
          {...dragHandleProps}
          className="cursor-grab active:cursor-grabbing text-zinc-300
                     dark:text-slate-600 hover:text-zinc-500
                     dark:hover:text-slate-400 touch-none"
        >
          <GripVertical size={16} />
        </span>
      </div>

      <div className="flex items-center gap-3 mb-4">
        <div
          className="w-11 h-11 rounded-xl flex items-center justify-center
                     bg-indigo-50 dark:bg-indigo-950/40
                     text-indigo-600 dark:text-indigo-400"
        >
          <Inbox size={20} />
        </div>
        <div>
          <div className="text-2xl font-bold leading-none text-zinc-900 dark:text-slate-100">
            {pendingCount}
          </div>
          <div className="text-xs text-zinc-400 dark:text-slate-500 mt-0.5">
            сообщений ждут ответа
          </div>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5 mb-4">
        <StatusPill ok={!!status.llm} label="LM Studio" />
        <StatusPill ok={!!status.bot} label="Бот" />
        <StatusPill ok={!!ragOk} label="RAG" />
        <StatusPill ok={!!whisperOk} pending={!whisperOk} label="Whisper" />
      </div>

      <div
        className="flex items-center justify-between p-3 rounded-lg
                   bg-light-card2 dark:bg-dark-card2
                   border border-light-border dark:border-dark-border"
      >
        <div>
          <div className="text-sm font-semibold text-zinc-800 dark:text-slate-200">
            Авто-ответ
          </div>
          <div className="text-xs text-zinc-400 dark:text-slate-500">
            {status.auto_reply
              ? "бот отвечает без подтверждения"
              : "ответы уходят в очередь"}
          </div>
        </div>
        <Toggle checked={!!status.auto_reply} onChange={onToggleAuto} />
      </div>
    </div>
  );
}
