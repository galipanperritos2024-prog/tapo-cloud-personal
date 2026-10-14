/** Types mirroring the backend API responses. */

export type CameraStatus = "unknown" | "online" | "offline" | "warning" | "disabled";
export type RecordingMode = "continuous" | "motion" | "event" | "manual" | "schedule";
export type MotionSource = "onvif" | "local" | "both" | "off";

export interface ScheduleWindow {
  days: number[]; // 0 = Monday
  start: string; // "22:00"
  end: string; // "08:00" or "24:00"
}

export interface StreamInfo {
  available?: boolean;
  codec?: string;
  width?: number;
  height?: number;
  fps?: number;
  bitrate_kbps?: number;
  audio_codec?: string;
  error?: string;
}

export interface Camera {
  id: number;
  name: string;
  slug: string;
  ip: string;
  rtsp_port: number;
  onvif_port: number;
  main_stream_path: string;
  sub_stream_path: string | null;
  enabled: boolean;
  manufacturer: string | null;
  model: string | null;
  firmware: string | null;
  onvif_enabled: boolean;
  ptz_enabled: boolean;
  ptz_caps: { pan_tilt?: boolean; zoom?: boolean; presets?: boolean };
  recording_mode: RecordingMode;
  auto_record: boolean;
  segment_minutes: number;
  pre_event_seconds: number;
  post_event_seconds: number;
  motion_source: MotionSource;
  motion_sensitivity: number;
  record_audio: boolean;
  schedule: ScheduleWindow[];
  status: CameraStatus;
  status_detail: string | null;
  last_seen_at: string | null;
  stream_info: StreamInfo;
  sub_stream_info: StreamInfo;
  created_at: string;
  username_masked: string | null;
  rtsp_url_masked: string | null;
}

export interface EventItem {
  id: number;
  camera_id: number | null;
  camera_name: string | null;
  type: string;
  source: string | null;
  occurred_at: string;
  ended_at: string | null;
  message: string | null;
  has_thumbnail: boolean;
  recording_id: number | null;
  acknowledged: boolean;
}

export interface Recording {
  id: number;
  camera_id: number;
  camera_name: string | null;
  kind: "continuous" | "event" | "manual";
  status: string;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  file_name: string;
  file_path: string;
  size_bytes: number | null;
  has_thumbnail: boolean;
  local_available: boolean;
  uploaded_at: string | null;
  drive_url: string | null;
  upload_status: string | null;
}

export interface Timeline {
  day: string;
  start: string;
  end: string;
  recordings: { id: number; kind: string; start: string; end: string; local: boolean; drive: boolean }[];
  events: { id: number; type: string; at: string; recording_id: number | null }[];
}

export interface StreamTicket {
  quality: "main" | "sub";
  whep_url: string;
  hls_url: string;
  token: string;
  expires_in: number;
}

export interface WorkerJob {
  running: boolean;
  restarts?: number;
  last_error?: string | null;
  pid?: number | null;
  connected?: boolean;
}

export interface CameraWorker {
  camera_id: number;
  name: string;
  status: CameraStatus;
  status_detail: string | null;
  using_relay: boolean;
  jobs: Record<string, WorkerJob | null>;
  manual_recording: { recording_id: number; elapsed_seconds: number } | null;
  active_episodes: string[];
}

export interface SystemStatus {
  version: string;
  uptime_seconds: number;
  cpu_percent: number;
  cpu_count: number;
  ram: { total: number; used: number; percent: number };
  disk: { path: string; total: number; used: number; free: number };
  binaries: { ffmpeg: string | null; ffprobe: string | null };
  cameras: { total: number; online: number; offline: number; warning: number; disabled: number };
  upload_queue: Record<string, number>;
  storage: { local_recordings_bytes: number; drive_bytes: number };
  last_upload_at: string | null;
  services: Record<string, boolean>;
  streams_active: number;
  recordings_active: number;
  ffmpeg_processes: number;
  camera_workers: CameraWorker[];
  current_upload: { file: string; bytes: number; total: number } | null;
}

export interface DriveStatus {
  connected: boolean;
  configured: boolean;
  email?: string | null;
  error?: string;
  client_id: string | null;
  redirect_uri: string;
  enabled: boolean;
  root_folder: string;
  quota: { limit?: number; usage?: number; usageInDrive?: number } | null;
  uploaded_bytes: number;
  queue: Record<string, number>;
  current_upload: { file: string; bytes: number; total: number } | null;
  last_success_at: string | null;
  last_error: string | null;
  online: boolean;
}

export interface QueueItem {
  id: number;
  recording_id: number;
  camera_name: string | null;
  file: string;
  size_bytes: number | null;
  status: string;
  attempts: number;
  bytes_uploaded: number;
  next_attempt_at: string | null;
  last_error: string | null;
  drive_path: string | null;
  completed_at: string | null;
}

export interface DiscoveredDevice {
  ip: string;
  onvif_port: number | null;
  rtsp_port: number | null;
  name: string | null;
  hardware: string | null;
  methods: string[];
  likely_tapo: boolean;
  already_added: boolean;
}

export type Settings = Record<string, unknown> & { retention_choices: string[] };
