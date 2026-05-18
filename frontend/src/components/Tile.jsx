import React from "react";

export default function Tile({
  title,
  icon: Icon,
  actions,
  className = "",
  bodyClassName = "",
  children,
}) {
  return (
    <div className={`tile ${className}`}>
      {(title || actions) && (
        <div className="flex items-center justify-between gap-2 mb-3">
          <div className="flex items-center gap-2 min-w-0">
            {Icon && (
              <Icon
                size={14}
                className="text-indigo-500 dark:text-indigo-400 flex-shrink-0"
              />
            )}
            {title && <span className="tile-label mb-0 truncate">{title}</span>}
          </div>
          {actions && (
            <div className="flex items-center gap-2 flex-shrink-0">
              {actions}
            </div>
          )}
        </div>
      )}
      <div className={bodyClassName}>{children}</div>
    </div>
  );
}
