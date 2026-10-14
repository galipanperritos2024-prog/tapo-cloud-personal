"use client";

/** Small shared UI primitives (status badge, cards, toggles, modal...). */

import { X } from "lucide-react";
import { type ReactNode, useEffect } from "react";
import type { CameraStatus } from "@/lib/types";

const STATUS: Record<string, { label: string; dot: string; text: string }> = {
  online: { label: "Online", dot: "bg-ok", text: "text-ok" },
  warning: { label: "Advertencia", dot: "bg-warn", text: "text-warn" },
  offline: { label: "Offline", dot: "bg-err", text: "text-err" },
  unknown: { label: "Conectando…", dot: "bg-muted animate-pulse", text: "text-muted" },
  disabled: { label: "Desactivada", dot: "bg-muted", text: "text-muted" },
};

export function StatusBadge({ status, className = "" }: { status: CameraStatus | string; className?: string }) {
  const s = STATUS[status] ?? STATUS.unknown;
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${s.text} ${className}`}>
      <span className={`h-2 w-2 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

export function ServiceDot({ ok, label, detail }: { ok: boolean | undefined; label: string; detail?: string }) {
  const color = ok === undefined ? "bg-muted" : ok ? "bg-ok" : "bg-err";
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span className="flex items-center gap-2 text-sm">
        <span className={`h-2 w-2 rounded-full ${color}`} />
        {label}
      </span>
      {detail && <span className="text-xs text-muted">{detail}</span>}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card p-5 ${className}`}>
      {(title || actions) && (
        <div className="mb-4 flex items-center justify-between gap-2">
          {title && <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, tone = "default", icon }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "default" | "ok" | "warn" | "err"; icon?: ReactNode }) {
  const toneCls = { default: "text-fg", ok: "text-ok", warn: "text-warn", err: "text-err" }[tone];
  return (
    <div className="card flex items-start justify-between gap-3 p-4 sm:p-5">
      <div className="min-w-0">
        <div className="text-[11px] font-medium tracking-wide text-muted uppercase sm:text-xs">{label}</div>
        <div className={`mt-2 text-xl font-semibold sm:text-3xl ${toneCls}`}>{value}</div>
        {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
      </div>
      {icon && <div className="hidden rounded-xl bg-surface-2 p-2.5 text-muted sm:block">{icon}</div>}
    </div>
  );
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode; disabled?: boolean }) {
  return (
    <label className={`flex cursor-pointer items-center justify-between gap-3 ${disabled ? "opacity-50" : ""}`}>
      {label && <span className="text-sm">{label}</span>}
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${checked ? "bg-accent" : "bg-surface-3"}`}
      >
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${checked ? "left-5.5" : "left-0.5"}`} />
      </button>
    </label>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-0 backdrop-blur-sm sm:items-center sm:p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`card max-h-[92vh] w-full overflow-y-auto rounded-b-none p-6 sm:rounded-2xl ${wide ? "max-w-5xl" : "max-w-xl"}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button className="btn-ghost p-2" onClick={onClose} aria-label="Cerrar">
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="rounded-xl border border-err/30 bg-err/10 px-4 py-3 text-sm text-err">{msg}</div>;
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      {icon && <div className="text-muted">{icon}</div>}
      <div className="font-medium">{title}</div>
      {children && <div className="max-w-md text-sm text-muted">{children}</div>}
    </div>
  );
}

export function ProgressBar({ value, tone = "accent" }: { value: number; tone?: "accent" | "ok" | "warn" | "err" }) {
  const color = { accent: "bg-accent", ok: "bg-ok", warn: "bg-warn", err: "bg-err" }[tone];
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-surface-3">
      <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

export function Spinner({ size = 16 }: { size?: number }) {
  return <span className="inline-block animate-spin rounded-full border-2 border-current border-t-transparent" style={{ width: size, height: size }} />;
}
