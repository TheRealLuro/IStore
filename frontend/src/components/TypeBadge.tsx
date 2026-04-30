import clsx from "clsx";
import { fileExtension } from "@/utils/format";
import type { FileItem } from "@/types/file";

const COLORS: Record<string, string> = {
  PDF: "bg-red-500/15 text-red-600 dark:text-red-300",
  DOC: "bg-blue-500/15 text-blue-600 dark:text-blue-300",
  DOCX: "bg-blue-500/15 text-blue-600 dark:text-blue-300",
  XLS: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  XLSX: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  PPT: "bg-orange-500/15 text-orange-600 dark:text-orange-300",
  PPTX: "bg-orange-500/15 text-orange-600 dark:text-orange-300",
  TXT: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-300",
  MD: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-300",
  CSV: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  MP4: "bg-purple-500/15 text-purple-600 dark:text-purple-300",
  MOV: "bg-purple-500/15 text-purple-600 dark:text-purple-300",
  WEBM: "bg-purple-500/15 text-purple-600 dark:text-purple-300",
  AVI: "bg-purple-500/15 text-purple-600 dark:text-purple-300",
  JPG: "bg-cyan-500/15 text-cyan-700 dark:text-cyan-300",
  JPEG: "bg-cyan-500/15 text-cyan-700 dark:text-cyan-300",
  PNG: "bg-cyan-500/15 text-cyan-700 dark:text-cyan-300",
  WEBP: "bg-cyan-500/15 text-cyan-700 dark:text-cyan-300",
  GIF: "bg-cyan-500/15 text-cyan-700 dark:text-cyan-300",
  HEIC: "bg-cyan-500/15 text-cyan-700 dark:text-cyan-300",
};

export function TypeBadge({ file, query }: { file: FileItem; query?: string }) {
  const ext = fileExtension(file.original_filename) || file.category.toUpperCase();
  const color = COLORS[ext] ?? "bg-elevated text-fg-secondary";
  const matches =
    query && ext.toLowerCase().includes(query.toLowerCase()) ? "ring-2 ring-accent/40" : "";

  return (
    <span
      className={clsx(
        "inline-flex items-center justify-center px-2 py-0.5 rounded-md text-[10px] font-semibold tracking-wider backdrop-blur",
        color,
        matches,
      )}
    >
      {ext || "FILE"}
    </span>
  );
}
