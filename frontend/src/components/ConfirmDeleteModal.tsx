import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Trash2, Loader2 } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { useUIStore } from "@/stores/uiStore";
import { useSelectionStore } from "@/stores/selectionStore";
import { bulkDelete, bulkRestore } from "@/api/files";

const UNDO_WINDOW_MS = 5000;

export function ConfirmDeleteModal() {
  const open = useUIStore((s) => s.confirmDeleteOpen);
  const setOpen = useUIStore((s) => s.setConfirmDelete);
  const setDeleting = useUIStore((s) => s.setDeleting);
  const setUndoBatch = useUIStore((s) => s.setUndoBatch);
  const selected = useSelectionStore((s) => s.selected);
  const clear = useSelectionStore((s) => s.clear);
  const queryClient = useQueryClient();
  const [progress, setProgress] = useState(0);

  const mutation = useMutation({
    mutationFn: async (ids: string[]) => {
      setDeleting(true);
      setProgress(0);
      const result = await bulkDelete(ids);
      setProgress(100);
      return result;
    },
    onSuccess: (_, ids) => {
      queryClient.invalidateQueries({ queryKey: ["files"] });
      queryClient.invalidateQueries({ queryKey: ["storage-usage"] });
      const idArr = [...ids];
      setUndoBatch({ ids: idArr, deadline: Date.now() + UNDO_WINDOW_MS });
      toast(
        (t) => (
          <div className="flex items-center gap-3">
            <span>
              Deleted {idArr.length} file{idArr.length === 1 ? "" : "s"}
            </span>
            <button
              onClick={async () => {
                toast.dismiss(t.id);
                try {
                  await bulkRestore(idArr);
                  setUndoBatch(null);
                  queryClient.invalidateQueries({ queryKey: ["files"] });
                  queryClient.invalidateQueries({ queryKey: ["storage-usage"] });
                  toast.success("Restored");
                } catch {
                  toast.error("Could not restore");
                }
              }}
              className="px-2.5 py-0.5 rounded-md bg-fg/10 hover:bg-fg/20 text-xs font-medium text-fg"
            >
              Undo
            </button>
          </div>
        ),
        { duration: UNDO_WINDOW_MS },
      );
      setTimeout(() => setUndoBatch(null), UNDO_WINDOW_MS);
      clear();
      setOpen(false);
      setDeleting(false);
    },
    onError: (e) => {
      toast.error("Delete failed");
      console.error(e);
      setDeleting(false);
    },
  });

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40 backdrop-blur-sm animate-fade-in z-40" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 bg-card rounded-3xl p-7 w-[92%] max-w-md animate-scale-in shadow-float">
          <div className="flex items-start gap-4">
            <div className="h-11 w-11 rounded-full bg-danger/10 flex items-center justify-center text-danger shrink-0">
              <Trash2 className="h-5 w-5" />
            </div>
            <div>
              <Dialog.Title className="text-lg font-semibold tracking-tight text-fg">
                Delete {selected.size} {selected.size === 1 ? "file" : "files"}?
              </Dialog.Title>
              <Dialog.Description className="text-sm text-fg-secondary mt-1.5 leading-relaxed">
                You'll have a few seconds to undo. Permanent removal happens after that.
              </Dialog.Description>
            </div>
          </div>

          {mutation.isPending && (
            <div className="mt-5 h-1 bg-elevated rounded-full overflow-hidden">
              <div
                className="h-full bg-danger transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
          )}

          <div className="flex justify-end gap-2 mt-7">
            <button
              onClick={() => setOpen(false)}
              disabled={mutation.isPending}
              className="btn-secondary disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={() => mutation.mutate([...selected])}
              disabled={mutation.isPending || selected.size === 0}
              className="btn h-10 px-5 text-sm bg-danger hover:bg-danger/90 text-white shadow-md hover:shadow-lg disabled:opacity-50"
            >
              {mutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" /> Deleting…
                </>
              ) : (
                "Delete"
              )}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
