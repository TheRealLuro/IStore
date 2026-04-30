import { useEffect, useState } from "react";
import { FileText, FileSpreadsheet, FileType2, Image as ImageIcon, Loader2 } from "lucide-react";
import { servedUrl, fetchAsBlobUrl } from "@/api/files";
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
    const token = localStorage.getItem("istore.jwt");
    fetch(servedUrl(file.id), {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => {
        if (!r.ok) throw new Error(`fetch ${r.status}`);
        return r.blob();
      })
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
  large,
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
    `bg-card relative overflow-hidden ` +
    `${className} ` +
    (large ? "" : "border-b border-divider");

  if (loading) {
    return (
      <div className={`flex items-center justify-center ${wrapper}`}>
        <Loader2 className="h-6 w-6 animate-spin text-fg-muted" />
      </div>
    );
  }

  if (!preview) {
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

  if (preview.kind === "image") {
    return (
      <div className={wrapper}>
        <img
          src={preview.dataUrl}
          alt=""
          className="w-full h-full object-cover object-top"
          loading="lazy"
        />
      </div>
    );
  }

  if (preview.kind === "html") {
    return (
      <div className={`${wrapper} bg-card`}>
        <div
          className="absolute inset-0 p-4 text-[8px] leading-tight overflow-hidden text-fg pointer-events-none origin-top-left"
          style={large ? { fontSize: "12px" } : { transform: "scale(1)" }}
          dangerouslySetInnerHTML={{ __html: preview.html }}
        />
        <div className="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-card to-transparent pointer-events-none" />
      </div>
    );
  }

  if (preview.kind === "table") {
    const cols = Math.min(6, preview.rows[0]?.length ?? 0);
    return (
      <div className={`${wrapper} bg-card`}>
        <div className="absolute inset-0 overflow-hidden">
          <table className="w-full text-[8px] leading-tight border-collapse">
            <tbody>
              {preview.rows.map((row, i) => (
                <tr key={i} className={i === 0 ? "font-semibold bg-elevated" : ""}>
                  {row.slice(0, cols).map((cell, j) => (
                    <td
                      key={j}
                      className="px-1 py-0.5 border border-divider truncate max-w-[60px]"
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="absolute inset-x-0 bottom-0 h-1/4 bg-gradient-to-t from-card to-transparent pointer-events-none" />
      </div>
    );
  }

  if (preview.kind === "text") {
    return (
      <div className={`${wrapper} bg-card p-3`}>
        <pre className="text-[9px] leading-tight whitespace-pre-wrap break-words text-fg-secondary line-clamp-[16] overflow-hidden">
          {preview.text}
        </pre>
        <div className="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-card to-transparent pointer-events-none" />
      </div>
    );
  }

  return (
    <div className={`flex items-center justify-center ${wrapper}`}>
      <Icon className="h-12 w-12 text-fg-muted" strokeWidth={1.4} />
    </div>
  );
}
