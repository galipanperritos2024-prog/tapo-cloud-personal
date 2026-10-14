"use client";

import { Bell, Camera as CameraIcon, Cloud, CloudOff, Film, HardDrive, Plus } from "lucide-react";
import Link from "next/link";
import useSWR from "swr";
import CameraCard from "@/components/CameraCard";
import { Card, Empty, ProgressBar, ServiceDot, Stat } from "@/components/ui";
import { fetcher } from "@/lib/api";
import { bytes, dateTime, duration, EVENT_LABELS, KIND_LABELS, relative, time } from "@/lib/format";
import type { Camera, DriveStatus, EventItem, Recording, SystemStatus } from "@/lib/types";

export default function Dashboard() {
  const { data: cameras } = useSWR<Camera[]>("/api/cameras", fetcher, { refreshInterval: 10000 });
  const { data: sys } = useSWR<SystemStatus>("/api/system/status", fetcher, { refreshInterval: 10000 });
  const { data: events } = useSWR<{ items: EventItem[] }>("/api/events?limit=8&type=motion&type=person&type=vehicle&type=animal&type=camera_offline&type=camera_online&type=storage_error&type=upload_error", fetcher, { refreshInterval: 10000 });
  const { data: recs } = useSWR<{ items: Recording[] }>("/api/recordings?limit=6", fetcher, { refreshInterval: 15000 });
  const { data: drive } = useSWR<DriveStatus>("/api/google/status", fetcher, { refreshInterval: 30000 });

  const diskPct = sys ? (sys.disk.used / sys.disk.total) * 100 : 0;
  const pendingUploads = sys ? (sys.upload_queue.pending ?? 0) + (sys.upload_queue.retry ?? 0) + (sys.upload_queue.uploading ?? 0) : 0;

  return (
    <div className="mx-auto max-w-7xl space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Inicio</h1>
          <p className="mt-1 text-sm text-muted">Estado de tus cámaras, grabaciones y copias en Google Drive.</p>
        </div>
        <Link href="/cameras/?add=1" className="btn-primary">
          <Plus size={16} /> Añadir cámara
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Stat
          label="Cámaras online"
          value={sys ? `${sys.cameras.online}/${sys.cameras.total}` : "—"}
          hint={sys && sys.cameras.offline > 0 ? `${sys.cameras.offline} sin conexión` : "Todas conectadas"}
          tone={sys && sys.cameras.offline > 0 ? "warn" : "ok"}
          icon={<CameraIcon size={20} />}
        />
        <Stat label="Grabaciones locales" value={sys ? bytes(sys.storage.local_recordings_bytes) : "—"} hint={sys ? `${bytes(sys.disk.free)} libres en disco` : undefined} icon={<HardDrive size={20} />} />
        <Stat
          label="Google Drive"
          value={drive ? (drive.connected ? "Conectado" : "Sin conectar") : "—"}
          hint={drive?.connected ? `${bytes(drive.uploaded_bytes)} almacenados` : "Las grabaciones se guardan en local"}
          tone={drive?.connected ? "ok" : "warn"}
          icon={drive?.connected ? <Cloud size={20} /> : <CloudOff size={20} />}
        />
        <Stat label="Cola de subida" value={pendingUploads} hint={sys?.last_upload_at ? `Última subida ${relative(sys.last_upload_at)}` : "Sin subidas todavía"} icon={<Film size={20} />} />
      </div>

      <section>
        <h2 className="mb-4 text-sm font-semibold tracking-wide text-muted uppercase">Mis cámaras</h2>
        {cameras?.length === 0 ? (
          <Card>
            <Empty icon={<CameraIcon size={36} />} title="Aún no hay cámaras">
              Añade tu primera cámara Tapo: la buscamos en tu red y en dos minutos estará grabando.
              <div className="mt-4">
                <Link href="/cameras/?add=1" className="btn-primary">
                  <Plus size={16} /> Añadir cámara
                </Link>
              </div>
            </Empty>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {cameras?.map((c) => <CameraCard key={c.id} camera={c} />)}
          </div>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Eventos recientes" actions={<Link href="/events/" className="text-xs text-accent">Ver todos</Link>} className="lg:col-span-2">
          {events?.items.length === 0 && <Empty icon={<Bell size={28} />} title="Sin eventos" />}
          <ul className="divide-y divide-line">
            {events?.items.map((e) => (
              <li key={e.id} className="flex items-center gap-4 py-3">
                <div className="h-12 w-20 shrink-0 overflow-hidden rounded-lg bg-black">
                  {e.has_thumbnail && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={`/api/events/${e.id}/thumbnail`} alt="" className="h-full w-full object-cover" loading="lazy" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{EVENT_LABELS[e.type] ?? e.type}</div>
                  <div className="truncate text-xs text-muted">{e.camera_name ?? "Sistema"}</div>
                </div>
                <span className="font-mono text-sm text-muted">{time(e.occurred_at)}</span>
              </li>
            ))}
          </ul>
        </Card>

        <div className="space-y-6">
          <Card title="Servicios">
            <ServiceDot ok={sys?.services.camera_service} label="Servicio de grabación" detail={sys ? `${sys.recordings_active} grabando` : undefined} />
            <ServiceDot ok={sys?.services.mediamtx} label="Streaming (MediaMTX)" detail={sys ? `${sys.streams_active} streams` : undefined} />
            <ServiceDot ok={sys?.services.google_drive} label="Google Drive" detail={drive?.email ?? undefined} />
            <ServiceDot ok={sys?.services.uploader_online} label="Conexión a Internet" />
          </Card>
          <Card title="Almacenamiento local">
            <ProgressBar value={diskPct} tone={diskPct > 90 ? "err" : diskPct > 75 ? "warn" : "accent"} />
            <div className="mt-2 flex justify-between text-xs text-muted">
              <span>{sys ? bytes(sys.disk.used) : "—"} usados</span>
              <span>{sys ? bytes(sys.disk.total) : "—"}</span>
            </div>
          </Card>
        </div>
      </div>

      <Card title="Últimas grabaciones" actions={<Link href="/recordings/" className="text-xs text-accent">Ver todas</Link>}>
        {recs?.items.length === 0 && <Empty icon={<Film size={28} />} title="Sin grabaciones todavía" />}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {recs?.items.map((r) => (
            <Link key={r.id} href={`/recordings/?play=${r.id}`} className="flex gap-3 rounded-xl p-2 hover:bg-surface-2">
              <div className="h-14 w-24 shrink-0 overflow-hidden rounded-lg bg-black">
                {r.has_thumbnail && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={`/api/recordings/${r.id}/thumbnail`} alt="" className="h-full w-full object-cover" loading="lazy" />
                )}
              </div>
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{r.camera_name} · {KIND_LABELS[r.kind]}</div>
                <div className="text-xs text-muted">{dateTime(r.started_at)} · {duration(r.duration_seconds)}</div>
                <div className="text-xs text-muted">{r.uploaded_at ? "En Drive" : r.upload_status === "pending" ? "Pendiente de subir" : "Local"}</div>
              </div>
            </Link>
          ))}
        </div>
      </Card>
    </div>
  );
}
