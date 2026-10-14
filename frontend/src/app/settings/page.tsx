"use client";

import { KeyRound, Shield } from "lucide-react";
import { type FormEvent, useState } from "react";
import useSWR from "swr";
import { Card, ErrorBox, PageHeader, Toggle } from "@/components/ui";
import { api, fetcher } from "@/lib/api";
import type { Settings } from "@/lib/types";

const RETENTION_LABELS: Record<string, string> = {
  until_uploaded: "Hasta que se suban a Drive",
  "24h": "24 horas",
  "3d": "3 días",
  "7d": "7 días",
  "30d": "30 días",
};

export default function SettingsPage() {
  const { data: s, mutate } = useSWR<Settings>("/api/settings", fetcher);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [pw, setPw] = useState({ current_password: "", new_password: "" });

  const set = async (key: string, value: unknown) => {
    setError(null);
    try {
      await mutate(await api<Settings>("/api/settings", { method: "PATCH", json: { [key]: value } }), false);
      setSaved("Guardado");
      setTimeout(() => setSaved(null), 1500);
    } catch (e) {
      setError(e);
    }
  };

  const changePassword = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api("/api/auth/password", { json: pw });
      setPw({ current_password: "", new_password: "" });
      setSaved("Contraseña cambiada. Se cerraron las demás sesiones.");
    } catch (err) {
      setError(err);
    }
  };

  if (!s) return null;
  const num = (key: string) => Number(s[key] ?? 0);

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader title="Configuración" subtitle={saved ?? undefined} />
      <ErrorBox error={error} />

      <Card title="Almacenamiento local">
        <div className="space-y-5">
          <div>
            <span className="label">Mantener archivos locales</span>
            <div className="flex flex-wrap gap-2">
              {s.retention_choices.map((r) => (
                <button key={r} onClick={() => set("storage.local_retention", r)} className={`rounded-lg px-3 py-1.5 text-sm ${s["storage.local_retention"] === r ? "bg-accent text-white" : "bg-surface-2 text-muted hover:text-fg"}`}>
                  {RETENTION_LABELS[r] ?? r}
                </button>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted">
              Con Google Drive activo, un archivo <b>nunca</b> se borra en local hasta que su subida se ha verificado (MD5). El plazo cuenta desde el final de la grabación.
            </p>
          </div>
          <Toggle label="Rotar grabaciones antiguas aunque Drive esté desactivado (modo solo local)" checked={!!s["storage.rotate_when_drive_disabled"]} onChange={(v) => set("storage.rotate_when_drive_disabled", v)} />
          <label className="block max-w-xs">
            <span className="label">Avisar si queda menos de (GB libres)</span>
            <input className="input" type="number" min={1} defaultValue={num("storage.min_free_gb")} onBlur={(e) => set("storage.min_free_gb", Math.max(1, +e.target.value))} />
          </label>
        </div>
      </Card>

      <Card title="Grabación">
        <div className="grid gap-4 sm:grid-cols-2">
          <label>
            <span className="label">Duración máxima de grabación manual (min)</span>
            <input className="input" type="number" min={1} max={240} defaultValue={num("recording.manual_max_minutes")} onBlur={(e) => set("recording.manual_max_minutes", Math.min(240, Math.max(1, +e.target.value)))} />
          </label>
          <label>
            <span className="label">Duración máxima de un clip de evento (min)</span>
            <input className="input" type="number" min={1} max={240} defaultValue={num("recording.event_max_clip_minutes")} onBlur={(e) => set("recording.event_max_clip_minutes", Math.min(240, Math.max(1, +e.target.value)))} />
          </label>
        </div>
      </Card>

      <Card title="Red">
        <Toggle label="Permitir escaneo de la subred local al buscar cámaras" checked={!!s["discovery.allow_subnet_scan"]} onChange={(v) => set("discovery.allow_subnet_scan", v)} />
        <div className="mt-5 flex gap-3 rounded-xl border border-line bg-bg p-4 text-sm">
          <Shield size={18} className="mt-0.5 shrink-0 text-ok" />
          <div className="space-y-2 text-muted">
            <p className="font-medium text-fg">Acceso remoto seguro</p>
            <p>
              La aplicación nunca abre puertos en tu router ni usa UPnP. Para ver tus cámaras fuera de casa, instala <b>Tailscale</b> (lo más sencillo) o <b>WireGuard</b> en
              este equipo y en tu móvil, y abre la app con la IP de la VPN. Tus cámaras siguen sin estar expuestas a Internet.
            </p>
          </div>
        </div>
      </Card>

      <Card title="Seguridad">
        <form onSubmit={changePassword} className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <label>
            <span className="label">Contraseña actual</span>
            <input className="input" type="password" autoComplete="current-password" required value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} />
          </label>
          <label>
            <span className="label">Nueva contraseña (mín. 10)</span>
            <input className="input" type="password" autoComplete="new-password" minLength={10} required value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} />
          </label>
          <button className="btn-secondary">
            <KeyRound size={16} /> Cambiar
          </button>
        </form>
      </Card>
    </div>
  );
}
