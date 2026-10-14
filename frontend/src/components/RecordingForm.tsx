"use client";

/** Recording configuration (mode, segments, pre/post buffer, detection, schedule). */

import { Plus, Trash2 } from "lucide-react";
import { DAY_LABELS, MODE_LABELS } from "@/lib/format";
import type { MotionSource, RecordingMode, ScheduleWindow } from "@/lib/types";
import { Toggle } from "./ui";

export interface RecordingValues {
  recording_mode: RecordingMode;
  auto_record: boolean;
  segment_minutes: number;
  pre_event_seconds: number;
  post_event_seconds: number;
  motion_source: MotionSource;
  motion_sensitivity: number;
  record_audio: boolean;
  schedule: ScheduleWindow[];
}

export const DEFAULT_RECORDING: RecordingValues = {
  recording_mode: "motion",
  auto_record: true,
  segment_minutes: 5,
  pre_event_seconds: 10,
  post_event_seconds: 30,
  motion_source: "both",
  motion_sensitivity: 50,
  record_audio: true,
  schedule: [
    { days: [0, 1, 2, 3, 4], start: "22:00", end: "08:00" },
    { days: [5, 6], start: "00:00", end: "24:00" },
  ],
};

const SEGMENTS = [1, 5, 10, 15, 30, 60];
const MODE_HELP: Record<RecordingMode, string> = {
  continuous: "Graba sin parar en archivos de la duración elegida.",
  motion: "Guarda un clip con los segundos previos y posteriores a cada movimiento.",
  event: "Como el anterior, pero solo con detecciones inteligentes de la cámara (persona, vehículo, animal) vía ONVIF.",
  manual: "Solo graba cuando pulses el botón de grabar.",
  schedule: "Graba de forma continua dentro de las franjas horarias definidas.",
};

function ScheduleEditor({ value, onChange }: { value: ScheduleWindow[]; onChange: (v: ScheduleWindow[]) => void }) {
  const update = (i: number, patch: Partial<ScheduleWindow>) => onChange(value.map((w, j) => (j === i ? { ...w, ...patch } : w)));
  return (
    <div className="space-y-3">
      {value.map((w, i) => (
        <div key={i} className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-bg p-3">
          <div className="flex gap-1">
            {DAY_LABELS.map((d, day) => {
              const on = w.days.includes(day);
              return (
                <button
                  type="button"
                  key={day}
                  onClick={() => update(i, { days: on ? w.days.filter((x) => x !== day) : [...w.days, day].sort() })}
                  className={`h-8 w-8 rounded-lg text-xs font-semibold ${on ? "bg-accent text-white" : "bg-surface-2 text-muted"}`}
                  aria-pressed={on}
                >
                  {d}
                </button>
              );
            })}
          </div>
          <div className="flex items-center gap-2 text-sm">
            <input type="time" className="input w-28" value={w.start} onChange={(e) => update(i, { start: e.target.value })} />
            <span className="text-muted">a</span>
            <input
              type="time"
              className="input w-28"
              value={w.end === "24:00" ? "23:59" : w.end}
              onChange={(e) => update(i, { end: e.target.value === "23:59" ? "24:00" : e.target.value })}
            />
          </div>
          <button type="button" className="btn-ghost ml-auto p-2" onClick={() => onChange(value.filter((_, j) => j !== i))} aria-label="Eliminar franja">
            <Trash2 size={16} />
          </button>
        </div>
      ))}
      <button type="button" className="btn-secondary" onClick={() => onChange([...value, { days: [0, 1, 2, 3, 4], start: "22:00", end: "08:00" }])}>
        <Plus size={16} /> Añadir franja
      </button>
      <p className="text-xs text-muted">Si la hora final es anterior a la inicial, la franja cruza la medianoche (p. ej. 22:00 a 08:00).</p>
    </div>
  );
}

export default function RecordingForm({ value, onChange, onvifAvailable = true }: { value: RecordingValues; onChange: (v: RecordingValues) => void; onvifAvailable?: boolean }) {
  const set = <K extends keyof RecordingValues>(k: K, v: RecordingValues[K]) => onChange({ ...value, [k]: v });
  const buffered = value.recording_mode === "motion" || value.recording_mode === "event";
  const detection = value.recording_mode !== "manual";

  return (
    <div className="space-y-5">
      <div>
        <span className="label">Modo de grabación</span>
        <div className="grid gap-2 sm:grid-cols-2">
          {(Object.keys(MODE_LABELS) as RecordingMode[]).map((m) => (
            <button
              type="button"
              key={m}
              onClick={() => set("recording_mode", m)}
              disabled={m === "event" && !onvifAvailable}
              className={`rounded-xl border p-3 text-left text-sm transition-colors disabled:opacity-40 ${
                value.recording_mode === m ? "border-accent bg-accent/10" : "border-line bg-bg hover:border-muted/50"
              }`}
            >
              <div className="font-medium">{MODE_LABELS[m]}</div>
              <div className="mt-0.5 text-xs text-muted">{MODE_HELP[m]}</div>
            </button>
          ))}
        </div>
      </div>

      {value.recording_mode !== "manual" && <Toggle label="Grabación automática activada" checked={value.auto_record} onChange={(v) => set("auto_record", v)} />}

      {(value.recording_mode === "continuous" || value.recording_mode === "schedule") && (
        <div>
          <span className="label">Dividir archivos cada</span>
          <div className="flex flex-wrap gap-2">
            {SEGMENTS.map((m) => (
              <button
                type="button"
                key={m}
                onClick={() => set("segment_minutes", m)}
                className={`rounded-lg px-3 py-1.5 text-sm ${value.segment_minutes === m ? "bg-accent text-white" : "bg-surface-2 text-muted hover:text-fg"}`}
              >
                {m} min
              </button>
            ))}
          </div>
        </div>
      )}

      {value.recording_mode === "schedule" && (
        <div>
          <span className="label">Horario</span>
          <ScheduleEditor value={value.schedule} onChange={(v) => set("schedule", v)} />
        </div>
      )}

      {buffered && (
        <div className="grid gap-4 sm:grid-cols-2">
          <label>
            <span className="label">Antes del evento: {value.pre_event_seconds} s</span>
            <input type="range" min={0} max={30} value={value.pre_event_seconds} onChange={(e) => set("pre_event_seconds", +e.target.value)} className="w-full accent-[var(--color-accent)]" />
          </label>
          <label>
            <span className="label">Después del evento: {value.post_event_seconds} s</span>
            <input type="range" min={10} max={120} step={5} value={value.post_event_seconds} onChange={(e) => set("post_event_seconds", +e.target.value)} className="w-full accent-[var(--color-accent)]" />
          </label>
        </div>
      )}

      {detection && (
        <div className="grid gap-4 sm:grid-cols-2">
          <label>
            <span className="label">Detección</span>
            <select className="input" value={value.motion_source} onChange={(e) => set("motion_source", e.target.value as MotionSource)}>
              <option value="both" disabled={!onvifAvailable}>Cámara (ONVIF) + análisis propio</option>
              <option value="onvif" disabled={!onvifAvailable}>Solo la cámara (ONVIF)</option>
              <option value="local">Solo análisis propio</option>
              <option value="off">Desactivada</option>
            </select>
          </label>
          {(value.motion_source === "local" || value.motion_source === "both") && (
            <label>
              <span className="label">Sensibilidad: {value.motion_sensitivity}</span>
              <input type="range" min={1} max={100} value={value.motion_sensitivity} onChange={(e) => set("motion_sensitivity", +e.target.value)} className="w-full accent-[var(--color-accent)]" />
            </label>
          )}
        </div>
      )}

      <Toggle label="Grabar audio" checked={value.record_audio} onChange={(v) => set("record_audio", v)} />
    </div>
  );
}
