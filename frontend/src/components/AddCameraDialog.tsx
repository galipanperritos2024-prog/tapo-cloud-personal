"use client";

/**
 * "Add camera" wizard:
 *   1. Find it (network discovery) or type its IP.
 *   2. Camera account credentials -> live connection test.
 *   3. Name + recording mode -> save.
 */

import { CheckCircle2, ChevronDown, Radar, Search, Wifi, XCircle } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import type { Camera, DiscoveredDevice } from "@/lib/types";
import RecordingForm, { DEFAULT_RECORDING, type RecordingValues } from "./RecordingForm";
import { ErrorBox, Modal, Spinner, Toggle } from "./ui";

interface TestResult {
  ok: boolean;
  main: { available: boolean; codec?: string; width?: number; height?: number; fps?: number; audio_codec?: string; error?: string };
  sub: { available: boolean; width?: number; height?: number } | null;
  onvif: { ok: boolean; model?: string; manufacturer?: string; ptz?: { pan_tilt?: boolean; zoom?: boolean }; events?: boolean; error?: string } | null;
}

export default function AddCameraDialog({ open, onClose, onAdded }: { open: boolean; onClose: () => void; onAdded: (c: Camera) => void }) {
  const [step, setStep] = useState(1);
  const [devices, setDevices] = useState<DiscoveredDevice[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [subnetScan, setSubnetScan] = useState(true);
  const [form, setForm] = useState({ name: "", ip: "", rtsp_port: 554, onvif_port: 2020, main_stream_path: "stream1", sub_stream_path: "stream2", username: "", password: "", onvif_enabled: true });
  const [advanced, setAdvanced] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [rec, setRec] = useState<RecordingValues>(DEFAULT_RECORDING);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  const reset = () => {
    setStep(1);
    setTest(null);
    setError(null);
    setForm((f) => ({ ...f, name: "", ip: "", username: "", password: "" }));
  };
  const close = () => {
    reset();
    onClose();
  };

  const scan = async () => {
    setScanning(true);
    setError(null);
    try {
      const res = await api<{ devices: DiscoveredDevice[] }>("/api/discovery/scan", { json: { subnet_scan: subnetScan } });
      setDevices(res.devices);
    } catch (e) {
      setError(e);
    } finally {
      setScanning(false);
    }
  };

  const pick = (d: DiscoveredDevice) => {
    setForm((f) => ({ ...f, ip: d.ip, onvif_port: d.onvif_port ?? 2020, rtsp_port: d.rtsp_port ?? 554, name: f.name || (d.hardware ? `Tapo ${d.hardware}` : "") }));
    setStep(2);
  };

  const runTest = async () => {
    setTesting(true);
    setError(null);
    setTest(null);
    try {
      const r = await api<TestResult>("/api/cameras/test", { json: { ...form, onvif: form.onvif_enabled } });
      setTest(r);
      if (r.onvif && !r.onvif.ok) setForm((f) => ({ ...f, onvif_enabled: false }));
      if (!r.onvif?.ok) setRec((v) => ({ ...v, motion_source: v.motion_source === "off" ? "off" : "local", recording_mode: v.recording_mode === "event" ? "motion" : v.recording_mode }));
    } catch (e) {
      setError(e);
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const cam = await api<Camera>("/api/cameras", { json: { ...form, sub_stream_path: form.sub_stream_path || null, ...rec } });
      onAdded(cam);
      close();
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };

  const f = (k: keyof typeof form) => ({
    value: String(form[k]),
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: typeof form[k] === "number" ? +e.target.value : e.target.value }),
  });

  return (
    <Modal open={open} onClose={close} title={`Añadir cámara · paso ${step} de 3`}>
      {step === 1 && (
        <div className="space-y-4">
          <p className="text-sm text-muted">Busca cámaras en tu red local (ONVIF) o introduce la IP manualmente.</p>
          <div className="flex flex-wrap items-center gap-3">
            <button className="btn-primary" onClick={scan} disabled={scanning}>
              {scanning ? <Spinner /> : <Radar size={16} />} Buscar cámaras
            </button>
            <Toggle label={<span className="text-xs text-muted">Escanear también la subred (puertos 554/2020)</span>} checked={subnetScan} onChange={setSubnetScan} />
          </div>
          {devices && (
            <div className="space-y-2">
              {devices.length === 0 && <p className="text-sm text-muted">No se encontró ningún dispositivo. Comprueba que ONVIF está activo o añade la IP manualmente.</p>}
              {devices.map((d) => (
                <button
                  key={d.ip}
                  disabled={d.already_added}
                  onClick={() => pick(d)}
                  className="flex w-full items-center justify-between rounded-xl border border-line bg-bg p-3 text-left hover:border-accent disabled:opacity-50"
                >
                  <span className="flex items-center gap-3">
                    <Wifi size={18} className={d.likely_tapo ? "text-accent" : "text-muted"} />
                    <span>
                      <span className="block text-sm font-medium">{d.name ?? d.hardware ?? "Dispositivo"} · {d.ip}</span>
                      <span className="block text-xs text-muted">
                        {[d.likely_tapo && "Probable Tapo", d.onvif_port && `ONVIF :${d.onvif_port}`, d.rtsp_port && `RTSP :${d.rtsp_port}`].filter(Boolean).join(" · ")}
                      </span>
                    </span>
                  </span>
                  <span className="text-xs text-muted">{d.already_added ? "Ya añadida" : "Seleccionar"}</span>
                </button>
              ))}
            </div>
          )}
          <div className="flex gap-2 border-t border-line pt-4">
            <input className="input" placeholder="IP manual, p. ej. 192.168.1.30" value={form.ip} onChange={(e) => setForm({ ...form, ip: e.target.value.trim() })} />
            <button className="btn-secondary" disabled={!form.ip} onClick={() => setStep(2)}>
              <Search size={16} /> Usar IP
            </button>
          </div>
          <ErrorBox error={error} />
        </div>
      )}

      {step === 2 && (
        <div className="space-y-4">
          <div className="rounded-xl border border-accent/30 bg-accent/10 p-3 text-xs leading-relaxed text-fg/90">
            Usa la <b>cuenta de cámara</b> creada en la app Tapo (Cámara → Configuración → Configuración avanzada → Cuenta de la cámara). No es tu
            cuenta TP-Link. La contraseña se guarda cifrada y nunca se muestra.
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label>
              <span className="label">IP</span>
              <input className="input" {...f("ip")} />
            </label>
            <label>
              <span className="label">Usuario</span>
              <input className="input" autoComplete="off" {...f("username")} />
            </label>
            <label className="sm:col-span-2">
              <span className="label">Contraseña</span>
              <input className="input" type="password" autoComplete="new-password" {...f("password")} />
            </label>
          </div>
          <button className="btn-ghost px-0 text-xs" onClick={() => setAdvanced(!advanced)}>
            <ChevronDown size={14} className={advanced ? "rotate-180" : ""} /> Opciones avanzadas
          </button>
          {advanced && (
            <div className="grid gap-3 sm:grid-cols-2">
              <label><span className="label">Puerto RTSP</span><input className="input" type="number" {...f("rtsp_port")} /></label>
              <label><span className="label">Puerto ONVIF</span><input className="input" type="number" {...f("onvif_port")} /></label>
              <label><span className="label">Stream principal</span><input className="input" {...f("main_stream_path")} /></label>
              <label><span className="label">Stream secundario</span><input className="input" {...f("sub_stream_path")} /></label>
              <div className="sm:col-span-2">
                <Toggle label="Usar ONVIF (PTZ, eventos, información)" checked={form.onvif_enabled} onChange={(v) => setForm({ ...form, onvif_enabled: v })} />
              </div>
            </div>
          )}
          <button className="btn-secondary w-full" onClick={runTest} disabled={testing || !form.ip || !form.username || !form.password}>
            {testing ? <Spinner /> : null} Probar conexión
          </button>
          {test && (
            <div className="space-y-2 rounded-xl border border-line bg-bg p-3 text-sm">
              <TestLine ok={test.main.available} label="Vídeo principal" detail={test.main.available ? `${test.main.codec?.toUpperCase()} ${test.main.width}×${test.main.height} @ ${test.main.fps} fps${test.main.audio_codec ? " · audio" : ""}` : test.main.error} />
              {test.sub && <TestLine ok={test.sub.available} label="Vídeo secundario" detail={test.sub.available ? `${test.sub.width}×${test.sub.height}` : "No disponible"} />}
              {test.onvif && (
                <TestLine
                  ok={test.onvif.ok}
                  label="ONVIF"
                  detail={test.onvif.ok ? `${test.onvif.manufacturer ?? ""} ${test.onvif.model ?? ""}${test.onvif.ptz?.pan_tilt ? " · PTZ" : ""}${test.onvif.events ? " · eventos" : ""}` : test.onvif.error}
                />
              )}
            </div>
          )}
          <ErrorBox error={error} />
          <div className="flex justify-between">
            <button className="btn-ghost" onClick={() => setStep(1)}>Atrás</button>
            <button className="btn-primary" disabled={!test?.ok} onClick={() => setStep(3)}>Continuar</button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="space-y-5">
          <label>
            <span className="label">Nombre</span>
            <input className="input" placeholder="Salón, Entrada, Habitación…" maxLength={48} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <RecordingForm value={rec} onChange={setRec} onvifAvailable={!!test?.onvif?.ok} />
          <ErrorBox error={error} />
          <div className="flex justify-between">
            <button className="btn-ghost" onClick={() => setStep(2)}>Atrás</button>
            <button className="btn-primary" disabled={!form.name || saving} onClick={save}>
              {saving && <Spinner />} Guardar cámara
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function TestLine({ ok, label, detail }: { ok: boolean; label: string; detail?: string | null }) {
  return (
    <div className="flex items-start gap-2">
      {ok ? <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-ok" /> : <XCircle size={16} className="mt-0.5 shrink-0 text-err" />}
      <div>
        <div className="font-medium">{label}</div>
        {detail && <div className="text-xs text-muted">{detail}</div>}
      </div>
    </div>
  );
}
