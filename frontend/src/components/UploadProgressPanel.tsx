import { useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Check,
  X,
  AlertCircle,
  Loader2,
  CircleSlash,
} from "lucide-react";
import { useUploadStore, type UploadItem } from "@/stores/uploadStore";
import { formatBytes } from "@/utils/format";

function formatEta(seconds: number): string {
  if (!isFinite(seconds) || seconds <= 0) return "calculating…";
  if (seconds < 1) return "<1s left";
  if (seconds < 60) return `${Math.ceil(seconds)}s left`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)}m left`;
  return `${Math.ceil(seconds / 3600)}h left`;
}

function formatSpeed(bps: number): string {
  if (!isFinite(bps) || bps <= 0) return "";
  if (bps < 1024) return `${bps.toFixed(0)} B/s`;
  if (bps < 1024 * 1024) return `${(bps / 1024).toFixed(0)} KB/s`;
  if (bps < 1024 * 1024 * 1024) return `${(bps / 1024 / 1024).toFixed(1)} MB/s`;
  return `${(bps / 1024 / 1024 / 1024).toFixed(2)} GB/s`;
}

function rateAndEta(item: UploadItem, now: number) {
  const elapsed = (now - item.startedAt) / 1000;
  const percent = item.size > 0 ? (item.uploaded / item.size) * 100 : 0;
  // Bytes fully transferred but waiting for server response (CLIP, encode, DB).
  if (item.size > 0 && item.uploaded >= item.size) {
    return { eta: "Processing…", percent: 100, speed: "" };
  }
  if (elapsed < 0.3 || item.uploaded === 0) {
    return { eta: "calculating…", percent, speed: "" };
  }
  const bps = item.uploaded / elapsed;
  const remaining = item.size - item.uploaded;
  return {
    eta: formatEta(remaining / bps),
    percent,
    speed: formatSpeed(bps),
  };
}

export function UploadProgressPanel() {
  const items = useUploadStore((s) => s.items);
  const collapsed = useUploadStore((s) => s.collapsed);
  const cancel = useUploadStore((s) => s.cancel);
  const clearDone = useUploadStore((s) => s.clearDone);
  const setCollapsed = useUploadStore((s) => s.setCollapsed);

  // 250ms tick so progress + ETA refresh smoothly even when no events fire.
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (items.every((i) => i.state !== "uploading")) return;
    const t = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(t);
  }, [items]);

  const counts = useMemo(() => {
    let uploading = 0;
    let done = 0;
    let failed = 0;
    let cancelled = 0;
    for (const i of items) {
      if (i.state === "uploading") uploading++;
      else if (i.state === "done") done++;
      else if (i.state === "error") failed++;
      else if (i.state === "cancelled") cancelled++;
    }
    return { uploading, done, failed, cancelled };
  }, [items]);

  if (items.length === 0) return null;

  const headerText =
    counts.uploading > 0
      ? `Uploading ${counts.uploading} item${counts.uploading === 1 ? "" : "s"}`
      : counts.failed > 0
        ? `${counts.done} uploaded · ${counts.failed} failed`
        : `${counts.done} upload${counts.done === 1 ? "" : "s"} complete`;

  return (
    <div
      className="fixed bottom-6 right-6 w-96 max-w-[92vw] bg-card border border-border rounded-2xl shadow-float overflow-hidden z-30 animate-fade-in"
      role="status"
      aria-live="polite"
    >
      <header className="flex items-center justify-between px-4 h-12 border-b border-divider">
        <div className="flex items-center gap-2">
          {counts.uploading > 0 ? (
            <Loader2 className="h-4 w-4 animate-spin text-accent" />
          ) : counts.failed > 0 ? (
            <AlertCircle className="h-4 w-4 text-danger" />
          ) : (
            <Check className="h-4 w-4 text-success" />
          )}
          <span className="text-[13px] font-medium text-fg">{headerText}</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="h-8 w-8 rounded-full hover:bg-hover text-fg-secondary flex items-center justify-center transition"
            aria-label={collapsed ? "Expand" : "Collapse"}
          >
            {collapsed ? (
              <ChevronUp className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </button>
          {counts.uploading === 0 && (
            <button
              onClick={clearDone}
              className="h-8 w-8 rounded-full hover:bg-hover text-fg-secondary flex items-center justify-center transition"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </header>

      {!collapsed && (
        <ul className="max-h-72 overflow-y-auto">
          {items.map((item) => (
            <Row key={item.id} item={item} now={now} onCancel={cancel} />
          ))}
        </ul>
      )}
    </div>
  );
}

function Row({
  item,
  now,
  onCancel,
}: {
  item: UploadItem;
  now: number;
  onCancel: (id: string) => void;
}) {
  const { eta, percent, speed } = rateAndEta(item, now);

  return (
    <li className="px-4 py-3 border-b border-divider last:border-0">
      {/* Top line: name + percentage + status icon/cancel */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <span className="text-[13px] font-medium text-fg truncate flex-1">
          {item.name}
        </span>
        {item.state === "uploading" && (
          <span className="text-[12px] font-semibold tabular-nums text-fg shrink-0">
            {percent.toFixed(0)}%
          </span>
        )}
        {item.state === "done" ? (
          <span className="h-5 w-5 rounded-full bg-success/15 text-success flex items-center justify-center shrink-0">
            <Check className="h-3 w-3" strokeWidth={3} />
          </span>
        ) : item.state === "error" ? (
          <span className="h-5 w-5 rounded-full bg-danger/15 text-danger flex items-center justify-center shrink-0">
            <AlertCircle className="h-3 w-3" />
          </span>
        ) : item.state === "cancelled" ? (
          <span className="h-5 w-5 rounded-full bg-elevated text-fg-muted flex items-center justify-center shrink-0">
            <CircleSlash className="h-3 w-3" />
          </span>
        ) : (
          <button
            onClick={() => onCancel(item.id)}
            aria-label="Cancel"
            className="h-5 w-5 rounded-full text-fg-muted hover:text-fg hover:bg-hover flex items-center justify-center shrink-0"
          >
            <X className="h-3 w-3" strokeWidth={2.5} />
          </button>
        )}
      </div>

      {item.state === "uploading" && (
        <>
          {/* Thicker bar with smoother fill */}
          <div className="h-1.5 bg-elevated rounded-full overflow-hidden">
            <div
              className="h-full bg-accent rounded-full transition-[width] duration-200 ease-out"
              style={{ width: `${percent}%` }}
            />
          </div>
          {/* Bottom line: bytes · speed · eta */}
          <div className="flex justify-between items-center text-[11px] text-fg-secondary mt-2 tabular-nums">
            <span>
              {formatBytes(item.uploaded)}{" "}
              <span className="text-fg-muted">/ {formatBytes(item.size)}</span>
            </span>
            <span className="flex items-center gap-1.5">
              {speed && (
                <>
                  <span className="text-fg">{speed}</span>
                  <span className="text-fg-muted">·</span>
                </>
              )}
              <span>{eta}</span>
            </span>
          </div>
        </>
      )}
      {item.state === "done" && (
        <div className="text-[11px] text-fg-secondary tabular-nums">
          {formatBytes(item.size)} · uploaded
        </div>
      )}
      {item.state === "error" && (
        <div className="text-[11px] text-danger truncate">
          {item.error || "Upload failed"}
        </div>
      )}
      {item.state === "cancelled" && (
        <div className="text-[11px] text-fg-muted">Cancelled</div>
      )}
    </li>
  );
}
