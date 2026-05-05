import clsx from "clsx";
import { Check, Loader2 } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import type { FileItem } from "@/types/file";
import { TypeBadge } from "./TypeBadge";
import { ThumbnailRenderer } from "./ThumbnailRenderer";
import { Highlight } from "./Highlight";
import { StatusChip } from "./FolderCard";
import { formatBytes, relativeTime } from "@/utils/format";
import { useSelectionStore } from "@/stores/selectionStore";
import { useUIStore } from "@/stores/uiStore";
import { getConsentStatus } from "@/api/consent";

interface Props {
  file: FileItem;
  query: string;
}

export function FileCard({ file, query }: Props) {
  const isSelected = useSelectionStore((s) => s.selected.has(file.id));
  const toggle = useSelectionStore((s) => s.toggle);
  const multiSelectMode = useSelectionStore((s) => s.multiSelectMode);
  const setMultiSelectMode = useSelectionStore((s) => s.setMultiSelectMode);
  const setPreview = useUIStore((s) => s.setPreview);

  // Surface "Pass B running" only when consent is granted — otherwise the
  // pending flag is meaningless to the user. React Query dedupes this; the
  // network only sees it once per gallery render.
  const { data: consent } = useQuery({
    queryKey: ["consent", "face_recognition"],
    queryFn: getConsentStatus,
    staleTime: 60_000,
    enabled: file.category === "image" && file.pending_face_scan,
  });
  const showScanning =
    file.category === "image" &&
    file.pending_face_scan &&
    consent?.state === "GRANTED";

  const onClick = () => {
    if (multiSelectMode) toggle(file.id);
    else setPreview(file.id);
  };

  const onCheckboxClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!multiSelectMode) setMultiSelectMode(true);
    toggle(file.id);
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      draggable
      onDragStart={(e) => {
        // Custom MIME type so only IStore folder cards accept the drop
        // (other DnD targets in the page won't match the type).
        e.dataTransfer.setData("application/x-istore-image", file.id);
        e.dataTransfer.effectAllowed = "move";
      }}
      className={clsx(
        "group relative rounded-3xl bg-card overflow-hidden transition-all duration-300 cursor-pointer",
        "shadow-soft hover:shadow-card hover:-translate-y-0.5",
        isSelected && "ring-2 ring-accent shadow-card",
      )}
    >
      <ThumbnailRenderer file={file} className="aspect-[4/3] w-full" />

      <button
        onClick={onCheckboxClick}
        aria-label={isSelected ? "Deselect" : "Select"}
        className={clsx(
          "absolute top-2.5 left-2.5 h-6 w-6 rounded-full flex items-center justify-center transition-all",
          isSelected
            ? "bg-accent text-white opacity-100 shadow-card"
            : "bg-white/90 dark:bg-black/40 backdrop-blur ring-1 ring-black/10 dark:ring-white/20 text-transparent opacity-0 group-hover:opacity-100",
          multiSelectMode && "opacity-100",
        )}
      >
        <Check className="h-3.5 w-3.5" strokeWidth={3} />
      </button>

      <div className="absolute top-2.5 right-2.5">
        <TypeBadge file={file} query={query} />
      </div>

      {showScanning && (
        <div className="absolute bottom-2.5 left-2.5 flex items-center gap-1.5 rounded-full bg-black/55 backdrop-blur px-2.5 py-1 text-[10px] font-medium text-white">
          <Loader2 className="h-3 w-3 animate-spin" />
          Scanning
        </div>
      )}

      {file.status && !showScanning && (
        <div className="absolute bottom-2.5 left-2.5">
          <StatusChip label={file.status} color={file.status_color} />
        </div>
      )}

      <div className="px-4 py-3">
        <div className="text-[14px] font-medium text-fg truncate">
          <Highlight
            text={file.original_filename || "(unnamed)"}
            query={query}
          />
        </div>
        <div className="text-[12px] text-fg-secondary mt-0.5 flex items-center gap-1.5">
          <span>{formatBytes(file.byte_size_served)}</span>
          <span className="text-fg-muted">·</span>
          <span>{relativeTime(file.uploaded_at)}</span>
        </div>
      </div>
    </div>
  );
}
