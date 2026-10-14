"use client";

import { Camera as CameraIcon, Plus } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import useSWR from "swr";
import AddCameraDialog from "@/components/AddCameraDialog";
import CameraCard from "@/components/CameraCard";
import { Card, Empty, PageHeader, StatusBadge } from "@/components/ui";
import { fetcher } from "@/lib/api";
import { MODE_LABELS, relative } from "@/lib/format";
import type { Camera } from "@/lib/types";

function CamerasInner() {
  const params = useSearchParams();
  const router = useRouter();
  const [adding, setAdding] = useState(false);
  const { data: cameras, mutate } = useSWR<Camera[]>("/api/cameras", fetcher, { refreshInterval: 10000 });

  useEffect(() => {
    if (params.get("add")) setAdding(true);
  }, [params]);

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Cámaras"
        subtitle="Cámaras de tu red local. Las credenciales se guardan cifradas."
        actions={
          <button className="btn-primary" onClick={() => setAdding(true)}>
            <Plus size={16} /> Añadir cámara
          </button>
        }
      />
      {cameras?.length === 0 ? (
        <Card>
          <Empty icon={<CameraIcon size={36} />} title="Aún no hay cámaras">
            Pulsa «Añadir cámara» para buscarlas automáticamente en tu red.
          </Empty>
        </Card>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {cameras?.map((c) => <CameraCard key={c.id} camera={c} />)}
          </div>
          <Card title="Detalle" className="mt-8 overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="text-left text-xs text-muted uppercase">
                <tr>
                  <th className="pb-3 font-medium">Nombre</th>
                  <th className="pb-3 font-medium">Estado</th>
                  <th className="pb-3 font-medium">IP</th>
                  <th className="pb-3 font-medium">Vídeo</th>
                  <th className="pb-3 font-medium">Modo</th>
                  <th className="pb-3 font-medium">Visto</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {cameras?.map((c) => (
                  <tr key={c.id} className="cursor-pointer hover:bg-surface-2" onClick={() => router.push(`/camera/?id=${c.id}`)}>
                    <td className="py-3 font-medium">{c.name}</td>
                    <td className="py-3"><StatusBadge status={c.status} /></td>
                    <td className="py-3 font-mono text-xs text-muted">{c.ip}</td>
                    <td className="py-3 text-xs text-muted">
                      {c.stream_info?.codec ? `${c.stream_info.codec.toUpperCase()} ${c.stream_info.width}×${c.stream_info.height} · ${c.stream_info.fps} fps` : "—"}
                    </td>
                    <td className="py-3 text-xs">{MODE_LABELS[c.recording_mode]}</td>
                    <td className="py-3 text-xs text-muted">{relative(c.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}
      <AddCameraDialog
        open={adding}
        onClose={() => {
          setAdding(false);
          if (params.get("add")) router.replace("/cameras/");
        }}
        onAdded={(c) => {
          mutate();
          router.push(`/camera/?id=${c.id}`);
        }}
      />
    </div>
  );
}

export default function CamerasPage() {
  return (
    <Suspense>
      <CamerasInner />
    </Suspense>
  );
}
