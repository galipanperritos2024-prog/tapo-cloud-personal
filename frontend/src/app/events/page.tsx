"use client";

import { Bell, CheckCheck, Play } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import useSWR from "swr";
import VideoModal from "@/components/VideoModal";
import { Card, Empty, PageHeader } from "@/components/ui";
import { api, fetcher } from "@/lib/api";
import { dateTime, EVENT_LABELS } from "@/lib/format";
import type { Camera, EventItem } from "@/lib/types";

const FILTERS: { key: string; label: string; types: string[] }[] = [
  { key: "all", label: "Todos", types: [] },
  { key: "detections", label: "Detecciones", types: ["motion", "person", "vehicle", "animal"] },
  { key: "smart", label: "Persona / vehículo / animal", types: ["person", "vehicle", "animal"] },
  { key: "system", label: "Sistema", types: ["camera_offline", "camera_online", "recording_started", "recording_finished", "storage_error", "upload_error"] },
];

const TONE: Record<string, string> = {
  person: "text-err",
  vehicle: "text-err",
  animal: "text-err",
  motion: "text-warn",
  camera_offline: "text-err",
  storage_error: "text-err",
  upload_error: "text-err",
  camera_online: "text-ok",
};

function EventsInner() {
  const params = useSearchParams();
  const [camera, setCamera] = useState(params.get("camera") ?? "");
  const [filter, setFilter] = useState("detections");
  const [play, setPlay] = useState<number | null>(null);
  const { data: cameras } = useSWR<Camera[]>("/api/cameras", fetcher);
  const types = FILTERS.find((f) => f.key === filter)!.types;
  const qs = new URLSearchParams({ limit: "120" });
  if (camera) qs.set("camera_id", camera);
  types.forEach((t) => qs.append("type", t));
  const { data, mutate } = useSWR<{ items: EventItem[]; unacknowledged: number }>(`/api/events?${qs}`, fetcher, { refreshInterval: 10000 });

  const ackAll = async () => {
    await api("/api/events/ack", { json: { ids: null } });
    mutate();
  };

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Eventos"
        subtitle={data ? `${data.unacknowledged} sin revisar` : undefined}
        actions={
          <button className="btn-secondary" onClick={ackAll}>
            <CheckCheck size={16} /> Marcar todo como visto
          </button>
        }
      />
      <div className="mb-6 flex flex-wrap gap-2">
        <select className="input w-auto" value={camera} onChange={(e) => setCamera(e.target.value)}>
          <option value="">Todas las cámaras</option>
          {cameras?.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        {FILTERS.map((f) => (
          <button key={f.key} onClick={() => setFilter(f.key)} className={`rounded-xl px-3 py-2 text-sm ${filter === f.key ? "bg-accent text-white" : "bg-surface-2 text-muted hover:text-fg"}`}>
            {f.label}
          </button>
        ))}
      </div>
      {data?.items.length === 0 ? (
        <Card>
          <Empty icon={<Bell size={32} />} title="Sin eventos con estos filtros" />
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {data?.items.map((e) => (
            <button
              key={e.id}
              onClick={() => e.recording_id && setPlay(e.recording_id)}
              className={`card overflow-hidden text-left transition-colors ${e.recording_id ? "hover:border-accent/50" : "cursor-default"} ${e.acknowledged ? "" : "ring-1 ring-accent/30"}`}
            >
              <div className="relative aspect-video bg-black">
                {e.has_thumbnail ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={`/api/events/${e.id}/thumbnail`} alt="" loading="lazy" className="h-full w-full object-cover" />
                ) : (
                  <div className="flex h-full items-center justify-center text-muted">
                    <Bell size={24} />
                  </div>
                )}
                {e.recording_id && (
                  <span className="absolute right-2 bottom-2 rounded-lg bg-black/60 p-1.5">
                    <Play size={14} fill="currentColor" />
                  </span>
                )}
              </div>
              <div className="p-3">
                <div className={`text-sm font-medium ${TONE[e.type] ?? ""}`}>{EVENT_LABELS[e.type] ?? e.type}</div>
                <div className="mt-0.5 flex justify-between text-xs text-muted">
                  <span>{e.camera_name ?? "Sistema"}</span>
                  <span>{dateTime(e.occurred_at)}</span>
                </div>
                {e.message && !EVENT_LABELS[e.type]?.startsWith(e.message) && <div className="mt-1 line-clamp-2 text-xs text-muted">{e.message}</div>}
              </div>
            </button>
          ))}
        </div>
      )}
      <VideoModal recordingId={play} onClose={() => setPlay(null)} />
    </div>
  );
}

export default function EventsPage() {
  return (
    <Suspense>
      <EventsInner />
    </Suspense>
  );
}
