"use client";

import { CameraOff, Circle } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { MODE_LABELS } from "@/lib/format";
import type { Camera } from "@/lib/types";
import { Spinner, StatusBadge } from "./ui";

/** Big camera tile with a periodically refreshed still (cheap: sub-stream JPEG). */
export default function CameraCard({ camera, refreshMs = 20000 }: { camera: Camera; refreshMs?: number }) {
  const [tick, setTick] = useState(0);
  const [imgOk, setImgOk] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const online = camera.status === "online";

  useEffect(() => {
    if (!online) return;
    setImgOk(true);
    const t = setInterval(() => setTick((x) => x + 1), refreshMs);
    return () => clearInterval(t);
  }, [online, refreshMs]);

  const info = camera.stream_info;
  return (
    <Link href={`/camera/?id=${camera.id}`} className="card group block overflow-hidden transition-colors hover:border-accent/50">
      <div className="relative aspect-video bg-black">
        {online && imgOk ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={`/api/cameras/${camera.id}/preview.jpg?t=${tick}`}
            alt={`Vista de ${camera.name}`}
            className={`h-full w-full object-cover transition duration-500 group-hover:scale-[1.02] ${loaded ? "opacity-100" : "opacity-0"}`}
            onLoad={() => setLoaded(true)}
            onError={() => setImgOk(false)}
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-muted">
            <CameraOff size={32} />
            <span className="px-4 text-center text-xs">{camera.status_detail ?? (online ? "Sin imagen" : "Sin conexión")}</span>
          </div>
        )}
        {online && imgOk && !loaded && (
          <div className="absolute inset-0 flex items-center justify-center text-muted">
            <Spinner size={20} />
          </div>
        )}
        {online && camera.auto_record && ["continuous", "schedule"].includes(camera.recording_mode) && (
          <span className="absolute top-3 right-3 flex items-center gap-1 rounded-lg bg-black/60 px-2 py-1 text-[11px] font-semibold text-err">
            <Circle size={8} fill="currentColor" /> REC
          </span>
        )}
      </div>
      <div className="flex items-start justify-between gap-3 p-4">
        <div className="min-w-0">
          <div className="truncate font-semibold">{camera.name}</div>
          <div className="mt-0.5 truncate text-xs text-muted">
            {MODE_LABELS[camera.recording_mode]}
            {info?.width ? ` · ${info.width}×${info.height}` : ""}
          </div>
        </div>
        <StatusBadge status={camera.status} />
      </div>
    </Link>
  );
}
