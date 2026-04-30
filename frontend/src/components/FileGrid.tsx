import type { FileItem } from "@/types/file";
import { FileCard } from "./FileCard";

interface Props {
  files: FileItem[];
  query: string;
  loading: boolean;
}

export function FileGrid({ files, query, loading }: Props) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5 px-6 pb-32">
        {Array.from({ length: 8 }).map((_, i) => (
          <div
            key={i}
            className="rounded-3xl bg-card aspect-[4/3] animate-pulse shadow-soft"
          />
        ))}
      </div>
    );
  }

  if (files.length === 0) {
    return (
      <div className="px-6 py-32 text-center">
        <p className="text-2xl font-semibold tracking-tight text-fg">
          No files yet
        </p>
        <p className="text-sm text-fg-secondary mt-2">
          {query
            ? "Try a different search or change the filter."
            : "Upload your first file to get started."}
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5 px-6 pb-32">
      {files.map((f) => (
        <FileCard key={f.id} file={f} query={query} />
      ))}
    </div>
  );
}
