"use client";

import { Cpu, HardDrive, MemoryStick, Server } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Card, PageHeader, ProgressBar, ServiceDot, Stat, StatusBadge } from "@/components/ui";
import { fetcher } from "@/lib/api";
import { bytes, relative } from "@/lib/format";
import type { SystemStatus } from "@/lib/types";

const JOB_LABEL: Record<string, string> = { continuous: "Grabación continua", prebuffer: "Buffer pre-evento", motion: "Detección local", onvif_events: "Eventos ONVIF" };

function levelClass(line: string) {
  if (/\bERROR\b/.test(line)) return "text-err";
  if (/\bWARNING\b/.test(line)) return "text-warn";
  if (/\bDEBUG\b/.test(line)) return "text-muted/70";
  return "text-fg/85";
}

function uptime(s: number) {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}

export default function SystemPage() {
  const { data: s } = useSWR<SystemStatus>("/api/system/status", fetcher, { refreshInterval: 5000 });
  const [logFile, setLogFile] = useState<"backend" | "camera-service">("camera-service");
  const { data: logs } = useSWR<{ lines: string[] }>(`/api/system/logs?file=${logFile}&lines=300`, fetcher, { refreshInterval: 5000 });

  const diskPct = s ? (s.disk.used / s.disk.total) * 100 : 0;
  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <PageHeader title="Estado del sistema" subtitle={s ? `Versión ${s.version} · activo ${uptime(s.uptime_seconds)}` : undefined} />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="CPU" value={s ? `${s.cpu_percent.toFixed(0)} %` : "—"} hint={s ? `${s.cpu_count} núcleos` : undefined} icon={<Cpu size={20} />} tone={s && s.cpu_percent > 85 ? "warn" : "default"} />
        <Stat label="RAM" value={s ? `${s.ram.percent.toFixed(0)} %` : "—"} hint={s ? `${bytes(s.ram.used)} de ${bytes(s.ram.total)}` : undefined} icon={<MemoryStick size={20} />} />
        <Stat label="Disco" value={s ? `${diskPct.toFixed(0)} %` : "—"} hint={s ? `${bytes(s.disk.free)} libres` : undefined} icon={<HardDrive size={20} />} tone={diskPct > 90 ? "err" : diskPct > 75 ? "warn" : "default"} />
        <Stat label="Procesos FFmpeg" value={s?.ffmpeg_processes ?? "—"} hint={s ? `${s.streams_active} streams · ${s.recordings_active} grabando` : undefined} icon={<Server size={20} />} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Servicios">
          <ServiceDot ok={s?.services.backend} label="Backend / API" />
          <ServiceDot ok={s?.services.camera_service} label="Servicio de cámaras" />
          <ServiceDot ok={s?.services.mediamtx} label="MediaMTX (streaming)" />
          <ServiceDot ok={s?.services.google_drive} label="Google Drive" />
          <ServiceDot ok={s?.services.uploader_online} label="Conexión con Google" />
          <ServiceDot ok={!!s?.binaries.ffmpeg} label="FFmpeg" detail={s?.binaries.ffmpeg ?? "no encontrado"} />
        </Card>
        <Card title="Cola de Drive">
          {s &&
            Object.entries(s.upload_queue).map(([k, v]) => (
              <div key={k} className="flex justify-between py-1 text-sm">
                <span className="text-muted">{{ pending: "Esperando", uploading: "Subiendo", completed: "Completados", error: "Error", retry: "Reintento" }[k] ?? k}</span>
                <span className="font-medium">{v}</span>
              </div>
            ))}
          <div className="mt-2 border-t border-line pt-2 text-xs text-muted">Última subida: {relative(s?.last_upload_at)}</div>
        </Card>
        <Card title="Almacenamiento">
          <ProgressBar value={diskPct} tone={diskPct > 90 ? "err" : diskPct > 75 ? "warn" : "accent"} />
          <div className="mt-3 space-y-1 text-sm">
            <div className="flex justify-between"><span className="text-muted">Grabaciones locales</span><span>{bytes(s?.storage.local_recordings_bytes)}</span></div>
            <div className="flex justify-between"><span className="text-muted">Subido a Drive</span><span>{bytes(s?.storage.drive_bytes)}</span></div>
            <div className="truncate pt-1 font-mono text-[11px] text-muted" title={s?.disk.path}>{s?.disk.path}</div>
          </div>
        </Card>
      </div>

      <Card title="Procesos por cámara" className="overflow-x-auto">
        {!s?.camera_workers.length && <p className="text-sm text-muted">El servicio de cámaras no informa de ninguna cámara.</p>}
        <div className="grid gap-4 md:grid-cols-2">
          {s?.camera_workers.map((w) => (
            <div key={w.camera_id} className="rounded-xl border border-line bg-bg p-4">
              <div className="mb-3 flex items-center justify-between">
                <span className="font-medium">{w.name}</span>
                <StatusBadge status={w.status} />
              </div>
              <div className="space-y-1.5 text-sm">
                {Object.entries(w.jobs).map(([k, j]) =>
                  j ? (
                    <div key={k} className="flex items-center justify-between gap-3">
                      <span className="flex items-center gap-2">
                        <span className={`h-2 w-2 rounded-full ${j.running ? "bg-ok" : "bg-warn"}`} /> {JOB_LABEL[k] ?? k}
                      </span>
                      <span className="truncate text-xs text-muted" title={j.last_error ?? undefined}>
                        {j.pid ? `PID ${j.pid}` : ""} {j.restarts ? `· ${j.restarts} reinicios` : ""}
                      </span>
                    </div>
                  ) : null,
                )}
                {w.manual_recording && <div className="text-xs text-err">● Grabación manual en curso</div>}
                {!w.using_relay && <div className="text-xs text-warn">Sin relé MediaMTX: lectura directa de la cámara</div>}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card
        title="Registro"
        actions={
          <div className="flex rounded-xl border border-line bg-surface-2 p-1">
            {(["camera-service", "backend"] as const).map((f) => (
              <button key={f} onClick={() => setLogFile(f)} className={`rounded-lg px-3 py-1 text-xs ${logFile === f ? "bg-accent text-white" : "text-muted"}`}>
                {f === "backend" ? "Backend" : "Cámaras"}
              </button>
            ))}
          </div>
        }
      >
        <pre className="max-h-[480px] overflow-auto rounded-xl bg-bg p-4 font-mono text-[12px] leading-relaxed">
          {logs?.lines.length ? logs.lines.map((l, i) => <div key={i} className={levelClass(l)}>{l}</div>) : <span className="text-muted">Sin registros.</span>}
        </pre>
      </Card>
    </div>
  );
}
