import type { FileItem, Folder } from "@/types/file";
import { FileCard } from "./FileCard";
import { FolderCard } from "./FolderCard";

interface Props {
  files: FileItem[];
  folders?: Folder[];
  query: string;
  loading: boolean;
}

/** Folders render before files, then files in their original order.
 * Both share the same grid track so card sizes line up. */
export function FileGrid({ files, folders = [], query, loading }: Props) {
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

  if (files.length === 0 && folders.length === 0) {
    return (
      <div className="px-6 py-32 text-center">
        <p className="text-2xl font-semibold tracking-tight text-fg">
          {query ? "No matches" : "Nothing here yet"}
        </p>
        <p className="text-sm text-fg-secondary mt-2">
          {query
            ? "Try a different search or change the filter."
            : "Upload a file or create a folder to get started."}
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5 px-6 pb-32">
      {folders.map((folder) => (
        <FolderCard key={`folder-${folder.id}`} folder={folder} query={query} />
      ))}
      {files.map((f) => (
        <FileCard key={f.id} file={f} query={query} />
      ))}
    </div>
  );
}
