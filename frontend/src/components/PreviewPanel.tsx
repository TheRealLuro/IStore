import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { X, Download, Share2, Trash2, ChevronUp, ChevronDown, ChevronRight } from "lucide-react";
import type { FileItem } from "@/types/file";
import { useUIStore } from "@/stores/uiStore";
import { useSelectionStore } from "@/stores/selectionStore";
import { useFilterStore } from "@/stores/filterStore";
import { ThumbnailRenderer } from "./ThumbnailRenderer";
import { TypeBadge } from "./TypeBadge";
import { Highlight } from "./Highlight";
import { PreviewPeopleStrip } from "./PreviewPeopleStrip";
import { RatingModal, shouldOfferRating } from "./RatingModal";
import { StatusPicker } from "./StatusPicker";
import { formatBytes, relativeTime } from "@/utils/format";
import { originalUrl, servedUrl, fetchAsBlobUrl, fetchMediaBlob } from "@/api/files";
import { setImageStatus } from "@/api/folders";
import { getConsentStatus } from "@/api/consent";
import { parseExifRows, type ExifRow } from "@/utils/exif";
import { tokens } from "@/api/client";
import toast from "react-hot-toast";

interface Props {
  files: FileItem[];
  onRequestBulkDelete: () => void;
}

export function PreviewPanel({ files, onRequestBulkDelete }: Props) {
  const previewId = useUIStore((s) => s.previewId);
  const setPreview = useUIStore((s) => s.setPreview);
  const selected = useSelectionStore((s) => s.selected);
  const clearSelection = useSelectionStore((s) => s.clear);
  const query = useFilterStore((s) => s.query);

  const file = useMemo(() => files.find((f) => f.id === previewId) || null, [files, previewId]);
  const selectedFiles = useMemo(
    () => files.filter((f) => selected.has(f.id)),
    [files, selected],
  );

  const isBulk = selectedFiles.length > 1;
  const open = previewId !== null || isBulk;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (isBulk) clearSelection();
        else setPreview(null);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, isBulk, clearSelection, setPreview]);

  if (!open) return null;

  return (
    <aside className="fixed top-4 right-4 bottom-4 w-full max-w-md bg-card rounded-[28px] border border-divider/80 z-20 animate-slide-in flex flex-col shadow-float overflow-hidden">
      <header className="flex items-center justify-between px-5 h-12 border-b border-divider/40">
        <div className="text-sm font-medium text-fg">
          {isBulk ? `${selectedFiles.length} selected` : "Preview"}
        </div>
        <button
          onClick={() => (isBulk ? clearSelection() : setPreview(null))}
          aria-label="Close preview"
          className="btn-icon"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      {isBulk ? (
        <BulkPanel files={selectedFiles} onRequestDelete={onRequestBulkDelete} />
      ) : (
        file && <SinglePanel file={file} query={query} />
      )}
    </aside>
  );
}

function SinglePanel({ file: baseFile, query }: { file: FileItem; query: string }) {
  const setPreview = useUIStore((s) => s.setPreview);
  const [downloading, setDownloading] = useState(false);
  const [ratingFor, setRatingFor] = useState<FileItem | null>(null);

  // Phase 4: surface detected people above the metadata box.
  const { data: consent } = useQuery({
    queryKey: ["consent", "face_recognition"],
    queryFn: getConsentStatus,
    staleTime: 60_000,
    enabled: baseFile.category === "image",
  });
  const consentActive = consent?.state === "GRANTED";

  // Phase 11: poll the single-image endpoint while the AI Vision summary is
  // still being generated. Mirrors PreviewPeopleStrip's pattern for /people.
  // Once `pending_summary` flips false the query disables itself.
  const { data: pollFile } = useQuery<FileItem>({
    queryKey: ["image", baseFile.id, "summary"],
    queryFn: async () => {
      const token = tokens.get();
      const r = await fetch(`/api/images/${baseFile.id}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!r.ok) throw new Error(`fetch ${r.status}`);
      return r.json();
    },
    enabled: baseFile.pending_summary,
    refetchInterval: baseFile.pending_summary ? 4000 : false,
  });
  const file: FileItem = pollFile ?? baseFile;

  const onDownload = async () => {
    if (downloading) return;
    setDownloading(true);
    try {
      const url = await fetchAsBlobUrl(originalUrl(file.id));
      const a = document.createElement("a");
      a.href = url;
      a.download = file.original_filename || file.id;
      a.click();
      URL.revokeObjectURL(url);
      toast.success("Downloaded original");
      // Phase 6: prompt for a rating on bandit-encoded photos. Hard-rule
      // images (screenshots forced lossless) have no arm to attribute reward
      // to, so skip them. Per-session dismissal honored.
      if (
        file.category === "image" &&
        file.bandit_arm_id !== null &&
        file.bandit_arm_id !== undefined &&
        shouldOfferRating()
      ) {
        setRatingFor(file);
      }
    } catch (e) {
      toast.error("Download failed");
      console.error(e);
    } finally {
      setDownloading(false);
    }
  };

  const [matchIndex, setMatchIndex] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Pull EXIF for image files only.
  // null = not loaded yet, [] = loaded, no metadata, [...] = loaded with rows.
  const [exif, setExif] = useState<ExifRow[] | null>(null);
  const [exifOpen, setExifOpen] = useState(true);
  useEffect(() => {
    if (file.category !== "image") {
      setExif(null);
      return;
    }
    setExif(null);
    let cancelled = false;
    // Always prefer the byte-perfect original (within the 30-day retention
    // window). Only fall back to the served variant if the original has
    // already been swept — WebP/AVIF re-encodes can drop EXIF on some inputs.
    const fetchExifSource = async (): Promise<Blob> => {
      try {
        return await fetchMediaBlob(originalUrl(file.id));
      } catch {
        return await fetchMediaBlob(servedUrl(file.id));
      }
    };

    fetchExifSource()
      .then((b) => parseExifRows(b))
      .then((rows) => {
        if (!cancelled) setExif(rows);
      })
      .catch((e) => {
        if (!cancelled) {
          console.warn("exif read failed", e);
          setExif([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [file.id, file.category]);

  const matches = useMemo(() => {
    if (!bodyRef.current || !query) return 0;
    return bodyRef.current.querySelectorAll("[data-highlight]").length;
  }, [query]);

  const navigateMatch = (delta: number) => {
    if (!bodyRef.current || matches === 0) return;
    const next = (matchIndex + delta + matches) % matches;
    setMatchIndex(next);
    const els = bodyRef.current.querySelectorAll("[data-highlight]");
    const el = els[next] as HTMLElement | undefined;
    if (el) {
      els.forEach((m, i) =>
        (m as HTMLElement).classList.toggle("ring-2", i === next),
      );
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  };

  return (
    <>
      <ThumbnailRenderer file={file} large className="aspect-square w-full max-h-[40vh] bg-elevated" />
      <div ref={bodyRef} className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
        <div>
          <div className="flex items-start justify-between gap-3">
            <h2 className="text-xl font-semibold tracking-tight text-fg leading-snug">
              <Highlight text={file.original_filename || "(unnamed)"} query={query} />
            </h2>
            <TypeBadge file={file} query={query} />
          </div>
          {query && matches > 1 && (
            <div className="flex items-center gap-2 mt-2 text-xs text-fg-secondary">
              <span>
                {matchIndex + 1} / {matches} matches
              </span>
              <div className="flex gap-1 ml-auto">
                <button
                  onClick={() => navigateMatch(-1)}
                  className="p-1 rounded hover:bg-hover"
                  aria-label="Previous match"
                >
                  <ChevronUp className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => navigateMatch(1)}
                  className="p-1 rounded hover:bg-hover"
                  aria-label="Next match"
                >
                  <ChevronDown className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          )}
          <div className="text-[12px] text-fg-secondary mt-2">
            {formatBytes(file.byte_size_served)} · {relativeTime(file.uploaded_at)}
          </div>
        </div>

        {file.category === "image" && (
          <PreviewPeopleStrip
            imageId={file.id}
            pendingScan={file.pending_face_scan}
            consentActive={!!consentActive}
          />
        )}

        <StatusRow file={file} />

        <AIVisionSection file={file} query={query} />

        <Section title="Storage">
          <Row label="Original size" value={formatBytes(file.byte_size_original)} />
          <Row label="Served size" value={formatBytes(file.byte_size_served)} />
          <Row
            label="Codec"
            value={
              file.codec
                ? `${file.codec}${file.lossless ? " (lossless)" : file.quality ? ` q=${file.quality}` : ""}`
                : "—"
            }
          />
          {file.width && file.height && (
            <Row label="Dimensions" value={`${file.width}×${file.height}`} />
          )}
        </Section>

        {(file.content_type || file.scene_label) && (
          <Section title="Classification">
            {file.content_type && (
              <Row
                label="Content type"
                value={
                  <Highlight
                    text={`${file.content_type} (${((file.content_confidence || 0) * 100).toFixed(0)}%)`}
                    query={query}
                  />
                }
              />
            )}
            {file.scene_label && (
              <Row
                label="Scene"
                value={
                  <Highlight
                    text={`${file.scene_label} (${((file.scene_confidence || 0) * 100).toFixed(0)}%)`}
                    query={query}
                  />
                }
              />
            )}
            {file.indoor_outdoor && <Row label="Setting" value={file.indoor_outdoor} />}
            {file.face_likelihood !== null && (
              <Row
                label="Face likelihood"
                value={`${(file.face_likelihood * 100).toFixed(0)}%`}
              />
            )}
          </Section>
        )}

        {file.category === "image" && (
          <Collapsible
            title="Metadata"
            count={exif?.length ?? 0}
            open={exifOpen}
            onToggle={() => setExifOpen(!exifOpen)}
          >
            {exif === null ? (
              <div className="flex items-center gap-2 text-sm text-fg-secondary py-1">
                <span className="h-3.5 w-3.5 rounded-full border-2 border-current border-t-transparent animate-spin" />
                Reading metadata…
              </div>
            ) : exif.length === 0 ? (
              <div className="text-sm text-fg-secondary py-1">
                No camera or EXIF metadata in this file.
                <div className="text-xs text-fg-muted mt-1">
                  Common for screenshots, AI-generated graphics, or anything stripped of metadata.
                </div>
              </div>
            ) : (
              exif.map((row) => (
                <Row
                  key={row.label}
                  label={row.label}
                  value={<Highlight text={row.value} query={query} />}
                />
              ))
            )}
          </Collapsible>
        )}
      </div>

      <footer className="border-t border-divider px-5 py-4 flex items-center gap-2">
        <button
          onClick={onDownload}
          disabled={downloading}
          className="flex-1 flex items-center justify-center gap-2 h-11 rounded-full bg-fg text-fg-inverse text-[14px] font-medium shadow-card hover:shadow-float hover:-translate-y-0.5 transition-all disabled:opacity-60 disabled:cursor-wait"
        >
          {downloading ? (
            <>
              <span className="h-4 w-4 rounded-full border-2 border-current border-t-transparent animate-spin" />
              Preparing…
            </>
          ) : (
            <>
              <Download className="h-4 w-4" strokeWidth={2.25} />
              Download original
            </>
          )}
        </button>
        <button
          onClick={() => {
            navigator.clipboard.writeText(`${window.location.origin}/file/${file.id}`);
            toast.success("Link copied");
          }}
          className="h-11 w-11 rounded-full bg-elevated hover:bg-hover transition flex items-center justify-center text-fg-secondary hover:text-fg"
          aria-label="Share"
          title="Copy link"
        >
          <Share2 className="h-4 w-4" />
        </button>
        <button
          onClick={() => {
            useSelectionStore.getState().setMany([file.id]);
            useSelectionStore.getState().setMultiSelectMode(true);
            useUIStore.getState().setConfirmDelete(true);
            setPreview(null);
          }}
          className="h-11 w-11 rounded-full bg-danger/10 text-danger hover:bg-danger/20 transition flex items-center justify-center"
          aria-label="Delete"
          title="Delete"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </footer>

      <RatingModal file={ratingFor} onClose={() => setRatingFor(null)} />
    </>
  );
}

function BulkPanel({
  files,
  onRequestDelete,
}: {
  files: FileItem[];
  onRequestDelete: () => void;
}) {
  const totalBytes = files.reduce((sum, f) => sum + (f.byte_size_served ?? 0), 0);
  const byCategory = files.reduce<Record<string, number>>((acc, f) => {
    acc[f.category] = (acc[f.category] ?? 0) + 1;
    return acc;
  }, {});

  const onBulkDownload = () => {
    toast(
      `${files.length} file${files.length === 1 ? "" : "s"} queued for download`,
      { icon: "⬇" },
    );
    files.forEach((f) => {
      fetchAsBlobUrl(originalUrl(f.id)).then((url) => {
        const a = document.createElement("a");
        a.href = url;
        a.download = f.original_filename || f.id;
        a.click();
        URL.revokeObjectURL(url);
      });
    });
  };

  return (
    <>
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
        <div className="rounded-2xl bg-elevated p-5">
          <div className="text-3xl font-semibold tracking-tight text-fg">
            {files.length} files
          </div>
          <div className="text-sm text-fg-secondary mt-1">{formatBytes(totalBytes)}</div>
          <div className="flex gap-1.5 mt-3 flex-wrap">
            {Object.entries(byCategory).map(([cat, count]) => (
              <span
                key={cat}
                className="px-2.5 py-0.5 text-[11px] rounded-full bg-card text-fg-secondary capitalize"
              >
                {count} {cat}
                {count === 1 ? "" : "s"}
              </span>
            ))}
          </div>
        </div>

        <ul className="space-y-1.5">
          {files.slice(0, 12).map((f) => (
            <li
              key={f.id}
              className="flex items-center gap-2 px-3 py-2 rounded-xl bg-elevated text-sm"
            >
              <TypeBadge file={f} />
              <span className="truncate flex-1 text-fg">{f.original_filename}</span>
              <span className="text-xs text-fg-secondary">{formatBytes(f.byte_size_served)}</span>
            </li>
          ))}
          {files.length > 12 && (
            <li className="px-3 py-1.5 text-xs text-fg-muted text-center">
              + {files.length - 12} more
            </li>
          )}
        </ul>
      </div>

      <footer className="border-t border-divider px-5 py-3 flex gap-2">
        <button onClick={onBulkDownload} className="btn-primary flex-1">
          <Download className="h-4 w-4" /> Download
        </button>
        <button
          onClick={onRequestDelete}
          className="btn h-10 px-4 bg-danger/10 text-danger hover:bg-danger/20"
        >
          <Trash2 className="h-4 w-4" /> Delete
        </button>
      </footer>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.18em] text-fg-muted mb-2">
        {title}
      </div>
      <div className="space-y-2 rounded-2xl bg-elevated p-4">{children}</div>
    </div>
  );
}

function Collapsible({
  title,
  count,
  open,
  onToggle,
  children,
}: {
  title: string;
  count?: number;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div>
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-fg-muted mb-2 hover:text-fg-secondary transition"
        aria-expanded={open}
      >
        <ChevronRight
          className={`h-3 w-3 transition-transform ${open ? "rotate-90" : ""}`}
          strokeWidth={2.5}
        />
        <span>{title}</span>
        {typeof count === "number" && (
          <span className="text-fg-muted normal-case tracking-normal">
            ({count})
          </span>
        )}
      </button>
      {open && (
        <div className="space-y-2 rounded-2xl bg-elevated p-4 animate-fade-in">
          {children}
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 text-sm">
      <span className="text-fg-secondary">{label}</span>
      <span className="text-fg truncate">{value}</span>
    </div>
  );
}

/** Inline status editor as a one-line section (no big "Status" header —
 * absent status reads as "tap to add", present status reads as the chip). */
function StatusRow({ file }: { file: FileItem }) {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: (body: { status: string | null; color: string | null }) =>
      setImageStatus(file.id, body.status, body.color),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["image", file.id] });
      toast.success("Status updated");
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Could not update status"),
  });

  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.18em] text-fg-muted mb-2">
        Status
      </div>
      <StatusPicker
        value={file.status}
        color={file.status_color}
        onChange={(label, color) => m.mutate({ status: label, color })}
        busy={m.isPending}
      />
    </div>
  );
}

function AIVisionSection({ file, query }: { file: FileItem; query: string }) {
  // Loading state mirrors PreviewPeopleStrip — show a skeleton while the
  // BackgroundTask is still running so the section doesn't flicker in/out.
  if (file.pending_summary) {
    return (
      <Section title="AI Vision">
        <div className="flex items-center gap-2 text-sm text-fg-secondary py-1">
          <span className="h-3.5 w-3.5 rounded-full border-2 border-current border-t-transparent animate-spin" />
          Generating summary…
        </div>
        <div className="text-xs text-fg-muted">
          A short topic, summary, and key points appear here once the
          model finishes. Searching matches against this text too.
        </div>
      </Section>
    );
  }

  if (!file.summary && !file.summary_topic) {
    return null;
  }

  return (
    <Section title="AI Vision">
      {file.summary_topic && (
        <div className="text-sm font-semibold text-fg leading-snug">
          <Highlight text={file.summary_topic} query={query} />
        </div>
      )}
      {file.summary && (
        <div className="text-sm text-fg-secondary leading-relaxed whitespace-pre-line">
          <Highlight text={file.summary} query={query} />
        </div>
      )}
      {file.summary_points && file.summary_points.length > 0 && (
        <ul className="list-disc list-inside space-y-1 text-sm text-fg-secondary marker:text-fg-muted">
          {file.summary_points.map((p, i) => (
            <li key={i}>
              <Highlight text={p} query={query} />
            </li>
          ))}
        </ul>
      )}
      {file.summary_generated_at && (
        <div className="text-[11px] text-fg-muted pt-1">
          Generated {relativeTime(file.summary_generated_at)}
        </div>
      )}
    </Section>
  );
}
