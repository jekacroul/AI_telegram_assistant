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

    // Display media from local file path
    if (msg.media_file_path) {
      const mediaUrl = `/media/${msg.media_file_path.replace('media/', '')}`;
      
      if (msg.media_type === "photo") {
        return (
          <div className="mt-2">
            <img 
              src={mediaUrl} 
              alt="Photo" 
              className="max-w-xs rounded-lg shadow-lg"
            />
          </div>
        );
      }
      
      if (msg.media_type === "video_note") {
        return (
          <div className="mt-2">
            <video 
              src={mediaUrl} 
              controls 
              className="max-w-xs rounded-lg shadow-lg"
              style={{ borderRadius: '50%' }}
            />
          </div>
        );
      }
      
      if (msg.media_type === "video") {
        return (
          <div className="mt-2">
            <video 
              src={mediaUrl} 
              controls 
              className="max-w-xs rounded-lg shadow-lg"
            />
          </div>
        );
      }
      
      if (msg.media_type === "animation") {
        return (
          <div className="mt-2">
            <img 
              src={mediaUrl} 
              alt="GIF" 
              className="max-w-xs rounded-lg shadow-lg"
            />
          </div>
        );
      }
      
      if (msg.media_type === "voice") {
        return (
          <div className="mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
            <audio src={mediaUrl} controls />
            <span className="text-sm text-muted">🎤 Голосовое сообщение</span>
          </div>
        );
      }
      
      if (msg.media_type === "audio") {
        return (
          <div className="mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
            <audio src={mediaUrl} controls />
            <span className="text-sm text-muted">🎵 Аудио</span>
          </div>
        );
      }
      
      if (msg.media_type === "document") {
        return (
          <div className="mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
            <a href={mediaUrl} download className="text-sm text-accent hover:underline">
              📄 Скачать документ
            </a>
          </div>
        );
      }
      
      if (msg.media_type === "sticker") {
        return (
          <div className="mt-2">
            <img 
              src={mediaUrl} 
              alt="Sticker" 
              className="max-w-[150px] rounded-lg"
            />
          </div>
        );
      }
    }
    
    // Fallback for old messages without file path
    return (
      <div className="mt-2 p-3 bg-white/5 rounded-lg border border-white/10">
        <span className="text-sm text-muted">📎 Медиафайл (требуется повторное сохранение)</span>
      </div>
    );
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
