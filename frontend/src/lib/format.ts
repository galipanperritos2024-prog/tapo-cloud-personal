/** Formatting helpers (Spanish locale, local time zone). */

const LOCALE = "es-ES";

export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function time(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
}

export function timeSec(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(LOCALE, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function relative(iso: string | null | undefined): string {
  if (!iso) return "nunca";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  return `hace ${Math.floor(s / 86400)} d`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min ${r.toString().padStart(2, "0")} s`;
  return `${r} s`;
}

export function todayISO(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export const EVENT_LABELS: Record<string, string> = {
  motion: "Movimiento",
  person: "Persona detectada",
  vehicle: "Vehículo detectado",
  animal: "Animal detectado",
  camera_offline: "Cámara desconectada",
  camera_online: "Cámara reconectada",
  recording_started: "Grabación iniciada",
  recording_finished: "Grabación finalizada",
  storage_error: "Error de almacenamiento",
  upload_error: "Error de subida",
};

export const MODE_LABELS: Record<string, string> = {
  continuous: "Continua 24/7",
  motion: "Por movimiento",
  event: "Por evento (persona/vehículo/animal)",
  manual: "Manual",
  schedule: "Por horario",
};

export const KIND_LABELS: Record<string, string> = { continuous: "Continua", event: "Evento", manual: "Manual" };

export const DAY_LABELS = ["L", "M", "X", "J", "V", "S", "D"];
