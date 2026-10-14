"use client";

/**
 * PTZ controls with press-and-hold: while a direction is held we re-send a
 * short ContinuousMove every 400 ms; releasing sends Stop. If the browser
 * tab dies mid-move the camera still stops on its own (ONVIF timeout).
 */

import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Save, ZoomIn, ZoomOut } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";
import type { Camera } from "@/lib/types";
import { ErrorBox } from "./ui";

interface Preset {
  token: string;
  name: string;
}

export default function PtzPad({ camera }: { camera: Camera }) {
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { data: presets, mutate } = useSWR<Preset[]>(camera.ptz_caps.presets ? `/api/cameras/${camera.id}/ptz/presets` : null, fetcher);

  const send = useCallback(
    (body: Record<string, unknown>) =>
      api(`/api/cameras/${camera.id}/ptz`, { json: body })
        .then(() => setError(null))
        .catch((e) => setError(e.message)),
    [camera.id],
  );

  const stop = useCallback(() => {
    if (timer.current) {
      clearInterval(timer.current);
      timer.current = null;
      send({ action: "stop" });
    }
  }, [send]);

  const start = (pan: number, tilt: number, zoom = 0) => {
    stop();
    const move = () => send({ action: "move", pan, tilt, zoom, duration: 0.6 });
    move();
    timer.current = setInterval(move, 400);
  };

  useEffect(() => stop, [stop]);

  const btn = (label: string, icon: React.ReactNode, pan: number, tilt: number, zoom = 0) => (
    <button
      className="btn-secondary h-12 w-12 p-0 select-none active:bg-accent/30"
      aria-label={label}
      title={label}
      onPointerDown={(e) => {
        (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
        start(pan, tilt, zoom);
      }}
      onPointerUp={stop}
      onPointerCancel={stop}
      onContextMenu={(e) => e.preventDefault()}
    >
      {icon}
    </button>
  );

  const savePreset = async () => {
    const name = window.prompt("Nombre del preset", `Preset ${(presets?.length ?? 0) + 1}`);
    if (!name) return;
    await send({ action: "set_preset", preset_name: name.slice(0, 32) });
    mutate();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-center gap-6">
        <div className="grid grid-cols-3 gap-1.5">
          <span />
          {btn("Arriba", <ChevronUp size={20} />, 0, 0.5)}
          <span />
          {btn("Izquierda", <ChevronLeft size={20} />, -0.5, 0)}
          <span className="h-12 w-12 rounded-xl border border-dashed border-line" />
          {btn("Derecha", <ChevronRight size={20} />, 0.5, 0)}
          <span />
          {btn("Abajo", <ChevronDown size={20} />, 0, -0.5)}
          <span />
        </div>
        {camera.ptz_caps.zoom && (
          <div className="flex flex-col gap-1.5">
            {btn("Zoom +", <ZoomIn size={18} />, 0, 0, 0.5)}
            {btn("Zoom -", <ZoomOut size={18} />, 0, 0, -0.5)}
          </div>
        )}
      </div>
      {camera.ptz_caps.presets && (
        <div className="flex flex-wrap items-center gap-2">
          {(presets ?? []).slice(0, 8).map((p, i) => (
            <button key={p.token} className="btn-secondary px-3 py-1.5 text-xs" onClick={() => send({ action: "goto_preset", preset: p.token })}>
              {p.name || `Preset ${i + 1}`}
            </button>
          ))}
          <button className="btn-ghost px-3 py-1.5 text-xs" onClick={savePreset}>
            <Save size={14} /> Guardar posición
          </button>
        </div>
      )}
      {error && <ErrorBox error={error} />}
    </div>
  );
}
