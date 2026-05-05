import { useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Folder as FolderIcon,
  MoreHorizontal,
  Pencil,
  Tag as TagIcon,
  Trash2,
} from "lucide-react";
import toast from "react-hot-toast";
import type { Folder } from "@/types/file";
import { useFilterStore } from "@/stores/filterStore";
import { relativeTime } from "@/utils/format";
import { deleteFolder, moveImageToFolder, updateFolder } from "@/api/folders";

interface Props {
  folder: Folder;
  query: string;
}

/** A folder reads "lighter" than a file card — same outer dimensions so
 * the grid stays uniform, but no thumbnail / no checkbox / softer
 * background. Click enters the folder; the kebab opens an action menu
 * (rename / delete / set status) backed by Radix Popover.
 *
 * DnD: this card is a drop target for image cards. Files dropped here
 * are moved via PATCH /images/{id}/move — we look at
 * `event.dataTransfer.getData('application/x-istore-image')` for the
 * image id (set by FileCard's dragstart). */
export function FolderCard({ folder, query }: Props) {
  const enterFolder = useFilterStore((s) => s.enterFolder);
  const queryClient = useQueryClient();
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState<"rename" | "status" | null>(null);
  const [draftName, setDraftName] = useState(folder.name);
  const [draftStatus, setDraftStatus] = useState(folder.status ?? "");
  const [draftColor, setDraftColor] = useState(folder.status_color ?? "");
  const [dragOver, setDragOver] = useState(false);

  const renameMutation = useMutation({
    mutationFn: (name: string) => updateFolder(folder.id, { name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["folders"] });
      toast.success("Folder renamed");
      setEditing(null);
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Could not rename"),
  });

  const statusMutation = useMutation({
    mutationFn: (vals: { status: string | null; status_color: string | null }) =>
      updateFolder(folder.id, vals),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["folders"] });
      toast.success("Status updated");
      setEditing(null);
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Could not update status"),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteFolder(folder.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["folders"] });
      toast.success("Folder deleted");
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Could not delete"),
  });

  const dropMutation = useMutation({
    mutationFn: (imageId: string) => moveImageToFolder(imageId, folder.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["files"] });
      queryClient.invalidateQueries({ queryKey: ["folders"] });
      toast.success(`Moved to ${folder.name}`);
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Could not move"),
  });

  const onClick = () => {
    if (editing) return; // don't navigate while editing
    enterFolder({ id: folder.id, name: folder.name });
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
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("application/x-istore-image")) {
          e.preventDefault();
          setDragOver(true);
        }
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        const id = e.dataTransfer.getData("application/x-istore-image");
        setDragOver(false);
        if (!id) return;
        e.preventDefault();
        dropMutation.mutate(id);
      }}
      className={clsx(
        "group relative rounded-3xl bg-elevated overflow-hidden transition-all duration-300 cursor-pointer",
        "shadow-soft hover:shadow-card hover:-translate-y-0.5",
        dragOver && "ring-2 ring-accent shadow-card scale-[1.02]",
      )}
    >
      <div className="aspect-[4/3] w-full flex items-center justify-center bg-gradient-to-br from-card to-elevated">
        <FolderIcon
          className="h-16 w-16 text-accent/80 transition group-hover:text-accent group-hover:scale-105"
          strokeWidth={1.5}
        />
      </div>

      {folder.status && (
        <div className="absolute top-2.5 left-2.5">
          <StatusChip label={folder.status} color={folder.status_color} />
        </div>
      )}

      <Popover.Root open={menuOpen} onOpenChange={setMenuOpen}>
        <Popover.Trigger asChild>
          <button
            aria-label="Folder actions"
            onClick={(e) => e.stopPropagation()}
            className={clsx(
              "absolute top-2.5 right-2.5 h-7 w-7 rounded-full flex items-center justify-center transition",
              "bg-white/90 dark:bg-black/40 backdrop-blur ring-1 ring-black/10 dark:ring-white/20",
              "text-fg-secondary opacity-0 group-hover:opacity-100 hover:text-fg",
              menuOpen && "opacity-100",
            )}
          >
            <MoreHorizontal className="h-4 w-4" />
          </button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            sideOffset={6}
            align="end"
            onClick={(e) => e.stopPropagation()}
            className="z-50 min-w-[180px] rounded-2xl bg-card shadow-float p-1.5 ring-1 ring-divider"
          >
            <MenuItem
              icon={<Pencil className="h-3.5 w-3.5" />}
              onClick={() => {
                setDraftName(folder.name);
                setEditing("rename");
                setMenuOpen(false);
              }}
            >
              Rename
            </MenuItem>
            <MenuItem
              icon={<TagIcon className="h-3.5 w-3.5" />}
              onClick={() => {
                setDraftStatus(folder.status ?? "");
                setDraftColor(folder.status_color ?? "");
                setEditing("status");
                setMenuOpen(false);
              }}
            >
              Set status…
            </MenuItem>
            <MenuItem
              tone="danger"
              icon={<Trash2 className="h-3.5 w-3.5" />}
              onClick={() => {
                if (confirm(`Delete "${folder.name}" and its contents?`)) {
                  deleteMutation.mutate();
                }
                setMenuOpen(false);
              }}
            >
              Delete
            </MenuItem>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>

      <div className="px-4 py-3">
        {editing === "rename" ? (
          <input
            autoFocus
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === "Enter") renameMutation.mutate(draftName.trim());
              if (e.key === "Escape") setEditing(null);
            }}
            onBlur={() => {
              if (draftName.trim() && draftName.trim() !== folder.name) {
                renameMutation.mutate(draftName.trim());
              } else {
                setEditing(null);
              }
            }}
            className="w-full text-[14px] font-medium bg-card text-fg px-2 py-1 rounded-lg border border-border outline-none focus:ring-2 focus:ring-accent"
          />
        ) : editing === "status" ? (
          <div
            onClick={(e) => e.stopPropagation()}
            className="space-y-2"
          >
            <input
              autoFocus
              value={draftStatus}
              onChange={(e) => setDraftStatus(e.target.value)}
              placeholder="Status (e.g. submitted)"
              onKeyDown={(e) => e.stopPropagation()}
              className="w-full text-[12px] bg-card text-fg px-2 py-1 rounded-lg border border-border outline-none focus:ring-2 focus:ring-accent"
            />
            <div className="flex gap-1">
              {(["green", "blue", "yellow", "red", "neutral"] as const).map((c) => (
                <button
                  key={c}
                  onClick={() => setDraftColor(c)}
                  aria-label={`Color ${c}`}
                  className={clsx(
                    "h-5 w-5 rounded-full border-2 transition",
                    draftColor === c ? "border-fg" : "border-transparent",
                    c === "green" && "bg-success",
                    c === "blue" && "bg-accent",
                    c === "yellow" && "bg-warning",
                    c === "red" && "bg-danger",
                    c === "neutral" && "bg-fg-muted",
                  )}
                />
              ))}
              <button
                onClick={() =>
                  statusMutation.mutate({
                    status: draftStatus.trim() || null,
                    status_color: draftStatus.trim() ? draftColor || "neutral" : null,
                  })
                }
                className="ml-auto h-6 px-2 rounded-full bg-fg text-fg-inverse text-[11px] font-medium"
              >
                Save
              </button>
              <button
                onClick={() => setEditing(null)}
                className="h-6 px-2 rounded-full bg-elevated text-fg text-[11px]"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="text-[14px] font-medium text-fg truncate">
              {query && folder.name.toLowerCase().includes(query.toLowerCase()) ? (
                <Mark text={folder.name} q={query} />
              ) : (
                folder.name
              )}
            </div>
            <div className="text-[12px] text-fg-secondary mt-0.5 flex items-center gap-1.5">
              <span>
                {folder.item_count} {folder.item_count === 1 ? "file" : "files"}
              </span>
              {folder.subfolder_count > 0 && (
                <>
                  <span className="text-fg-muted">·</span>
                  <span>
                    {folder.subfolder_count}{" "}
                    {folder.subfolder_count === 1 ? "folder" : "folders"}
                  </span>
                </>
              )}
              <span className="text-fg-muted">·</span>
              <span>{relativeTime(folder.updated_at)}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function MenuItem({
  icon,
  children,
  onClick,
  tone = "default",
}: {
  icon: React.ReactNode;
  children: React.ReactNode;
  onClick: () => void;
  tone?: "default" | "danger";
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "w-full flex items-center gap-2 px-3 py-2 rounded-xl text-[13px] transition text-left",
        tone === "danger"
          ? "text-danger hover:bg-danger/10"
          : "text-fg hover:bg-elevated",
      )}
    >
      {icon}
      {children}
    </button>
  );
}

function Mark({ text, q }: { text: string; q: string }) {
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, i)}
      <mark className="bg-accent-soft text-accent rounded px-0.5">
        {text.slice(i, i + q.length)}
      </mark>
      {text.slice(i + q.length)}
    </>
  );
}

interface StatusChipProps {
  label: string;
  color: string | null;
}

/** A pill chip for project status. Color is a free-form key the user
 * chooses from a small palette; unknown values fall back to neutral. */
export function StatusChip({ label, color }: StatusChipProps) {
  const tone = STATUS_TONES[color || ""] || STATUS_TONES.neutral;
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
        tone,
      )}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}

const STATUS_TONES: Record<string, string> = {
  green: "bg-success/15 text-success",
  blue: "bg-accent-soft text-accent",
  yellow: "bg-warning/15 text-warning",
  red: "bg-danger/15 text-danger",
  neutral: "bg-elevated text-fg-secondary ring-1 ring-divider",
};
