import React from "react";

export function Skeleton({ className = "" }) {
  return <div className={`skeleton ${className}`} />;
}

export function MessageListSkeleton({ rows = 5 }) {
  return (
    <div className="space-y-1">
      {Array.from({ length: rows }).map((_, i) => (
        <div className="flex items-start gap-3 p-3" key={i}>
          <Skeleton className="w-9 h-9 rounded-full" />
          <div className="flex-1 space-y-2 pt-1">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-3 w-48" />
          </div>
        </div>
      ))}
    </div>
  );
}
