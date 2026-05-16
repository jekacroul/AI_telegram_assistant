import React, { useState } from "react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { SortableContext, arrayMove } from "@dnd-kit/sortable";
import SortableTile from "./SortableTile.jsx";
import { DEFAULT_SIZES } from "../hooks/useTileLayout.js";

export default function TileGrid({ order, sizes, onReorder, onResize, renderTile }) {
  const [activeId, setActiveId] = useState(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } })
  );

  function handleDragEnd(event) {
    setActiveId(null);
    const { active, over } = event;
    if (over && active.id !== over.id) {
      const oldIndex = order.indexOf(active.id);
      const newIndex = order.indexOf(over.id);
      if (oldIndex !== -1 && newIndex !== -1) {
        onReorder(arrayMove(order, oldIndex, newIndex));
      }
    }
  }

  const activeSize = activeId
    ? sizes[activeId] || DEFAULT_SIZES[activeId]
    : null;

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={(e) => setActiveId(e.active.id)}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setActiveId(null)}
    >
      <SortableContext items={order}>
        <div className="flex flex-wrap gap-4 items-start">
          {order.map((id) => (
            <SortableTile
              key={id}
              id={id}
              size={sizes[id] || DEFAULT_SIZES[id]}
              onResize={onResize}
            >
              {({ dragHandleProps }) => renderTile(id, dragHandleProps)}
            </SortableTile>
          ))}
        </div>
      </SortableContext>
      <DragOverlay>
        {activeId && activeSize ? (
          <div
            style={{ width: activeSize.w, height: activeSize.h }}
            className="scale-[1.03] shadow-2xl rotate-[0.5deg]"
          >
            {renderTile(activeId, {})}
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}
