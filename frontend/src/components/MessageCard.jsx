import React, { useState } from "react";

function initials(name) {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  return (parts[0][0] + (parts[1]?.[0] || "")).toUpperCase();
}
function isMediaPlaceholder(text) {
  return ["(фото)", "(видео)", "(кружок)", "(photo)", "(video)"].includes(
    (text || "").trim().toLowerCase()
  );
}

export default function MessageCard({ msg, onReply, onFeedback }) {
  const ts = msg.timestamp ? new Date(msg.timestamp).toLocaleString() : "";
  const isMine = msg.is_mine;
  const [openMedia, setOpenMedia] = useState(false);
  const username = (msg.chat_username || "").trim();
  const usernameLabel = username ? ` @${username}` : "";

  return (
    <div className="card flex gap-3 items-start">
      <div
        className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-semibold ${
          isMine ? "bg-accent/30 text-accent" : "bg-white/10 text-white"
        }`}
      >
        {initials(msg.sender_name)}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-medium truncate">{msg.sender_name}</span>
          <span className="text-xs text-muted">
            в чате {msg.chat_name}
            {usernameLabel}
          </span>
          <span className="text-xs text-muted ml-auto">{ts}</span>
        </div>
        {msg.text && !(msg.media_path && isMediaPlaceholder(msg.text)) && (
          <div className="text-sm whitespace-pre-wrap break-words">{msg.text}</div>
        )}
        {msg.media_type === "photo" && msg.media_path && (
          <button className="mt-2 block" onClick={() => setOpenMedia(true)}>
            <img
              src={msg.media_path}
              alt="photo"
              className="max-h-56 rounded-lg border border-white/10 object-cover"
            />
          </button>
        )}
        {(msg.media_type === "video" || msg.media_type === "video_note") &&
          msg.media_path && (
            <button className="mt-2 block" onClick={() => setOpenMedia(true)}>
              <video
                src={msg.media_path}
                className="max-h-56 rounded-lg border border-white/10"
              />
            </button>
          )}
        {msg.media_private && !msg.media_path && (
          <div className="mt-2 text-xs text-muted">
            Приватное {msg.media_type === "photo" ? "фото" : "видео"} (одноразовое)
          </div>
        )}
        {msg.replied && msg.reply_text && (
          <div className="mt-2 pl-3 border-l-2 border-accent/50 text-sm text-muted">
            <div className="text-[10px] uppercase tracking-wide mb-1">
              Ответ ассистента
            </div>
            <div className="whitespace-pre-wrap">{msg.reply_text}</div>
          </div>
        )}
        <div className="mt-2 flex gap-2">
          {!isMine && !msg.replied && onReply && (
            <button className="btn-primary" onClick={() => onReply(msg)}>
              Ответить
            </button>
          )}
          {msg.replied && onFeedback && (
            <>
              <button
                className="btn-secondary"
                onClick={() => onFeedback(msg, "good")}
              >
                👍
              </button>
              <button
                className="btn-secondary"
                onClick={() => onFeedback(msg, "bad")}
              >
                👎
              </button>
            </>
          )}
        </div>
      </div>
      {openMedia && msg.media_path && (
        <div
          className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center p-4"
          onClick={() => setOpenMedia(false)}
        >
          {msg.media_type === "photo" ? (
            <img
              src={msg.media_path}
              alt="photo fullscreen"
              className="max-w-full max-h-full object-contain"
            />
          ) : (
            <video
              src={msg.media_path}
              controls
              autoPlay
              className="max-w-full max-h-full"
            />
          )}
        </div>
      )}
    </div>
  );
}
