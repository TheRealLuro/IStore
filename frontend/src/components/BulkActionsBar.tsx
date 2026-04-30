import { Trash2, X, Download, Share2 } from "lucide-react";
import toast from "react-hot-toast";
import { useSelectionStore } from "@/stores/selectionStore";
import { useUIStore } from "@/stores/uiStore";
import type { FileItem } from "@/types/file";
import { fetchAsBlobUrl, originalUrl } from "@/api/files";

interface Props {
  files: FileItem[];
}

export function BulkActionsBar({ files }: Props) {
  const selected = useSelectionStore((s) => s.selected);
  const clear = useSelectionStore((s) => s.clear);
  const setConfirmDelete = useUIStore((s) => s.setConfirmDelete);
  const deleting = useUIStore((s) => s.deleting);

  if (selected.size === 0) return null;
  const sel = files.filter((f) => selected.has(f.id));

  const onDownload = () => {
    toast(`${sel.length} file${sel.length === 1 ? "" : "s"} queued`, { icon: "⬇" });
    sel.forEach((f) => {
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
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-card border border-border rounded-full shadow-float px-2 py-1.5 flex items-center gap-1 z-30 animate-fade-in">
      <span className="text-[13px] font-medium text-fg px-3 tabular-nums">
        {selected.size} selected
      </span>
      <div className="h-6 w-px bg-divider" />
      <button
        onClick={onDownload}
        disabled={deleting}
        className="flex items-center gap-1.5 px-3 h-8 rounded-full hover:bg-hover text-[13px] text-fg transition disabled:opacity-50"
      >
        <Download className="h-3.5 w-3.5" /> Download
      </button>
      <button
        onClick={() => {
          navigator.clipboard.writeText(
            sel.map((f) => `${window.location.origin}/file/${f.id}`).join("\n"),
          );
          toast.success("Links copied");
        }}
        disabled={deleting}
        className="flex items-center gap-1.5 px-3 h-8 rounded-full hover:bg-hover text-[13px] text-fg transition disabled:opacity-50"
      >
        <Share2 className="h-3.5 w-3.5" /> Share
      </button>
      <button
        onClick={() => setConfirmDelete(true)}
        disabled={deleting}
        className="flex items-center gap-1.5 px-3 h-8 rounded-full hover:bg-danger/10 text-danger text-[13px] transition disabled:opacity-50"
      >
        <Trash2 className="h-3.5 w-3.5" /> Delete
      </button>
      <div className="h-6 w-px bg-divider" />
      <button
        onClick={clear}
        disabled={deleting}
        aria-label="Clear selection"
        className="h-8 w-8 rounded-full hover:bg-hover text-fg-secondary disabled:opacity-50 flex items-center justify-center"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
