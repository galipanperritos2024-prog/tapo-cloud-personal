"use client";

/** 24 h timeline of one camera-day: recording blocks + event markers. */

import { EVENT_LABELS, time } from "@/lib/format";
import type { Timeline as TimelineData } from "@/lib/types";

const KIND_COLOR: Record<string, string> = {
  continuous: "bg-accent/60",
  event: "bg-warn/80",
  manual: "bg-ok/70",
};

export default function Timeline({ data, onSelect }: { data: TimelineData; onSelect: (recordingId: number) => void }) {
  const start = new Date(data.start).getTime();
  const span = new Date(data.end).getTime() - start;
  const pct = (iso: string) => ((new Date(iso).getTime() - start) / span) * 100;

  return (
    <div>
      <div className="relative h-14 overflow-hidden rounded-xl border border-line bg-bg">
        {/* hour grid */}
        {Array.from({ length: 24 }, (_, h) => (
          <div key={h} className="absolute top-0 bottom-0 border-l border-line/50" style={{ left: `${(h / 24) * 100}%` }} />
        ))}
        {data.recordings.map((r) => {
          const left = Math.max(0, pct(r.start));
          const width = Math.max(0.15, Math.min(100, pct(r.end)) - left);
          return (
            <button
              key={r.id}
              title={`${time(r.start)}–${time(r.end)}${r.local ? "" : " (solo en Drive)"}`}
              onClick={() => onSelect(r.id)}
              className={`absolute top-2 bottom-5 rounded-sm hover:brightness-125 ${KIND_COLOR[r.kind] ?? "bg-accent/60"} ${r.local ? "" : "opacity-40"}`}
              style={{ left: `${left}%`, width: `${width}%` }}
            />
          );
        })}
        {data.events
          .filter((e) => ["motion", "person", "vehicle", "animal"].includes(e.type))
          .map((e) => (
            <button
              key={e.id}
              title={`${time(e.at)} ${EVENT_LABELS[e.type] ?? e.type}`}
              onClick={() => e.recording_id && onSelect(e.recording_id)}
              className={`absolute bottom-1 h-3 w-1 rounded-full ${e.type === "motion" ? "bg-warn" : "bg-err"}`}
              style={{ left: `${pct(e.at)}%` }}
            />
          ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted">
        {[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => (
          <span key={h}>{String(h).padStart(2, "0")}:00</span>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-4 text-xs text-muted">
        <span className="flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-accent/60" /> Continua</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-warn/80" /> Evento</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-ok/70" /> Manual</span>
        <span className="flex items-center gap-1.5"><span className="h-3 w-1 rounded-full bg-warn" /> Movimiento</span>
        <span className="flex items-center gap-1.5"><span className="h-3 w-1 rounded-full bg-err" /> Persona/vehículo/animal</span>
      </div>
    </div>
  );
}
