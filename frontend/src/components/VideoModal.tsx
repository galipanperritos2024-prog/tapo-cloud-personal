"use client";

import { Cloud, Download, Trash2 } from "lucide-react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { bytes, dateTime, duration, EVENT_LABELS, KIND_LABELS, timeSec } from "@/lib/format";
import type { Recording } from "@/lib/types";
import { Modal } from "./ui";

/** Plays a recording from local storage (HTTP range => instant seeking). */
export default function VideoModal({ recordingId, onClose, onDelete }: { recordingId: number | null; onClose: () => void; onDelete?: (id: number) => void }) {
  const { data: rec } = useSWR<Recording & { events: { id: number; type: string; at: string }[] }>(recordingId ? `/api/recordings/${recordingId}` : null, fetcher);
  return (
    <Modal open={recordingId !== null} onClose={onClose} title={rec ? `${rec.camera_name} · ${dateTime(rec.started_at)}` : "Grabación"} wide>
      {rec && (
        <div className="space-y-4">
          {rec.local_available ? (
            <video key={rec.id} src={`/api/recordings/${rec.id}/file`} controls autoPlay playsInline className="aspect-video w-full rounded-xl bg-black" />
          ) : (
            <div className="flex aspect-video flex-col items-center justify-center gap-3 rounded-xl bg-black text-sm text-muted">
              El archivo local ya se eliminó según la política de retención.
              {rec.drive_url && (
                <a className="btn-primary" href={rec.drive_url} target="_blank" rel="noreferrer">
                  <Cloud size={16} /> Ver en Google Drive
                </a>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
            <div className="text-muted">
              {KIND_LABELS[rec.kind]} · {duration(rec.duration_seconds)} · {bytes(rec.size_bytes)} · <span className="font-mono">{rec.file_path}</span>
            </div>
            <div className="flex gap-2">
              {rec.drive_url && (
                <a className="btn-secondary" href={rec.drive_url} target="_blank" rel="noreferrer">
                  <Cloud size={16} /> Drive
                </a>
              )}
              {rec.local_available && (
                <a className="btn-secondary" href={`/api/recordings/${rec.id}/file?download=true`}>
                  <Download size={16} /> Descargar
                </a>
              )}
              {onDelete && rec.local_available && (
                <button className="btn-danger" onClick={() => onDelete(rec.id)}>
                  <Trash2 size={16} /> Borrar local
                </button>
              )}
            </div>
          </div>
          {rec.events?.length > 0 && (
            <div className="flex flex-wrap gap-2 text-xs">
              {rec.events.map((e) => (
                <span key={e.id} className="rounded-lg bg-surface-2 px-2 py-1">
                  {timeSec(e.at)} · {EVENT_LABELS[e.type] ?? e.type}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
