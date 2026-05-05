import { ChevronRight, Home } from "lucide-react";
import { useFilterStore } from "@/stores/filterStore";

/** Breadcrumb for the current folder path. Renders nothing at root —
 * the gallery already implies "all my files" via the header title. */
export function Breadcrumb() {
  const folderPath = useFilterStore((s) => s.folderPath);
  const navigateToCrumb = useFilterStore((s) => s.navigateToCrumb);

  if (folderPath.length === 0) return null;

  return (
    <nav
      aria-label="Folder path"
      className="px-6 pt-2 pb-1 flex items-center gap-1 text-sm text-fg-secondary overflow-x-auto"
    >
      <button
        onClick={() => navigateToCrumb(-1)}
        className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 hover:bg-hover hover:text-fg transition"
      >
        <Home className="h-3.5 w-3.5" strokeWidth={2} />
        <span>Files</span>
      </button>
      {folderPath.map((crumb, i) => {
        const isLast = i === folderPath.length - 1;
        return (
          <span key={crumb.id} className="inline-flex items-center gap-1">
            <ChevronRight
              className="h-3.5 w-3.5 text-fg-muted shrink-0"
              strokeWidth={2}
            />
            {isLast ? (
              <span className="px-2.5 py-1 font-medium text-fg truncate max-w-[280px]">
                {crumb.name}
              </span>
            ) : (
              <button
                onClick={() => navigateToCrumb(i)}
                className="px-2.5 py-1 rounded-full hover:bg-hover hover:text-fg transition truncate max-w-[180px]"
              >
                {crumb.name}
              </button>
            )}
          </span>
        );
      })}
    </nav>
  );
}
