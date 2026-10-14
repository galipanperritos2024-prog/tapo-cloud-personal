"use client";

import { ArrowLeft, Camera as CameraIcon, Circle, RefreshCw, Save, Square, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import useSWR from "swr";
import LivePlayer from "@/components/LivePlayer";
import PtzPad from "@/components/PtzPad";
import RecordingForm, { type RecordingValues } from "@/components/RecordingForm";
import { Card, ErrorBox, Spinner, StatusBadge, Toggle } from "@/components/ui";
import { api, fetcher } from "@/lib/api";
import { duration, EVENT_LABELS, relative, time } from "@/lib/format";
import type { Camera, EventItem, SystemStatus } from "@/lib/types";

function pickRecording(c: Camera): RecordingValues {
  const { recording_mode, auto_record, segment_minutes, pre_event_seconds, post_event_seconds, motion_source, motion_sensitivity, record_audio, schedule } = c;
  return { recording_mode, auto_record, segment_minutes, pre_event_seconds, post_event_seconds, motion_source, motion_sensitivity, record_audio, schedule };
}

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 text-sm">
      <span className="text-muted">{label}</span>
      <span className="truncate text-right font-medium">{value ?? "—"}</span>
    </div>
  );
}

function CameraView() {
  const id = Number(useSearchParams().get("id"));
  const router = useRouter();
  const { data: cam, mutate, error: loadError } = useSWR<Camera>(id ? `/api/cameras/${id}` : null, fetcher, { refreshInterval: 5000 });
  const { data: sys } = useSWR<SystemStatus>("/api/system/status", fetcher, { refreshInterval: 3000 });
  const { data: events } = useSWR<{ items: EventItem[] }>(id ? `/api/events?camera_id=${id}&limit=6` : null, fetcher, { refreshInterval: 10000 });
  const [quality, setQuality] = useState<"sub" | "main">("sub");
  const [rec, setRec] = useState<RecordingValues | null>(null);
  const [edit, setEdit] = useState({ name: "", username: "", password: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (cam && !rec) {
      setRec(pickRecording(cam));
      setEdit((e) => ({ ...e, name: cam.name }));
    }
  }, [cam, rec]);

  if (loadError) return <ErrorBox error={loadError} />;
  if (!cam || !rec) {
    return (
      <div className="flex justify-center py-20 text-muted">
        <Spinner size={24} />
      </div>
    );
  }

  const worker = sys?.camera_workers.find((w) => w.camera_id === cam.id);
  const manual = worker?.manual_recording;
  const info = cam.stream_info ?? {};
  const online = cam.status === "online";

  const run = async (key: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (ok) setNotice(ok);
      await mutate();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  const snapshot = () =>
    run("snap", async () => {
      const blob = await api<Blob>(`/api/cameras/${cam.id}/snapshot`, { method: "POST" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${cam.slug}_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.jpg`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    }, "Captura guardada (también en la carpeta de grabaciones).");

  const toggleRecord = () => run("rec", () => api(`/api/cameras/${cam.id}/record`, { json: { action: manual ? "stop" : "start" } }), manual ? "Grabación manual guardada." : undefined);

  const patch = (body: object, ok?: string) => run("patch", () => api(`/api/cameras/${cam.id}`, { method: "PATCH", json: body }), ok);

  const remove = () => {
    if (!window.confirm(`¿Eliminar la cámara «${cam.name}»? Las grabaciones ya guardadas (local y Drive) se conservan.`)) return;
    run("del", async () => {
      await api(`/api/cameras/${cam.id}`, { method: "DELETE" });
      router.push("/cameras/");
    });
  };

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Link href="/cameras/" className="btn-ghost p-2" aria-label="Volver">
            <ArrowLeft size={18} />
          </Link>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{cam.name}</h1>
            <div className="mt-1 flex items-center gap-3 text-sm text-muted">
              <StatusBadge status={cam.status} />
              {cam.status_detail && <span className="text-xs">{cam.status_detail}</span>}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <div className="flex rounded-xl border border-line bg-surface-2 p-1">
            {(["sub", "main"] as const).map((q) => (
              <button key={q} onClick={() => setQuality(q)} className={`rounded-lg px-3 py-1 text-xs ${quality === q ? "bg-accent text-white" : "text-muted"}`}>
                {q === "sub" ? "Fluida" : "Alta calidad"}
              </button>
            ))}
          </div>
          <button className="btn-secondary" onClick={snapshot} disabled={!online || busy === "snap"}>
            {busy === "snap" ? <Spinner /> : <CameraIcon size={16} />} Captura
          </button>
          <button className={manual ? "btn-danger" : "btn-secondary"} onClick={toggleRecord} disabled={!online || busy === "rec"}>
            {busy === "rec" ? <Spinner /> : manual ? <Square size={14} fill="currentColor" /> : <Circle size={14} className="text-err" fill="currentColor" />}
            {manual ? `Detener (${duration(manual.elapsed_seconds)})` : "Grabar"}
          </button>
        </div>
      </div>

      {notice && <div className="rounded-xl border border-ok/30 bg-ok/10 px-4 py-3 text-sm text-ok">{notice}</div>}
      <ErrorBox error={error} />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {online ? (
            <LivePlayer cameraId={cam.id} quality={quality} />
          ) : (
            <div className="flex aspect-video flex-col items-center justify-center gap-2 rounded-2xl bg-black text-sm text-muted">
              <StatusBadge status={cam.status} />
              <span className="max-w-md px-6 text-center">{cam.status_detail ?? "Esperando a la cámara…"}</span>
            </div>
          )}

          <Card title="Grabación">
            <RecordingForm value={rec} onChange={setRec} onvifAvailable={cam.onvif_enabled} />
            <div className="mt-5 flex justify-end">
              <button className="btn-primary" disabled={busy === "patch"} onClick={() => patch(rec, "Configuración de grabación guardada.")}>
                <Save size={16} /> Guardar
              </button>
            </div>
          </Card>

          <Card title="Eventos de esta cámara" actions={<Link href={`/events/?camera=${cam.id}`} className="text-xs text-accent">Ver todos</Link>}>
            {events?.items.length === 0 && <p className="text-sm text-muted">Sin eventos.</p>}
            <ul className="divide-y divide-line">
              {events?.items.map((e) => (
                <li key={e.id} className="flex items-center justify-between py-2 text-sm">
                  <span>{EVENT_LABELS[e.type] ?? e.type}</span>
                  <span className="font-mono text-muted">{time(e.occurred_at)}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>

        <div className="space-y-6">
          {cam.ptz_enabled && (
            <Card title="Control PTZ">
              <PtzPad camera={cam} />
            </Card>
          )}

          <Card title="Información">
            <InfoRow label="IP local" value={<span className="font-mono">{cam.ip}</span>} />
            <InfoRow label="Resolución" value={info.width ? `${info.width}×${info.height}` : null} />
            <InfoRow label="FPS" value={info.fps} />
            <InfoRow label="Bitrate" value={info.bitrate_kbps ? `${(info.bitrate_kbps / 1000).toFixed(1)} Mbps` : null} />
            <InfoRow label="Códec" value={info.codec?.toUpperCase()} />
            <InfoRow label="Audio" value={info.audio_codec ?? "Sin audio"} />
            <InfoRow label="Modelo" value={[cam.manufacturer, cam.model].filter(Boolean).join(" ") || null} />
            <InfoRow label="Firmware" value={cam.firmware} />
            <InfoRow label="ONVIF" value={cam.onvif_enabled ? `Sí (puerto ${cam.onvif_port})` : "No"} />
            <InfoRow label="Última conexión" value={relative(cam.last_seen_at)} />
            <div className="mt-3 rounded-lg bg-bg p-2 font-mono text-[11px] break-all text-muted">{cam.rtsp_url_masked}</div>
          </Card>

          <Card title="Ajustes de la cámara">
            <div className="space-y-4">
              <Toggle label="Cámara activada" checked={cam.enabled} onChange={(v) => patch({ enabled: v })} />
              <Toggle label="Grabación automática" checked={cam.auto_record} onChange={(v) => patch({ auto_record: v })} disabled={cam.recording_mode === "manual"} />
              <label className="block">
                <span className="label">Nombre</span>
                <div className="flex gap-2">
                  <input className="input" maxLength={48} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
                  <button className="btn-secondary" disabled={!edit.name || edit.name === cam.name} onClick={() => patch({ name: edit.name }, "Nombre actualizado.")}>
                    <Save size={14} />
                  </button>
                </div>
              </label>
              <div>
                <span className="label">Credenciales (usuario actual: {cam.username_masked})</span>
                <div className="space-y-2">
                  <input className="input" placeholder="Nuevo usuario (opcional)" autoComplete="off" value={edit.username} onChange={(e) => setEdit({ ...edit, username: e.target.value })} />
                  <input className="input" type="password" placeholder="Nueva contraseña" autoComplete="new-password" value={edit.password} onChange={(e) => setEdit({ ...edit, password: e.target.value })} />
                  <button
                    className="btn-secondary w-full"
                    disabled={!edit.password}
                    onClick={() => {
                      patch({ ...(edit.username && { username: edit.username }), password: edit.password }, "Credenciales actualizadas.");
                      setEdit({ ...edit, username: "", password: "" });
                    }}
                  >
                    Actualizar credenciales
                  </button>
                </div>
              </div>
              <button className="btn-secondary w-full" disabled={busy === "onvif"} onClick={() => run("onvif", () => api(`/api/cameras/${cam.id}/onvif/refresh`, { method: "POST" }), "Información ONVIF actualizada.")}>
                <RefreshCw size={14} /> Volver a detectar ONVIF / PTZ
              </button>
              <button className="btn-danger w-full" onClick={remove}>
                <Trash2 size={14} /> Eliminar cámara
              </button>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

export default function CameraPage() {
  return (
    <Suspense>
      <CameraView />
    </Suspense>
  );
}
