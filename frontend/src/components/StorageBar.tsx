import { useQuery } from "@tanstack/react-query";
import { getStorageUsage } from "@/api/storage";
import { formatBytes } from "@/utils/format";

const CATEGORY_META: Record<
  string,
  { bar: string; dot: string; label: string }
> = {
  image: {
    bar: "bg-cyan-500",
    dot: "bg-cyan-500",
    label: "Images",
  },
  video: {
    bar: "bg-purple-500",
    dot: "bg-purple-500",
    label: "Videos",
  },
  document: {
    bar: "bg-blue-500",
    dot: "bg-blue-500",
    label: "Documents",
  },
  other: {
    bar: "bg-zinc-400",
    dot: "bg-zinc-400",
    label: "Other",
  },
};

const CATEGORY_ORDER = ["image", "video", "document", "other"];

export function StorageBar() {
  const { data } = useQuery({
    queryKey: ["storage-usage"],
    queryFn: getStorageUsage,
    refetchInterval: 30_000,
  });

  const used = data?.used_bytes ?? 0;
  const quota = data?.quota_bytes ?? 100 * 1024 * 1024 * 1024;
  const pct = quota > 0 ? Math.min(100, (used / quota) * 100) : 0;
  const byCategory = data?.by_category ?? {};

  // Adaptive precision: small fractions still show useful detail.
  const formatPct = (p: number): string => {
    if (p === 0) return "0";
    if (p >= 10) return p.toFixed(0);
    if (p >= 1) return p.toFixed(1);
    if (p >= 0.01) return p.toFixed(2);
    if (p >= 0.001) return p.toFixed(3);
    return "<0.001";
  };

  const segments = CATEGORY_ORDER.filter(
    (cat) => (byCategory[cat] ?? 0) > 0,
  ).map((cat) => ({
    cat,
    bytes: byCategory[cat]!,
    pct: quota > 0 ? (byCategory[cat]! / quota) * 100 : 0,
  }));

  return (
    <div className="px-6 pt-5 pb-4 border-b border-divider bg-card/40">
      <div className="flex items-baseline justify-between mb-3">
        <div className="flex items-baseline gap-2">
          <span className="text-[10px] uppercase tracking-[0.18em] font-medium text-fg-muted">
            Storage
          </span>
          <span className="text-[11px] text-fg-secondary tabular-nums">
            · {formatPct(pct)}% used
          </span>
        </div>
        <div className="text-[12px] tabular-nums">
          <span className="font-semibold text-fg">{formatBytes(used)}</span>
          <span className="text-fg-muted"> of {formatBytes(quota)}</span>
        </div>
      </div>

      <div className="h-2 bg-elevated rounded-full overflow-hidden flex gap-px">
        {segments.length === 0 ? (
          <div className="h-full w-full" />
        ) : (
          segments.map(({ cat, pct: segPct }) => (
            <div
              key={cat}
              className={`h-full ${CATEGORY_META[cat].bar} transition-[width] duration-700 ease-out`}
              style={{ width: `${segPct}%` }}
              title={`${CATEGORY_META[cat].label}: ${formatBytes(byCategory[cat])}`}
            />
          ))
        )}
      </div>

      {segments.length > 0 && (
        <div className="flex flex-wrap gap-x-5 gap-y-1.5 mt-3">
          {segments.map(({ cat, bytes }) => (
            <div key={cat} className="flex items-center gap-1.5 text-[11px]">
              <span
                className={`h-2 w-2 rounded-full ${CATEGORY_META[cat].dot}`}
              />
              <span className="text-fg-secondary">
                {CATEGORY_META[cat].label}
              </span>
              <span className="text-fg font-medium tabular-nums">
                {formatBytes(bytes)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
