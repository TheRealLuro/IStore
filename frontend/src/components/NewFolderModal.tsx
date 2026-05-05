import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderPlus, Loader2 } from "lucide-react";
import toast from "react-hot-toast";
import { createFolder } from "@/api/folders";
import { useFilterStore } from "@/stores/filterStore";
import { ApiError } from "@/api/client";

interface Props {
  open: boolean;
  onClose: () => void;
}

/** Inline modal for "New folder" — creates inside the currently-open
 * parent (filterStore.folderId). Submits on Enter; surfaces 409 from
 * the unique-name index as a friendly inline error. */
export function NewFolderModal({ open, onClose }: Props) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const parentId = useFilterStore((s) => s.folderId);
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: () =>
      createFolder({ name: name.trim(), parent_folder_id: parentId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["folders", parentId] });
      toast.success("Folder created");
      setName("");
      setError(null);
      onClose();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) {
        setError("A folder with this name already exists here");
      } else {
        setError(e instanceof Error ? e.message : "Failed to create folder");
      }
    },
  });

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          setName("");
          setError(null);
          onClose();
        }
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 animate-fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[92%] max-w-md bg-card rounded-3xl shadow-float p-7 animate-scale-in">
          <Dialog.Title className="text-lg font-semibold tracking-tight text-fg flex items-center gap-2">
            <FolderPlus className="h-5 w-5 text-accent" strokeWidth={1.8} />
            New folder
          </Dialog.Title>
          <Dialog.Description className="text-sm text-fg-secondary mt-1">
            Folders organize your files into projects. You can move files in
            later by drag, or with the move action on each card.
          </Dialog.Description>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!name.trim() || mutation.isPending) return;
              setError(null);
              mutation.mutate();
            }}
            className="mt-5 space-y-3"
          >
            <input
              autoFocus
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (error) setError(null);
              }}
              placeholder="e.g. Assignment 4"
              maxLength={200}
              className="input"
            />
            {error && (
              <div className="text-sm text-danger" role="alert">
                {error}
              </div>
            )}
            <div className="flex gap-2 pt-1">
              <button
                type="button"
                onClick={() => {
                  setName("");
                  setError(null);
                  onClose();
                }}
                className="btn btn-secondary flex-1"
                disabled={mutation.isPending}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!name.trim() || mutation.isPending}
                className="flex-1 h-10 rounded-full bg-fg text-fg-inverse text-sm font-medium shadow-card hover:shadow-float hover:-translate-y-0.5 active:translate-y-0 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              >
                {mutation.isPending && (
                  <Loader2 className="h-4 w-4 animate-spin" />
                )}
                Create
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
