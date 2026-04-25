import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BrowserMultiFormatReader } from '@zxing/browser';
import {
  BarcodeFormat,
  BinaryBitmap,
  DecodeHintType,
  HTMLCanvasElementLuminanceSource,
  HybridBinarizer,
  MultiFormatReader,
  NotFoundException,
} from '@zxing/library';
import { Button } from '@/components/ui/button';
import { ClipboardPaste, GripHorizontal, Loader2, RefreshCcw, Upload, Zap, ZapOff } from 'lucide-react';

// ─── Public API ───────────────────────────────────────────────────────────────

export interface DispatchQrScannerProps {
  active: boolean;
  onScan: (decodedText: string) => void;
  onScannerStateChange?: (isScanning: boolean) => void;
  onScannerError?: (message: string) => void;
}

// ─── Internal types ───────────────────────────────────────────────────────────

type ExtendedTrackConstraints = MediaTrackConstraintSet & {
  zoom?: number;
  torch?: boolean;
  focusMode?: string;
};

interface CameraCapabilities {
  zoomSupported: boolean;
  zoomMin: number;
  zoomMax: number;
  zoomStep: number;
  torchSupported: boolean;
  continuousFocusSupported: boolean;
  singleShotFocusSupported: boolean;
}

interface CameraInfo {
  width: number;
  height: number;
}

interface ScanRegion {
  x: number;      // CSS px from container left
  y: number;      // CSS px from container top
  width: number;
  height: number;
}

type Corner = 'nw' | 'ne' | 'sw' | 'se';

interface CornerDrag {
  corner: Corner;
  startX: number;
  startY: number;
  startRegion: ScanRegion;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const DEFAULT_CAPABILITIES: CameraCapabilities = {
  zoomSupported: false,
  zoomMin: 1,
  zoomMax: 1,
  zoomStep: 0.1,
  torchSupported: false,
  continuousFocusSupported: false,
  singleShotFocusSupported: false,
};

// QR-only + exhaustive search — same hints used for both live and image decode
const ZXING_HINTS = new Map<DecodeHintType, unknown>([
  [DecodeHintType.POSSIBLE_FORMATS, [BarcodeFormat.QR_CODE]],
  [DecodeHintType.TRY_HARDER, true],
]);

const DECODE_INTERVAL_MS = 180;
const CAMERA_SETTLE_DELAY_MS = 1000;
const READER_RESET_EVERY_FAILURES = 45;

// Debug toggle: set true temporarily to bypass crop decoding and test full-frame decode.
// Keep false in production so overlay region remains the active decode target.
const DEBUG_FULL_FRAME_DECODE = false;

const SW_ZOOM_MIN = 1;
const SW_ZOOM_MAX = 3;
const SW_ZOOM_STEP = 0.1;

const VIEWPORT_DEFAULT_H = 320;
const VIEWPORT_MIN_H = 180;
const VIEWPORT_MAX_H = 700;

const MIN_REGION_PX = 80;
const HANDLE_PX = 14;
const HANDLE_HALF = HANDLE_PX / 2;

const CORNERS: Corner[] = ['nw', 'ne', 'sw', 'se'];

const RESOLUTION_TIERS: MediaStreamConstraints[] = [
  {
    video: {
      facingMode: { ideal: 'environment' },
      width: { ideal: 1920, min: 1280 },
      height: { ideal: 1080, min: 720 },
    },
    audio: false,
  },
  {
    video: {
      facingMode: { ideal: 'environment' },
      width: { ideal: 1280 },
      height: { ideal: 720 },
    },
    audio: false,
  },
  {
    video: { width: { ideal: 1280 }, height: { ideal: 720 } },
    audio: false,
  },
  { video: true, audio: false },
];

// ─── Pure helpers ─────────────────────────────────────────────────────────────

function defaultRegion(containerW: number, containerH: number): ScanRegion {
  const size = Math.round(Math.min(containerW, containerH) * 0.65);
  return {
    x: Math.round((containerW - size) / 2),
    y: Math.round((containerH - size) / 2),
    width: size,
    height: size,
  };
}

function clampRegionToContainer(region: ScanRegion, containerW: number, containerH: number): ScanRegion {
  const cW = Math.max(1, containerW);
  const cH = Math.max(1, containerH);
  const width = Math.max(MIN_REGION_PX, Math.min(region.width, cW));
  const height = Math.max(MIN_REGION_PX, Math.min(region.height, cH));
  const x = Math.max(0, Math.min(region.x, cW - width));
  const y = Math.max(0, Math.min(region.y, cH - height));
  return { x, y, width, height };
}

function expandRegion(region: ScanRegion, containerW: number, containerH: number, factor: number): ScanRegion {
  const cW = Math.max(1, containerW);
  const cH = Math.max(1, containerH);
  const cx = region.x + region.width / 2;
  const cy = region.y + region.height / 2;
  const width = Math.min(cW, Math.max(MIN_REGION_PX, region.width * factor));
  const height = Math.min(cH, Math.max(MIN_REGION_PX, region.height * factor));
  return clampRegionToContainer(
    {
      x: cx - width / 2,
      y: cy - height / 2,
      width,
      height,
    },
    cW,
    cH,
  );
}

/**
 * Maps a CSS-pixel scan region (drawn over the container) to source coordinates
 * in the native video frame, accounting for object-fit: cover scaling and offset.
 *
 * object-fit: cover scales the video so it fills the container while preserving
 * aspect ratio — one axis may be cropped. The rendered video's position relative
 * to the container can have a negative offset (overflow) on the cropped axis.
 * We reverse that transform to find the actual video pixels the region covers.
 */
function cssRegionToVideoRegion(
  region: ScanRegion,
  container: { width: number; height: number },
  video: { width: number; height: number },
): { sx: number; sy: number; sWidth: number; sHeight: number } {
  const cW = Math.max(1, container.width);
  const cH = Math.max(1, container.height);
  const vW = Math.max(1, video.width);
  const vH = Math.max(1, video.height);

  // object-fit: cover => scale up to fill container, then crop overflow.
  const coverScale = Math.max(cW / vW, cH / vH);
  const renderedW = vW * coverScale;
  const renderedH = vH * coverScale;
  const offsetX = (cW - renderedW) / 2;
  const offsetY = (cH - renderedH) / 2;

  // Clamp the CSS region to container bounds first for numeric safety.
  const rLeft = Math.max(0, Math.min(cW, region.x));
  const rTop = Math.max(0, Math.min(cH, region.y));
  const rRight = Math.max(rLeft, Math.min(cW, region.x + region.width));
  const rBottom = Math.max(rTop, Math.min(cH, region.y + region.height));

  // Convert container-space points into rendered-video space, then to source pixels.
  const leftRendered = rLeft - offsetX;
  const topRendered = rTop - offsetY;
  const rightRendered = rRight - offsetX;
  const bottomRendered = rBottom - offsetY;

  const toVideoX = (value: number) => (value / renderedW) * vW;
  const toVideoY = (value: number) => (value / renderedH) * vH;

  const sx0 = Math.max(0, Math.min(vW, toVideoX(leftRendered)));
  const sy0 = Math.max(0, Math.min(vH, toVideoY(topRendered)));
  const sx1 = Math.max(0, Math.min(vW, toVideoX(rightRendered)));
  const sy1 = Math.max(0, Math.min(vH, toVideoY(bottomRendered)));

  const sx = Math.min(sx0, sx1);
  const sy = Math.min(sy0, sy1);
  const sWidth = Math.max(0, Math.abs(sx1 - sx0));
  const sHeight = Math.max(0, Math.abs(sy1 - sy0));

  return { sx, sy, sWidth, sHeight };
}

function cornerHandleStyle(corner: Corner): React.CSSProperties {
  const base: React.CSSProperties = {
    position: 'absolute',
    width: HANDLE_PX,
    height: HANDLE_PX,
    backgroundColor: 'white',
    border: '2px solid hsl(var(--primary))',
    borderRadius: 3,
    touchAction: 'none',
  };
  switch (corner) {
    case 'nw': return { ...base, top: -HANDLE_HALF, left: -HANDLE_HALF, cursor: 'nwse-resize' };
    case 'ne': return { ...base, top: -HANDLE_HALF, right: -HANDLE_HALF, cursor: 'nesw-resize' };
    case 'sw': return { ...base, bottom: -HANDLE_HALF, left: -HANDLE_HALF, cursor: 'nesw-resize' };
    case 'se': return { ...base, bottom: -HANDLE_HALF, right: -HANDLE_HALF, cursor: 'nwse-resize' };
  }
}

// ─── Camera helpers ───────────────────────────────────────────────────────────

async function acquireStream(): Promise<MediaStream> {
  let lastError: unknown;
  for (const constraints of RESOLUTION_TIERS) {
    try {
      return await navigator.mediaDevices.getUserMedia(constraints);
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError;
}

function releaseStream(stream: MediaStream | null): void {
  stream?.getTracks().forEach((t) => t.stop());
}

function detectCapabilities(stream: MediaStream): CameraCapabilities {
  const track = stream.getVideoTracks()[0];
  if (!track || typeof track.getCapabilities !== 'function') return DEFAULT_CAPABILITIES;
  const caps = track.getCapabilities() as Record<string, unknown>;
  const zoomCap = caps.zoom as { min?: number; max?: number; step?: number } | undefined;
  const focusModes = Array.isArray(caps.focusMode) ? (caps.focusMode as string[]) : [];
  return {
    zoomSupported: Boolean(zoomCap),
    zoomMin: zoomCap?.min ?? 1,
    zoomMax: zoomCap?.max ?? 1,
    zoomStep: zoomCap?.step ?? 0.1,
    torchSupported: Boolean(caps.torch),
    continuousFocusSupported: focusModes.includes('continuous'),
    singleShotFocusSupported: focusModes.includes('single-shot'),
  };
}

async function applyAutofocus(track: MediaStreamTrack, caps: CameraCapabilities): Promise<void> {
  if (!caps.continuousFocusSupported) return;
  try {
    await track.applyConstraints({
      advanced: [{ focusMode: 'continuous' } as ExtendedTrackConstraints],
    });
  } catch { /* non-fatal */ }
}

async function triggerFocusCycle(track: MediaStreamTrack, caps: CameraCapabilities): Promise<void> {
  if (!caps.singleShotFocusSupported && !caps.continuousFocusSupported) return;
  try {
    if (caps.singleShotFocusSupported) {
      await track.applyConstraints({ advanced: [{ focusMode: 'single-shot' } as ExtendedTrackConstraints] });
    }
    if (caps.continuousFocusSupported) {
      await track.applyConstraints({ advanced: [{ focusMode: 'continuous' } as ExtendedTrackConstraints] });
    }
  } catch { /* non-fatal */ }
}

function readCameraInfo(stream: MediaStream): CameraInfo {
  const s = stream.getVideoTracks()[0]?.getSettings();
  return { width: s?.width ?? 0, height: s?.height ?? 0 };
}

// ─── ZoomControl sub-component ────────────────────────────────────────────────

interface ZoomControlProps {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (v: number) => void;
}

function ZoomControl({ label, min, max, step, value, onChange }: ZoomControlProps) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-24 shrink-0 text-xs text-muted-foreground">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-2 flex-1 cursor-pointer accent-primary"
        aria-label={label}
      />
      <span className="w-10 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
        {value.toFixed(1)}×
      </span>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

const DispatchQrScanner = ({
  active,
  onScan,
  onScannerStateChange,
  onScannerError,
}: DispatchQrScannerProps) => {
  // ── Refs ──────────────────────────────────────────────────────────────────

  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const scanLockedRef = useRef(false);
  const capsRef = useRef<CameraCapabilities>(DEFAULT_CAPABILITIES);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Viewport drag: snapshot of drag start so the move handler computes delta
  const viewportDragRef = useRef<{ startY: number; startH: number } | null>(null);
  // Region corner drag
  const cornerDragRef = useRef<CornerDrag | null>(null);

  const elementId = useMemo(
    () => `dispatch-qr-scanner-${Math.random().toString(36).slice(2, 10)}`,
    [],
  );

  // ── State ─────────────────────────────────────────────────────────────────

  const [capabilities, setCapabilities] = useState<CameraCapabilities>(DEFAULT_CAPABILITIES);
  const [cameraInfo, setCameraInfo] = useState<CameraInfo>({ width: 0, height: 0 });
  const [zoom, setZoom] = useState<number>(1);
  const [torchOn, setTorchOn] = useState<boolean>(false);
  const [initializing, setInitializing] = useState<boolean>(false);
  const [viewportHeight, setViewportHeight] = useState<number>(VIEWPORT_DEFAULT_H);
  const [scanRegion, setScanRegion] = useState<ScanRegion>({ x: 60, y: 60, width: 200, height: 200 });
  const [uploadScanning, setUploadScanning] = useState<boolean>(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // Keep a ref in sync so the decode loop (setInterval closure) always reads the
  // latest region without stale closure issues.
  const scanRegionRef = useRef(scanRegion);
  scanRegionRef.current = scanRegion;

  // ── Scan region init ──────────────────────────────────────────────────────

  // Set the default centred region once the container has laid out.
  useEffect(() => {
    if (!containerRef.current) return;
    const containerW = Math.max(1, containerRef.current.offsetWidth);
    const containerH = Math.max(1, containerRef.current.offsetHeight);
    setScanRegion((prev) => {
      const safePrev = clampRegionToContainer(prev, containerW, containerH);
      if (safePrev.width >= MIN_REGION_PX && safePrev.height >= MIN_REGION_PX) {
        return safePrev;
      }
      return defaultRegion(containerW, containerH);
    });
  }, []);

  // ── Scan region corner drag ───────────────────────────────────────────────

  const handleCornerPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    cornerDragRef.current = {
      corner: e.currentTarget.dataset.corner as Corner,
      startX: e.clientX,
      startY: e.clientY,
      startRegion: { ...scanRegionRef.current },
    };
  }, []);

  const handleCornerPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const drag = cornerDragRef.current;
    if (!drag) return;

    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    const sr = drag.startRegion;
    const cW = containerRef.current?.offsetWidth ?? 500;
    const cH = containerRef.current?.offsetHeight ?? 400;

    let { x, y, width, height } = sr;

    switch (drag.corner) {
      case 'nw':
        x = Math.max(0, Math.min(sr.x + sr.width - MIN_REGION_PX, sr.x + dx));
        y = Math.max(0, Math.min(sr.y + sr.height - MIN_REGION_PX, sr.y + dy));
        width = sr.x + sr.width - x;
        height = sr.y + sr.height - y;
        break;
      case 'ne':
        y = Math.max(0, Math.min(sr.y + sr.height - MIN_REGION_PX, sr.y + dy));
        width = Math.max(MIN_REGION_PX, Math.min(cW - sr.x, sr.width + dx));
        height = sr.y + sr.height - y;
        break;
      case 'sw':
        x = Math.max(0, Math.min(sr.x + sr.width - MIN_REGION_PX, sr.x + dx));
        width = sr.x + sr.width - x;
        height = Math.max(MIN_REGION_PX, Math.min(cH - sr.y, sr.height + dy));
        break;
      case 'se':
        width = Math.max(MIN_REGION_PX, Math.min(cW - sr.x, sr.width + dx));
        height = Math.max(MIN_REGION_PX, Math.min(cH - sr.y, sr.height + dy));
        break;
    }

    // Final container-bounds clamp
    x = Math.max(0, x);
    y = Math.max(0, y);
    width = Math.min(width, cW - x);
    height = Math.min(height, cH - y);

    setScanRegion({ x, y, width, height });
  }, []);

  const handleCornerPointerUp = useCallback(() => {
    cornerDragRef.current = null;
  }, []);

  const resetScanRegion = useCallback(() => {
    if (!containerRef.current) return;
    const cW = Math.max(1, containerRef.current.offsetWidth);
    const cH = Math.max(1, containerRef.current.offsetHeight);
    setScanRegion(defaultRegion(cW, cH));
  }, []);

  // ── Camera zoom + torch ───────────────────────────────────────────────────

  const applyZoom = useCallback(async (value: number) => {
    setZoom(value);
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track || !capsRef.current.zoomSupported) return;
    try {
      await track.applyConstraints({ advanced: [{ zoom: value } as ExtendedTrackConstraints] });
    } catch { /* CSS transform fallback active via zoom state */ }
  }, []);

  const toggleTorch = useCallback(async () => {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track) return;
    const next = !torchOn;
    try {
      await track.applyConstraints({ advanced: [{ torch: next } as ExtendedTrackConstraints] });
      setTorchOn(next);
    } catch { /* non-fatal */ }
  }, [torchOn]);

  const handleVideoTap = useCallback(() => {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track) return;
    void triggerFocusCycle(track, capsRef.current);
  }, []);

  // ── Viewport resize drag handle ───────────────────────────────────────────

  const handleViewportDragStart = useCallback(
    (e: React.MouseEvent<HTMLDivElement> | React.TouchEvent<HTMLDivElement>) => {
      const clientY = 'touches' in e ? e.touches[0].clientY : (e as React.MouseEvent).clientY;
      viewportDragRef.current = {
        startY: clientY,
        startH: containerRef.current?.offsetHeight ?? VIEWPORT_DEFAULT_H,
      };

      const onMove = (ev: MouseEvent | TouchEvent) => {
        if (!viewportDragRef.current) return;
        const y = 'touches' in ev ? (ev as TouchEvent).touches[0]?.clientY ?? 0 : (ev as MouseEvent).clientY;
        const next = Math.max(
          VIEWPORT_MIN_H,
          Math.min(VIEWPORT_MAX_H, viewportDragRef.current.startH + (y - viewportDragRef.current.startY)),
        );
        setViewportHeight(next);
      };

      const onEnd = () => {
        viewportDragRef.current = null;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onEnd);
        window.removeEventListener('touchmove', onMove);
        window.removeEventListener('touchend', onEnd);
      };

      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onEnd);
      window.addEventListener('touchmove', onMove, { passive: true });
      window.addEventListener('touchend', onEnd);
    },
    [],
  );

  // ── Image QR decode (upload + paste share this) ───────────────────────────

  const processImageBlob = useCallback(
    async (blob: Blob) => {
      setUploadError(null);
      setUploadScanning(true);
      const url = URL.createObjectURL(blob);
      try {
        const img = new Image();
        await new Promise<void>((resolve, reject) => {
          img.onload = () => resolve();
          img.onerror = () => reject(new Error('Image failed to load'));
          img.src = url;
        });
        const reader = new BrowserMultiFormatReader(ZXING_HINTS);
        const result = await reader.decodeFromImageElement(img);
        const text = result.getText().trim();
        if (text) onScan(text);
      } catch (err) {
        if (err instanceof NotFoundException) {
          setUploadError('No QR code detected in this image.');
        } else {
          setUploadError(`Could not read QR: ${err instanceof Error ? err.message : 'Unknown error'}`);
        }
      } finally {
        URL.revokeObjectURL(url);
        setUploadScanning(false);
      }
    },
    [onScan],
  );

  const handleUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (fileInputRef.current) fileInputRef.current.value = '';
      if (!file) return;
      await processImageBlob(file);
    },
    [processImageBlob],
  );

  const openFilePicker = useCallback(() => fileInputRef.current?.click(), []);

  const handlePasteButton = useCallback(async () => {
    try {
      const items = await navigator.clipboard.read();
      for (const item of items) {
        const imageType = item.types.find((t) => t.startsWith('image/'));
        if (imageType) {
          await processImageBlob(await item.getType(imageType));
          return;
        }
      }
      setUploadError('No image found in clipboard. Copy a QR image first.');
    } catch (err) {
      const msg = err instanceof Error ? err.message : '';
      setUploadError(
        msg.toLowerCase().includes('denied') || msg.toLowerCase().includes('permission')
          ? 'Clipboard access denied. Try Ctrl+V instead.'
          : 'Could not read clipboard. Try uploading the file directly.',
      );
    }
  }, [processImageBlob]);

  // ── Global Ctrl+V paste ───────────────────────────────────────────────────

  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      for (const item of Array.from(e.clipboardData?.items ?? [])) {
        if (item.type.startsWith('image/')) {
          const blob = item.getAsFile();
          if (blob) void processImageBlob(blob);
          return;
        }
      }
    };
    document.addEventListener('paste', onPaste);
    return () => document.removeEventListener('paste', onPaste);
  }, [processImageBlob]);

  // ── Scanner lifecycle ─────────────────────────────────────────────────────
  //
  // Uses a manual setInterval decode loop instead of ZXing's decodeFromStream.
  // Each tick:
  //   1. Read the scan region (CSS px) from scanRegionRef (always current)
  //   2. Map it to native video-frame pixel coordinates via cssRegionToVideoRegion
  //   3. drawImage the cropped region onto a hidden canvas
  //   4. Run MultiFormatReader.decodeWithState on that canvas → ZXing only sees
  //      the selected crop, not the full frame
  //
  // The reader is created once per camera session and reused — no re-init on resize.

  useEffect(() => {
    if (!active) {
      onScannerStateChange?.(false);
      return;
    }

    let isDisposed = false;
    let localStream: MediaStream | null = null;
    let loopId: ReturnType<typeof setInterval> | null = null;

    const cleanup = () => {
      if (loopId !== null) { clearInterval(loopId); loopId = null; }
      const v = videoRef.current;
      if (v) { try { v.pause(); } catch { /* ignore */ } v.srcObject = null; }
      releaseStream(localStream);
      localStream = null;
      streamRef.current = null;
    };

    const start = async () => {
      scanLockedRef.current = false;
      setTorchOn(false);
      setZoom(1);
      setCapabilities(DEFAULT_CAPABILITIES);
      setCameraInfo({ width: 0, height: 0 });
      capsRef.current = DEFAULT_CAPABILITIES;
      setInitializing(true);

      // 1. Acquire camera stream
      try {
        localStream = await acquireStream();
      } catch (err) {
        if (!isDisposed) {
          onScannerError?.(`Camera unavailable. ${err instanceof Error ? err.message : 'Permission denied'}`);
          onScannerStateChange?.(false);
          setInitializing(false);
        }
        return;
      }
      if (isDisposed) { releaseStream(localStream); return; }

      streamRef.current = localStream;

      // 2. Capabilities
      const caps = detectCapabilities(localStream);
      setCapabilities(caps);
      capsRef.current = caps;
      if (caps.zoomSupported) setZoom(caps.zoomMin);

      // 3. Autofocus
      const primaryTrack = localStream.getVideoTracks()[0];
      if (primaryTrack) await applyAutofocus(primaryTrack, caps);

      // 4. Confirmed resolution
      setCameraInfo(readCameraInfo(localStream));

      const videoEl = videoRef.current;
      if (!videoEl || isDisposed) { cleanup(); return; }

      // 5. Attach stream to video element manually
      videoEl.srcObject = localStream;
      try {
        await videoEl.play();
      } catch (err) {
        if (!isDisposed) {
          onScannerError?.('Video playback blocked. Check browser autoplay settings.');
          onScannerStateChange?.(false);
          setInitializing(false);
        }
        cleanup();
        return;
      }
      if (isDisposed) { cleanup(); return; }

      // Let autofocus/exposure settle before first decode attempts.
      await new Promise<void>((resolve) => {
        window.setTimeout(resolve, CAMERA_SETTLE_DELAY_MS);
      });
      if (isDisposed) { cleanup(); return; }

      setInitializing(false);
      if (!isDisposed) onScannerStateChange?.(true);

      // 6. Manual decode loop
      //    MultiFormatReader is reused across ticks — no re-init on region change.
      //    decodeWithState respects the hints set via setHints().
      const zxReader = new MultiFormatReader();
      zxReader.setHints(ZXING_HINTS);

      const cropCanvas = document.createElement('canvas');
      const ctx = cropCanvas.getContext('2d', { willReadFrequently: true });
      if (!ctx) { onScannerError?.('Canvas not supported'); return; }

      let consecutiveFailures = 0;

      loopId = setInterval(() => {
        if (isDisposed || scanLockedRef.current) return;
        if (!videoEl.videoWidth || videoEl.readyState < 2) return;

        const rawRegion = scanRegionRef.current;
        const vW = videoEl.videoWidth;
        const vH = videoEl.videoHeight;
        const cW = containerRef.current?.offsetWidth ?? vW;
        const cH = containerRef.current?.offsetHeight ?? vH;
        const region = clampRegionToContainer(rawRegion, cW, cH);

        // Keep state and decode region synchronized if layout changes made the region invalid.
        if (
          region.x !== rawRegion.x
          || region.y !== rawRegion.y
          || region.width !== rawRegion.width
          || region.height !== rawRegion.height
        ) {
          setScanRegion(region);
        }

        const regionCandidates: ScanRegion[] = [region, expandRegion(region, cW, cH, 1.25)];

        // Map CSS overlay coordinates → native video pixel crop
        const decodeCandidate = (candidate: ScanRegion) => (DEBUG_FULL_FRAME_DECODE
          ? { sx: 0, sy: 0, sWidth: vW, sHeight: vH }
          : cssRegionToVideoRegion(
            candidate,
            { width: cW, height: cH },
            { width: vW, height: vH },
          ));

        for (const candidate of regionCandidates) {
          const { sx, sy, sWidth, sHeight } = decodeCandidate(candidate);
          if (sWidth < 1 || sHeight < 1) continue;

          // Upscale cropped output for stronger binarization/edge detection in live decode.
          const dpr = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1;
          const scale = Math.max(2, dpr);
          cropCanvas.width = Math.max(1, Math.round(sWidth * scale));
          cropCanvas.height = Math.max(1, Math.round(sHeight * scale));
          ctx.imageSmoothingEnabled = false;
          ctx.clearRect(0, 0, cropCanvas.width, cropCanvas.height);
          ctx.drawImage(videoEl, sx, sy, sWidth, sHeight, 0, 0, cropCanvas.width, cropCanvas.height);

          // ZXing decode on the cropped canvas only
          try {
            const lum = new HTMLCanvasElementLuminanceSource(cropCanvas);
            const bmp = new BinaryBitmap(new HybridBinarizer(lum));
            const result = zxReader.decodeWithState(bmp);
            const text = result.getText().trim();
            if (!text || scanLockedRef.current) return;
            scanLockedRef.current = true;
            onScan(text);
            consecutiveFailures = 0;
            cleanup();
            if (!isDisposed) onScannerStateChange?.(false);
            return;
          } catch {
            // NotFoundException fires every frame when no QR is visible — expected
          }
        }

        consecutiveFailures += 1;
        if (consecutiveFailures >= READER_RESET_EVERY_FAILURES) {
          zxReader.reset();
          consecutiveFailures = 0;
        }
      }, DECODE_INTERVAL_MS);
    };

    void start();

    return () => {
      isDisposed = true;
      setInitializing(false);
      onScannerStateChange?.(false);
      cleanup();
    };
  }, [active, onScan, onScannerError, onScannerStateChange]);

  // ── Resize observer (container layout changes) ────────────────────────────

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(() => {
      if (videoRef.current) videoRef.current.style.width = '100%';
      const cW = Math.max(1, container.offsetWidth);
      const cH = Math.max(1, container.offsetHeight);
      setScanRegion((prev) => clampRegionToContainer(prev, cW, cH));
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  // ── Derived display values ────────────────────────────────────────────────

  const zoomMin = capabilities.zoomSupported ? capabilities.zoomMin : SW_ZOOM_MIN;
  const zoomMax = capabilities.zoomSupported ? capabilities.zoomMax : SW_ZOOM_MAX;
  const zoomStep = capabilities.zoomSupported ? capabilities.zoomStep : SW_ZOOM_STEP;
  const zoomLabel = capabilities.zoomSupported ? 'Zoom (optical)' : 'Zoom (digital)';
  const videoScale = capabilities.zoomSupported ? 1 : zoom;
  const resolutionLabel = cameraInfo.width > 0 ? `${cameraInfo.width}×${cameraInfo.height}` : null;
  const regionLabel = `${Math.round(scanRegion.width)}×${Math.round(scanRegion.height)} px`;

  return (
    <div className="space-y-2">
      {/* ── Camera viewport ───────────────────────────────────────────────── */}
      <div
        ref={containerRef}
        id={elementId}
        className="relative overflow-hidden rounded-lg border bg-black/95"
        style={{ height: viewportHeight }}
      >
        {/* Initializing spinner */}
        {initializing && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/80">
            <div className="text-center text-white">
              <div className="mx-auto mb-2 h-8 w-8 animate-spin rounded-full border-2 border-white border-t-transparent" />
              <p className="text-xs opacity-75">Starting camera…</p>
            </div>
          </div>
        )}

        {/*
         * Video element — srcObject is set manually in start().
         * CSS digital zoom applied via transform when hardware zoom unavailable.
         * Click/touch triggers focus cycle on supported mobile devices.
         */}
        <video
          ref={videoRef}
          className="block w-full object-cover"
          style={{
            height: '100%',
            transform: `scale(${videoScale})`,
            transformOrigin: 'center',
            transition: 'transform 0.15s ease',
          }}
          muted
          playsInline
          onClick={handleVideoTap}
          onTouchEnd={handleVideoTap}
        />

        {/*
         * Scan region overlay — visible when camera is active and not initializing.
         *
         * Architecture:
         *   • Dark vignette outside the region: box-shadow trick (container clips overflow)
         *   • Blue border marks the active decode region
         *   • Four corner handles (pointer-events-auto) let the user resize the region
         *   • The region in CSS px is translated to native video coords each decode tick
         *     by cssRegionToVideoRegion(), so what you see IS what ZXing decodes
         */}
        {active && !initializing && (
          <div className="pointer-events-none absolute inset-0">
            {/* Status bar */}
            <div className="absolute left-0 right-0 top-0 z-10 flex items-center justify-between px-2 py-1.5">
              <span className="rounded bg-black/55 px-1.5 py-0.5 text-[10px] text-white/75">
                {resolutionLabel ? `${resolutionLabel}` : 'detecting…'} · crop {regionLabel}
              </span>
              {capabilities.continuousFocusSupported && (
                <span className="rounded bg-black/50 px-1.5 py-0.5 text-[10px] text-white/60">
                  AF · tap to refocus
                </span>
              )}
            </div>

            {/*
             * Scan region: border + dark vignette (box-shadow) + draggable corners.
             * The box-shadow spreads 9999px in all directions — the container's
             * overflow:hidden clips it so only the area outside the box is dark.
             */}
            <div
              className="absolute border-2 border-primary"
              style={{
                left: scanRegion.x,
                top: scanRegion.y,
                width: scanRegion.width,
                height: scanRegion.height,
                boxShadow: '0 0 0 9999px rgba(0,0,0,0.50)',
              }}
            />

            {/* Corner handles — pointer-events-auto so they receive drag events */}
            <div
              className="pointer-events-auto absolute"
              style={{
                left: scanRegion.x,
                top: scanRegion.y,
                width: scanRegion.width,
                height: scanRegion.height,
              }}
            >
              {CORNERS.map((corner) => (
                <div
                  key={corner}
                  data-corner={corner}
                  style={cornerHandleStyle(corner)}
                  onPointerDown={handleCornerPointerDown}
                  onPointerMove={handleCornerPointerMove}
                  onPointerUp={handleCornerPointerUp}
                />
              ))}
            </div>
          </div>
        )}

        {/* Viewport resize drag handle */}
        <div
          className="absolute bottom-0 left-0 right-0 z-20 flex h-5 cursor-row-resize select-none items-center justify-center bg-black/40 transition-colors hover:bg-black/60"
          onMouseDown={handleViewportDragStart}
          onTouchStart={handleViewportDragStart}
          title={`Drag to resize · ${viewportHeight}px tall`}
        >
          <GripHorizontal className="h-3 w-3 text-white/50" />
        </div>
      </div>

      {/* ── Camera controls ───────────────────────────────────────────────── */}
      {active && (
        <div className="space-y-2 rounded-lg border bg-card px-3 py-2">
          <ZoomControl
            label={zoomLabel}
            min={zoomMin}
            max={zoomMax}
            step={zoomStep}
            value={zoom}
            onChange={applyZoom}
          />

          <div className="flex flex-wrap items-center gap-2">
            {capabilities.torchSupported && (
              <Button type="button" variant={torchOn ? 'default' : 'outline'} size="sm" onClick={toggleTorch}>
                {torchOn
                  ? <><Zap className="mr-2 h-4 w-4" />Torch on</>
                  : <><ZapOff className="mr-2 h-4 w-4" />Torch off</>}
              </Button>
            )}
            <Button type="button" variant="outline" size="sm" onClick={resetScanRegion}>
              <RefreshCcw className="mr-2 h-4 w-4" />
              Reset region
            </Button>
            <span className="text-xs text-muted-foreground">
              Drag corner handles to resize the scan area
            </span>
          </div>
        </div>
      )}

      {/* ── Upload / paste ────────────────────────────────────────────────── */}
      <div className="space-y-2 rounded-lg border bg-card px-3 py-2">
        <p className="text-xs text-muted-foreground">
          Or scan from a saved QR image — upload a file or paste from clipboard (Ctrl+V)
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleUpload} />
          <Button type="button" variant="outline" size="sm" onClick={openFilePicker} disabled={uploadScanning}>
            {uploadScanning
              ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              : <Upload className="mr-2 h-4 w-4" />}
            {uploadScanning ? 'Scanning…' : 'Upload Image'}
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={handlePasteButton} disabled={uploadScanning}>
            <ClipboardPaste className="mr-2 h-4 w-4" />
            Paste Image
          </Button>
        </div>
        {uploadError && <p className="text-xs text-destructive">{uploadError}</p>}
      </div>
    </div>
  );
};

export default DispatchQrScanner;
