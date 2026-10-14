"use client";

/**
 * Live player.
 *
 * 1. WebRTC via WHEP (MediaMTX) — typically 0.3–1 s latency on a LAN.
 * 2. Falls back to LL-HLS (hls.js) — ~2–4 s — when WebRTC cannot connect
 *    (strict firewalls, some VPNs).
 *
 * The browser never sees camera credentials: it gets a short-lived token from
 * the backend, valid only for this camera, which MediaMTX verifies with us.
 */

import { Maximize, RefreshCw, Volume2, VolumeX } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { StreamTicket } from "@/lib/types";
import { Spinner } from "./ui";

type Mode = "webrtc" | "hls";
type State = "connecting" | "playing" | "error";

async function waitIceGathering(pc: RTCPeerConnection, timeoutMs = 1500) {
  if (pc.iceGatheringState === "complete") return;
  await new Promise<void>((resolve) => {
    const t = setTimeout(resolve, timeoutMs); // LAN host candidates arrive almost instantly
    pc.addEventListener("icegatheringstatechange", () => {
      if (pc.iceGatheringState === "complete") {
        clearTimeout(t);
        resolve();
      }
    });
  });
}

export default function LivePlayer({ cameraId, quality = "sub", className = "" }: { cameraId: number; quality?: "main" | "sub"; className?: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const cleanupRef = useRef<() => void>(() => {});
  const [mode, setMode] = useState<Mode | null>(null);
  const [state, setState] = useState<State>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [muted, setMuted] = useState(true);
  const [attempt, setAttempt] = useState(0);

  const startHls = useCallback(async (ticket: StreamTicket) => {
    const video = videoRef.current!;
    const url = `${ticket.hls_url}?token=${encodeURIComponent(ticket.token)}`;
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = url; // Safari: native HLS
      cleanupRef.current = () => {
        video.removeAttribute("src");
        video.load();
      };
    } else {
      const Hls = (await import("hls.js")).default;
      if (!Hls.isSupported()) throw new Error("Este navegador no soporta WebRTC ni HLS");
      const hls = new Hls({
        lowLatencyMode: true,
        liveSyncDurationCount: 1,
        // Token travels as a query parameter on every playlist/segment request.
        xhrSetup: (xhr, u) => {
          if (!u.includes("token=")) xhr.open("GET", `${u}${u.includes("?") ? "&" : "?"}token=${encodeURIComponent(ticket.token)}`, true);
        },
      });
      hls.on(Hls.Events.ERROR, (_e, data) => {
        if (data.fatal) {
          setState("error");
          setError(
            String(data.details).includes("IncompatibleCodecs")
              ? "Tu navegador no puede reproducir el códec de esta cámara (prueba Chrome, Edge o Safari, o configura la cámara en H.264)."
              : "Se perdió la conexión con el stream (HLS)",
          );
        }
      });
      hls.loadSource(url);
      hls.attachMedia(video);
      cleanupRef.current = () => hls.destroy();
    }
    setMode("hls");
  }, []);

  const startWebRTC = useCallback(async (ticket: StreamTicket) => {
    const pc = new RTCPeerConnection({ iceServers: [] }); // LAN/VPN only: no STUN/TURN
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("audio", { direction: "recvonly" });
    const stream = new MediaStream();
    pc.ontrack = (ev) => {
      stream.addTrack(ev.track);
      if (videoRef.current && videoRef.current.srcObject !== stream) videoRef.current.srcObject = stream;
    };
    await pc.setLocalDescription(await pc.createOffer());
    await waitIceGathering(pc);
    const res = await fetch(ticket.whep_url, {
      method: "POST",
      headers: { "Content-Type": "application/sdp", Authorization: `Bearer ${ticket.token}` },
      body: pc.localDescription!.sdp,
    });
    if (res.status !== 201) {
      pc.close();
      const detail = await res.text().catch(() => "");
      throw new Error(res.status === 401 ? "Acceso al stream denegado" : detail.includes("codecs not supported") ? "Códec no soportado por WebRTC en este navegador" : `WHEP ${res.status}`);
    }
    const session = res.headers.get("Location");
    await pc.setRemoteDescription({ type: "answer", sdp: await res.text() });

    // Fail fast if media does not flow (e.g. UDP blocked).
    await new Promise<void>((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("WebRTC no conectó a tiempo")), 6000);
      const check = () => {
        if (pc.connectionState === "connected") {
          clearTimeout(t);
          resolve();
        } else if (pc.connectionState === "failed") {
          clearTimeout(t);
          reject(new Error("WebRTC falló"));
        }
      };
      pc.addEventListener("connectionstatechange", check);
      check();
    }).catch((e) => {
      pc.close();
      throw e;
    });

    pc.addEventListener("connectionstatechange", () => {
      if (["failed", "disconnected"].includes(pc.connectionState)) {
        setState("error");
        setError("Se perdió la conexión en directo");
      }
    });
    cleanupRef.current = () => {
      if (session) {
        // whep_url may be relative ("/webrtc/...") when served behind the reverse proxy.
        const url = new URL(session, new URL(ticket.whep_url, window.location.href)).toString();
        fetch(url, { method: "DELETE", headers: { Authorization: `Bearer ${ticket.token}` } }).catch(() => undefined);
      }
      pc.close();
    };
    setMode("webrtc");
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState("connecting");
    setError(null);
    (async () => {
      try {
        const ticket = await api<StreamTicket>(`/api/cameras/${cameraId}/stream?quality=${quality}`);
        if (cancelled) return;
        try {
          await startWebRTC(ticket);
        } catch (e) {
          if (cancelled) return;
          console.warn("WebRTC unavailable, falling back to HLS:", e);
          await startHls(ticket);
        }
        if (!cancelled) setState("playing");
      } catch (e) {
        if (!cancelled) {
          setState("error");
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    })();
    return () => {
      cancelled = true;
      cleanupRef.current();
      cleanupRef.current = () => {};
    };
  }, [cameraId, quality, attempt, startHls, startWebRTC]);

  return (
    <div ref={wrapRef} className={`group relative aspect-video overflow-hidden rounded-2xl bg-black ${className}`}>
      <video ref={videoRef} className="h-full w-full object-contain" autoPlay playsInline muted={muted} />
      {state === "connecting" && (
        <div className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-muted">
          <Spinner /> Conectando…
        </div>
      )}
      {state === "error" && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/70 p-4 text-center text-sm">
          <span className="text-err">{error}</span>
          <button className="btn-secondary" onClick={() => setAttempt((a) => a + 1)}>
            <RefreshCw size={14} /> Reintentar
          </button>
        </div>
      )}
      {state === "playing" && (
        <>
          <span className="absolute top-3 left-3 flex items-center gap-1.5 rounded-lg bg-black/60 px-2 py-1 text-[11px] font-semibold">
            <span className="h-2 w-2 animate-pulse rounded-full bg-err" /> EN DIRECTO · {mode === "webrtc" ? "WebRTC" : "HLS"}
          </span>
          <div className="absolute right-3 bottom-3 flex gap-2 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
            <button className="rounded-lg bg-black/60 p-2 hover:bg-black/80" onClick={() => setMuted((m) => !m)} aria-label={muted ? "Activar sonido" : "Silenciar"}>
              {muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
            </button>
            <button className="rounded-lg bg-black/60 p-2 hover:bg-black/80" onClick={() => wrapRef.current?.requestFullscreen?.()} aria-label="Pantalla completa">
              <Maximize size={16} />
            </button>
          </div>
        </>
      )}
    </div>
  );
}
