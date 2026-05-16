import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";

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

function formatDuration(seconds) {
  if (!seconds || seconds < 0) return "0:00";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function MessageCard({ msg, onReply, onFeedback }) {
  const ts = msg.timestamp ? new Date(msg.timestamp).toLocaleString() : "";
  const isMine = msg.is_mine;
  const [openMedia, setOpenMedia] = useState(false);
  const [openTranscription, setOpenTranscription] = useState(false);
  const [transcription, setTranscription] = useState(msg.transcription || "");
  const [retranscribing, setRetranscribing] = useState(false);
  const [retErr, setRetErr] = useState("");

  useEffect(() => {
    setTranscription(msg.transcription || "");
  }, [msg.transcription]);
  const username = (msg.chat_username || "").trim();
  const usernameLabel = username ? ` @${username}` : "";

  async function retranscribe() {
    setRetranscribing(true);
    setRetErr("");
    try {
      const res = await api.whisperRetranscribe(msg.id);
      setTranscription(res.transcription || "");
      setOpenTranscription(true);
    } catch (e) {
      setRetErr(e.message);
    } finally {
      setRetranscribing(false);
    }
  }

  const isVoice = !!msg.is_voice;

  return (
    <div className="card flex gap-3 items-start">
      <div
        className={`w-9 h-9 rounded-full flex items-center justify-center text-xs font-medium flex-shrink-0 ${
          isMine ? "bg-accent text-accent-fg" : "bg-surface text-muted"
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

        {isVoice && (
          <div className="mt-1">
            <div className="flex items-center gap-2 text-sm">
              <span className="text-lg" aria-label="voice">🎤</span>
              <span className="text-muted">
                Голосовое · {formatDuration(msg.voice_duration)}
              </span>
              {transcription && (
                <button
                  className="text-xs text-accent underline ml-2"
                  onClick={() => setOpenTranscription((v) => !v)}
                >
                  {openTranscription ? "Скрыть текст" : "Показать текст"}
                </button>
              )}
            </div>
            {msg.transcription_low_confidence && (
              <div className="mt-1 text-xs text-bad">
                ⚠️ Низкая уверенность — проверь транскрипцию
              </div>
            )}
            {msg.transcription_error && (
              <div className="mt-1 text-xs text-bad">
                Ошибка транскрипции: {msg.transcription_error}
              </div>
            )}
            {openTranscription && (
              <div className="mt-2 text-sm text-muted whitespace-pre-wrap">
                {transcription || "(пусто)"}
              </div>
            )}
            <div className="mt-2 flex gap-2">
              <button
                className="btn-secondary text-xs"
                disabled={retranscribing}
                onClick={retranscribe}
              >
                {retranscribing ? "Транскрибирую..." : "Перегенерировать транскрипцию"}
              </button>
              {retErr && (
                <span className="text-bad text-xs self-center">{retErr}</span>
              )}
            </div>
          </div>
        )}

        {!isVoice &&
          msg.text &&
          !(msg.media_path && isMediaPlaceholder(msg.text)) && (
            <div className="text-sm whitespace-pre-wrap break-words">{msg.text}</div>
          )}
        {msg.media_type === "photo" && msg.media_path && (
          <button className="mt-2 block" onClick={() => setOpenMedia(true)}>
            <img
              src={msg.media_path}
              alt="photo"
              className="max-h-56 rounded-lg border border-line object-cover"
            />
          </button>
        )}
        {(msg.media_type === "video" || msg.media_type === "video_note") &&
          msg.media_path && (
            <button className="mt-2 block" onClick={() => setOpenMedia(true)}>
              <video
                src={msg.media_path}
                className="max-h-56 rounded-lg border border-line"
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
