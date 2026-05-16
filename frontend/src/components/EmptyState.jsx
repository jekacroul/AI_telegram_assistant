import React from "react";
import { Inbox } from "lucide-react";

export default function EmptyState({ icon: Icon = Inbox, title, description }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div
        className="w-12 h-12 rounded-xl bg-surface
                   flex items-center justify-center mb-4"
      >
        <Icon size={20} className="text-muted" />
      </div>
      {title && (
        <p className="text-sm font-medium text-fg mb-1">{title}</p>
      )}
      {description && (
        <p className="text-xs text-muted max-w-xs">{description}</p>
      )}
    </div>
  );
}
