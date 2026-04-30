import clsx from "clsx";
import { LayoutGrid, FileText, Film, Image as ImageIcon } from "lucide-react";
import { useFilterStore, type CategoryFilter } from "@/stores/filterStore";
import type { FileItem } from "@/types/file";

interface TabsProps {
  files: FileItem[];
}

const TABS: { id: CategoryFilter; label: string; Icon: typeof LayoutGrid }[] = [
  { id: "all", label: "All", Icon: LayoutGrid },
  { id: "document", label: "Documents", Icon: FileText },
  { id: "video", label: "Videos", Icon: Film },
  { id: "image", label: "Images", Icon: ImageIcon },
];

export function FilterTabs({ files }: TabsProps) {
  const category = useFilterStore((s) => s.category);
  const setCategory = useFilterStore((s) => s.setCategory);

  const counts = files.reduce<Record<string, number>>((acc, f) => {
    acc[f.category] = (acc[f.category] ?? 0) + 1;
    return acc;
  }, {});
  counts.all = files.length;

  return (
    <div className="flex gap-2 overflow-x-auto px-6 py-4 no-scrollbar">
      {TABS.map(({ id, label, Icon }) => {
        const active = category === id;
        const count = counts[id] ?? 0;
        return (
          <button
            key={id}
            onClick={() => setCategory(id)}
            className={clsx(
              "flex items-center gap-2 px-4 h-9 rounded-full text-[13px] font-medium whitespace-nowrap transition-all",
              active
                ? "bg-fg text-fg-inverse shadow-card"
                : "bg-card text-fg-secondary hover:bg-hover hover:text-fg ring-1 ring-border",
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
            <span
              className={clsx(
                "ml-0.5 inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full text-[11px] font-semibold tabular-nums",
                active ? "bg-white/15 text-fg-inverse" : "bg-elevated text-fg-secondary",
              )}
            >
              {count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
