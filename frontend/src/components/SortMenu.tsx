import * as Popover from "@radix-ui/react-popover";
import { ArrowUpDown, Check } from "lucide-react";
import clsx from "clsx";
import { useFilterStore, type SortMode } from "@/stores/filterStore";

const OPTIONS: { value: SortMode; label: string }[] = [
  { value: "uploaded_desc", label: "Newest first" },
  { value: "uploaded_asc", label: "Oldest first" },
  { value: "name_asc", label: "Name (A → Z)" },
  { value: "name_desc", label: "Name (Z → A)" },
  { value: "size_desc", label: "Largest first" },
  { value: "size_asc", label: "Smallest first" },
];

/** Compact sort dropdown for the topbar. Folders are still rendered
 * before files regardless of sort — this only orders the file list. */
export function SortMenu() {
  const sortMode = useFilterStore((s) => s.sortMode);
  const setSortMode = useFilterStore((s) => s.setSortMode);

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          aria-label="Sort"
          title="Sort"
          className="btn-icon"
        >
          <ArrowUpDown className="h-4 w-4" />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          sideOffset={6}
          align="end"
          className="z-50 min-w-[180px] rounded-2xl bg-card shadow-float p-1.5 ring-1 ring-divider"
        >
          {OPTIONS.map((o) => (
            <button
              key={o.value}
              onClick={() => setSortMode(o.value)}
              className={clsx(
                "w-full flex items-center gap-2 px-3 py-2 rounded-xl text-[13px] transition text-left",
                sortMode === o.value
                  ? "bg-elevated text-fg"
                  : "text-fg-secondary hover:bg-elevated hover:text-fg",
              )}
            >
              <Check
                className={clsx(
                  "h-3.5 w-3.5",
                  sortMode === o.value ? "text-accent" : "invisible",
                )}
              />
              {o.label}
            </button>
          ))}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
