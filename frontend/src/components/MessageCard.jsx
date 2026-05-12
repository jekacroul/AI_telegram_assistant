export default function MessageCard({ msg, onReply, onFeedback }) {
  const ts = msg.timestamp ? new Date(msg.timestamp).toLocaleString() : "";
  const isMine = msg.is_mine;

  function initials(name) {
    if (!name) return "?";
    const parts = name.trim().split(/\s+/);
    return (parts[0][0] + (parts[1]?.[0] || "")).toUpperCase();
  }

  const renderMedia = () => {
    if (!msg.media_type) return null;
    
    if (msg.is_view_once) {
      // View-once media cannot be displayed per Telegram policy
      return (
        <div className="mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
          <span className="text-sm text-muted">🔒 Одноразовое медиа (недоступно для просмотра)</span>
        </div>
      );
    }

    if (msg.media_type === "photo" && msg.media_file_id) {
      return (
        <div className="mt-2">
          <img 
            src={`https://api.telegram.org/file/bot${window.TELEGRAM_BOT_TOKEN}/${msg.media_file_id}`} 
            alt="Photo" 
            className="max-w-xs rounded-lg shadow-lg"
            onError={(e) => {
              e.target.style.display = 'none';
              e.target.nextSibling && (e.target.nextSibling.style.display = 'block');
            }}
          />
          <div className="hidden mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
            <span className="text-sm text-muted">📷 Фото (недоступно для прямого просмотра)</span>
          </div>
        </div>
      );
    }
    
    if (msg.media_type === "video_note" && msg.media_file_id) {
      return (
        <div className="mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
          <span className="text-sm text-muted">⭕ Кружок (видеосообщение)</span>
          <div className="text-xs text-muted mt-1">File ID: {msg.media_file_id}</div>
        </div>
      );
    }
    
    if (msg.media_type === "video" && msg.media_file_id) {
      return (
        <div className="mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
          <span className="text-sm text-muted">🎬 Видео</span>
          <div className="text-xs text-muted mt-1">File ID: {msg.media_file_id}</div>
        </div>
      );
    }
    
    if (msg.media_type === "animation" && msg.media_file_id) {
      return (
        <div className="mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
          <span className="text-sm text-muted">🎬 GIF</span>
          <div className="text-xs text-muted mt-1">File ID: {msg.media_file_id}</div>
        </div>
      );
    }
    
    if (msg.media_type === "voice" && msg.media_file_id) {
      return (
        <div className="mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
          <span className="text-sm text-muted">🎤 Голосовое сообщение</span>
          <div className="text-xs text-muted mt-1">File ID: {msg.media_file_id}</div>
        </div>
      );
    }
    
    if (msg.media_type === "audio" && msg.media_file_id) {
      return (
        <div className="mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
          <span className="text-sm text-muted">🎵 Аудио</span>
          <div className="text-xs text-muted mt-1">File ID: {msg.media_file_id}</div>
        </div>
      );
    }
    
    if (msg.media_type === "document" && msg.media_file_id) {
      return (
        <div className="mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
          <span className="text-sm text-muted">📄 Документ</span>
          <div className="text-xs text-muted mt-1">File ID: {msg.media_file_id}</div>
        </div>
      );
    }
    
    if (msg.media_type === "sticker" && msg.media_file_id) {
      return (
        <div className="mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
          <span className="text-sm text-muted">😊 Стикер</span>
          <div className="text-xs text-muted mt-1">File ID: {msg.media_file_id}</div>
        </div>
      );
    }
    
    return null;
  };

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
          <span className="text-xs text-muted">в {msg.chat_name}</span>
          <span className="text-xs text-muted ml-auto">{ts}</span>
        </div>
        <div className="text-sm whitespace-pre-wrap break-words">{msg.text}</div>
        {renderMedia()}
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
    </div>
  );
}
