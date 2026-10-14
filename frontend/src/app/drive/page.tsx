"use client";

import { CheckCircle2, Cloud, CloudOff, ExternalLink, LogOut, RefreshCw, Upload } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import useSWR from "swr";
import { Card, ErrorBox, PageHeader, ProgressBar, Spinner, Stat, Toggle } from "@/components/ui";
import { api, fetcher } from "@/lib/api";
import { bytes, dateTime, relative } from "@/lib/format";
import type { DriveStatus, QueueItem, Settings } from "@/lib/types";

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-surface-3 text-muted",
  uploading: "bg-accent/20 text-accent",
  completed: "bg-ok/15 text-ok",
  retry: "bg-warn/15 text-warn",
  error: "bg-err/15 text-err",
};
const STATUS_LABEL: Record<string, string> = { pending: "Esperando", uploading: "Subiendo", completed: "Completado", retry: "Reintento", error: "Error" };

function DriveInner() {
  const params = useSearchParams();
  const { data: st, mutate } = useSWR<DriveStatus>("/api/google/status", fetcher, { refreshInterval: 5000 });
  const { data: settings, mutate: mutateSettings } = useSWR<Settings>("/api/settings", fetcher);
  const [filter, setFilter] = useState("");
  const { data: queue, mutate: mutateQueue } = useSWR<QueueItem[]>(`/api/google/queue${filter ? `?status=${filter}` : ""}`, fetcher, { refreshInterval: 5000 });
  const [cfg, setCfg] = useState({ client_id: "", client_secret: "" });
  const [error, setError] = useState<unknown>(params.get("error"));
  const [busy, setBusy] = useState(false);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await Promise.all([mutate(), mutateQueue(), mutateSettings()]);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const connect = () =>
    act(async () => {
      const { auth_url } = await api<{ auth_url: string }>("/api/google/connect", { method: "POST" });
      window.location.href = auth_url;
    });

  const setSetting = (key: string, value: unknown) => act(() => api("/api/settings", { method: "PATCH", json: { [key]: value } }));
  const kinds = (settings?.["drive.upload_kinds"] as string[]) ?? [];
  const quotaPct = st?.quota?.limit ? ((st.quota.usage ?? 0) / st.quota.limit) * 100 : 0;
  const cur = st?.current_upload;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader title="Google Drive" subtitle="Copia automática de tus grabaciones en tu propio Google Drive." />
      {params.get("connected") && st?.connected && (
        <div className="flex items-center gap-2 rounded-xl border border-ok/30 bg-ok/10 px-4 py-3 text-sm text-ok">
          <CheckCircle2 size={16} /> Google Drive conectado correctamente.
        </div>
      )}
      <ErrorBox error={error} />

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Estado" value={st ? (st.connected ? "Conectado" : "Sin conectar") : "—"} tone={st?.connected ? "ok" : "warn"} hint={st?.email ?? undefined} icon={st?.connected ? <Cloud size={20} /> : <CloudOff size={20} />} />
        <Stat label="Subido por la app" value={st ? bytes(st.uploaded_bytes) : "—"} hint={st?.last_success_at ? `Última subida ${relative(st.last_success_at)}` : undefined} icon={<Upload size={20} />} />
        <Stat
          label="En cola"
          value={st ? (st.queue.pending ?? 0) + (st.queue.retry ?? 0) + (st.queue.uploading ?? 0) : "—"}
          hint={st && st.queue.error ? `${st.queue.error} con error` : st?.online === false ? "Sin Internet: se subirán al volver" : undefined}
          tone={st?.queue.error ? "err" : "default"}
        />
      </div>

      {!st?.connected && (
        <Card title="Conectar tu cuenta">
          {!st?.configured ? (
            <div className="space-y-4">
              <ol className="list-decimal space-y-1 pl-5 text-sm text-muted">
                <li>Abre <a className="text-accent" href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">Google Cloud Console <ExternalLink size={12} className="inline" /></a>, crea un proyecto y habilita «Google Drive API».</li>
                <li>Configura la pantalla de consentimiento (tipo «Externo», modo prueba, añade tu email como usuario de prueba).</li>
                <li>Crea credenciales «ID de cliente OAuth» de tipo <b>Aplicación web</b> con este URI de redirección:</li>
              </ol>
              <div className="rounded-lg bg-bg p-2 font-mono text-xs break-all">{st?.redirect_uri}</div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label><span className="label">Client ID</span><input className="input" value={cfg.client_id} onChange={(e) => setCfg({ ...cfg, client_id: e.target.value.trim() })} /></label>
                <label><span className="label">Client secret</span><input className="input" type="password" autoComplete="off" value={cfg.client_secret} onChange={(e) => setCfg({ ...cfg, client_secret: e.target.value.trim() })} /></label>
              </div>
              <button className="btn-primary" disabled={!cfg.client_id || busy} onClick={() => act(() => api("/api/google/config", { json: { client_id: cfg.client_id, client_secret: cfg.client_secret || null } }))}>
                Guardar credenciales OAuth
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-muted">
                Se abrirá Google para que autorices el acceso. La app solo pide permiso para <b>los archivos que ella misma crea</b> (ámbito <code>drive.file</code>): no puede ver el resto de tu Drive. Nunca introduces tu contraseña de Google aquí.
              </p>
              <button className="btn-primary" disabled={busy} onClick={connect}>
                {busy ? <Spinner /> : <Cloud size={16} />} Conectar Google Drive
              </button>
              <p className="text-xs text-muted">Abre esta página desde <span className="font-mono">{st?.redirect_uri?.split("/api")[0]}</span> para que Google pueda volver a ella.</p>
            </div>
          )}
        </Card>
      )}

      {st?.connected && (
        <Card
          title="Cuenta"
          actions={
            <button className="btn-ghost text-xs" onClick={() => window.confirm("¿Desconectar Google Drive? Las grabaciones quedarán solo en local.") && act(() => api("/api/google/disconnect", { method: "POST" }))}>
              <LogOut size={14} /> Desconectar
            </button>
          }
        >
          <div className="text-sm">{st.email}</div>
          {st.error && (
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-warn/30 bg-warn/10 px-4 py-3 text-sm text-warn">
              <span>{st.error}</span>
              <button className="btn-secondary py-1 text-xs" onClick={connect}>Reconectar</button>
            </div>
          )}
          {st.quota?.limit ? (
            <div className="mt-3">
              <ProgressBar value={quotaPct} tone={quotaPct > 90 ? "err" : quotaPct > 75 ? "warn" : "accent"} />
              <div className="mt-1 text-xs text-muted">{bytes(st.quota.usage)} de {bytes(st.quota.limit)} usados en tu cuenta de Google</div>
            </div>
          ) : null}
          {cur && (
            <div className="mt-4 rounded-xl bg-bg p-3">
              <div className="mb-2 flex justify-between text-xs">
                <span className="truncate font-mono">{cur.file}</span>
                <span className="text-muted">{bytes(cur.bytes)} / {bytes(cur.total)}</span>
              </div>
              <ProgressBar value={(cur.bytes / Math.max(1, cur.total)) * 100} />
            </div>
          )}
        </Card>
      )}

      <Card title="Opciones de subida">
        <div className="grid gap-5 md:grid-cols-2">
          <Toggle label="Subir automáticamente a Google Drive" checked={!!settings?.["drive.enabled"]} onChange={(v) => setSetting("drive.enabled", v)} />
          <label>
            <span className="label">Carpeta raíz en Drive</span>
            <input className="input" defaultValue={String(settings?.["drive.root_folder"] ?? "TapoCloud")} onBlur={(e) => e.target.value !== settings?.["drive.root_folder"] && setSetting("drive.root_folder", e.target.value)} />
          </label>
          <div>
            <span className="label">Qué subir</span>
            <div className="flex flex-wrap gap-2">
              {[["continuous", "Continuas"], ["event", "Eventos"], ["manual", "Manuales"]].map(([k, label]) => {
                const on = kinds.includes(k);
                return (
                  <button key={k} onClick={() => setSetting("drive.upload_kinds", on ? kinds.filter((x) => x !== k) : [...kinds, k])} className={`rounded-lg px-3 py-1.5 text-sm ${on ? "bg-accent text-white" : "bg-surface-2 text-muted"}`}>
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
          <label>
            <span className="label">Límite de ancho de banda (kbps, 0 = sin límite)</span>
            <input className="input" type="number" min={0} defaultValue={Number(settings?.["drive.bandwidth_limit_kbps"] ?? 0)} onBlur={(e) => setSetting("drive.bandwidth_limit_kbps", Math.max(0, +e.target.value))} />
          </label>
        </div>
        <p className="mt-4 text-xs text-muted">
          Estructura en Drive: <span className="font-mono">{String(settings?.["drive.root_folder"] ?? "TapoCloud")}/Cámara/AAAA/MM/DD/HH-MM-SS.mp4</span>. Cada archivo se verifica con MD5 antes de considerarse subido; el archivo local nunca se borra antes.
        </p>
      </Card>

      <Card
        title="Cola de subida"
        actions={
          <div className="flex flex-wrap gap-2">
            <select className="input w-auto py-1 text-xs" value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">Todos</option>
              {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <button className="btn-secondary py-1 text-xs" onClick={() => act(() => api("/api/google/queue/retry-all", { method: "POST" }))}><RefreshCw size={12} /> Reintentar errores</button>
            <button className="btn-secondary py-1 text-xs" onClick={() => act(() => api("/api/google/queue/enqueue-missing", { method: "POST" }))}><Upload size={12} /> Encolar pendientes</button>
          </div>
        }
        className="overflow-x-auto"
      >
        {queue?.length === 0 && <p className="text-sm text-muted">La cola está vacía.</p>}
        {!!queue?.length && (
          <table className="w-full min-w-[720px] text-sm">
            <thead className="text-left text-xs text-muted uppercase">
              <tr>
                <th className="pb-3 font-medium">Archivo</th>
                <th className="pb-3 font-medium">Tamaño</th>
                <th className="pb-3 font-medium">Estado</th>
                <th className="pb-3 font-medium">Detalle</th>
                <th className="pb-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {queue.map((q) => (
                <tr key={q.id}>
                  <td className="py-2.5 font-mono text-xs">{q.file}</td>
                  <td className="py-2.5 text-xs text-muted">{bytes(q.size_bytes)}</td>
                  <td className="py-2.5"><span className={`rounded-md px-2 py-0.5 text-xs ${STATUS_STYLE[q.status]}`}>{STATUS_LABEL[q.status] ?? q.status}</span></td>
                  <td className="max-w-xs truncate py-2.5 text-xs text-muted" title={q.last_error ?? undefined}>
                    {q.status === "completed" ? dateTime(q.completed_at) : q.status === "retry" ? `Intento ${q.attempts} · próximo ${dateTime(q.next_attempt_at)}` : q.last_error ?? ""}
                  </td>
                  <td className="py-2.5 text-right">
                    {(q.status === "error" || q.status === "retry") && (
                      <button className="btn-ghost px-2 py-1 text-xs" onClick={() => act(() => api(`/api/google/queue/${q.id}/retry`, { method: "POST" }))}>Reintentar</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

export default function DrivePage() {
  return (
    <Suspense>
      <DriveInner />
    </Suspense>
  );
}
