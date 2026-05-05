import { useEffect, useState } from "react";
import { FileText, FileSpreadsheet, FileType2, Image as ImageIcon, Loader2 } from "lucide-react";
import { servedUrl, fetchAsBlobUrl, fetchMediaBlob } from "@/api/files";
import {
  previewerFor,
  getCachedPreview,
  setCachedPreview,
  type DocPreview,
} from "@/utils/docRender";
import type { FileItem } from "@/types/file";

interface Props {
  file: FileItem;
  className?: string;
  /** Preview-panel mode renders at higher fidelity and shows full HTML/table content. */
  large?: boolean;
}

export function ThumbnailRenderer({ file, className, large = false }: Props) {
  const [imgSrc, setImgSrc] = useState<string | null>(null);
  const [imgError, setImgError] = useState(false);
  const [docPreview, setDocPreview] = useState<DocPreview | null>(
    () => getCachedPreview(file.id) ?? null,
  );
  const [docLoading, setDocLoading] = useState(false);

  // The lazy initializer above only fires on the very first render. When the
  // PreviewPanel re-uses this component for a different file (clicking
  // through documents), state would otherwise hang onto the previous
  // document's bitmap — which is why the framework_showdown.pdf preview was
  // showing LBLF.pdf content. Reset on every file.id change.
  useEffect(() => {
    setDocPreview(getCachedPreview(file.id) ?? null);
  }, [file.id]);

  // Image / video: blob fetch
  useEffect(() => {
    if (file.category !== "image" && file.category !== "video") return;
    let revoke: string | null = null;
    let cancelled = false;
    fetchAsBlobUrl(servedUrl(file.id))
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        revoke = url;
        setImgSrc(url);
      })
      .catch(() => setImgError(true));
    return () => {
      cancelled = true;
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [file.id, file.category]);

  // Document: format-specific render
  useEffect(() => {
    if (file.category !== "document") return;
    if (docPreview) return;
    const renderer = previewerFor(file.original_filename);
    if (!renderer) return;
    setDocLoading(true);
    let cancelled = false;
    fetchMediaBlob(servedUrl(file.id))
      .then((blob) => renderer(blob))
      .then((p) => {
        if (cancelled) return;
        setCachedPreview(file.id, p);
        setDocPreview(p);
      })
      .catch((e) => {
        console.error(`[doc preview] ${file.original_filename}:`, e);
      })
      .finally(() => {
        if (!cancelled) setDocLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [file.id, file.category, file.original_filename, docPreview]);

  if (file.category === "image") {
    if (imgError) {
      return (
        <div className={`flex items-center justify-center bg-elevated text-fg-muted ${className}`}>
          <ImageIcon className="h-10 w-10" strokeWidth={1.4} />
        </div>
      );
    }
    return (
      <div className={`bg-elevated overflow-hidden ${className}`}>
        {imgSrc ? (
          <img
            src={imgSrc}
            alt={file.original_filename || ""}
            className="w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full bg-elevated animate-pulse" />
        )}
      </div>
    );
  }

  if (file.category === "video") {
    return (
      <div className={`relative bg-elevated overflow-hidden ${className}`}>
        {imgSrc ? (
          <video
            src={imgSrc}
            className="w-full h-full object-cover"
            preload="metadata"
            muted
            playsInline
          />
        ) : (
          <div className="w-full h-full bg-elevated animate-pulse" />
        )}
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="h-12 w-12 rounded-full bg-white/95 flex items-center justify-center shadow-card">
            <div className="w-0 h-0 border-y-[7px] border-y-transparent border-l-[10px] border-l-fg ml-0.5" />
          </div>
        </div>
      </div>
    );
  }

  if (file.category === "document") {
    return (
      <DocumentTile
        file={file}
        preview={docPreview}
        loading={docLoading}
        className={className}
        large={large}
      />
    );
  }

  return (
    <div className={`flex items-center justify-center bg-elevated text-fg-muted ${className}`}>
      <FileType2 className="h-12 w-12" strokeWidth={1.4} />
    </div>
  );
}

function DocumentTile({
  file,
  preview,
  loading,
  className,
}: {
  file: FileItem;
  preview: DocPreview | null;
  loading: boolean;
  className?: string;
  large?: boolean;
}) {
  const ext = (file.original_filename || "").split(".").pop()?.toLowerCase() || "";
  const Icon =
    ext === "xlsx" || ext === "xls" || ext === "csv"
      ? FileSpreadsheet
      : FileText;

  const wrapper =
    `bg-white relative overflow-hidden flex items-center justify-center ${className}`;

  if (loading) {
    return (
      <div className={wrapper}>
        <Loader2 className="h-6 w-6 animate-spin text-fg-muted" />
      </div>
    );
  }

  // No bitmap available (unsupported format, render error, or stripped
  // type that we can't preview cheaply): show the typed icon card. Drive
  // does the same thing for media types it can't natively render.
  if (!preview || preview.kind !== "image") {
    return (
      <div className={`flex flex-col items-center justify-center gap-2 ${wrapper}`}>
        <Icon className="h-12 w-12 text-fg-muted" strokeWidth={1.4} />
        {ext && (
          <span className="text-[10px] font-semibold uppercase tracking-widest text-fg-muted">
            {ext}
          </span>
        )}
      </div>
    );
  }

  // Drive-style paper-card: the entire tile IS the document. The card
  // wrapper is now portrait (3:4) for documents (FileCard sets it), so the
  // bitmap fills edge-to-edge with a top-aligned crop, letting the first
  // lines drive recognition.
  return (
    <div className={wrapper}>
      <img
        src={preview.dataUrl}
        alt=""
        className="absolute inset-0 w-full h-full object-cover object-top bg-white"
        loading="lazy"
        draggable={false}
      />
    </div>
  );
}
