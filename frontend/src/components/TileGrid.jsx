import React, { useState } from "react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
} from "@dnd-kit/sortable";
import SortableTile from "./SortableTile.jsx";

export default function TileGrid({ order, onReorder, getColSpan, renderTile }) {
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

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={(e) => setActiveId(e.active.id)}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setActiveId(null)}
    >
      <SortableContext items={order} strategy={rectSortingStrategy}>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 items-start">
          {order.map((id) => (
            <SortableTile key={id} id={id} colSpan={getColSpan(id)}>
              {({ dragHandleProps }) => renderTile(id, dragHandleProps)}
            </SortableTile>
          ))}
        </div>
      </SortableContext>
      <DragOverlay>
        {activeId ? (
          <div className="scale-[1.03] shadow-2xl rotate-[0.5deg]">
            {renderTile(activeId, {})}
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}
