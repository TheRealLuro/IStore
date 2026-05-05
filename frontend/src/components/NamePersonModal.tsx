import { useState, useEffect } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, UserPlus } from "lucide-react";
import toast from "react-hot-toast";
import { faceCropUrl, listPeople, nameCluster, type ClusterRead } from "@/api/people";
import { AuthedImage } from "./AuthedImage";

interface Props {
  cluster: ClusterRead | null;
  onClose: () => void;
}

export function NamePersonModal({ cluster, onClose }: Props) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");

  useEffect(() => {
    if (cluster) setName("");
  }, [cluster]);

  const open = cluster !== null;

  // Existing named people — clicking one merges the cluster into that person
  // (the backend's `name_cluster` endpoint reuses a person with matching
  // display_name). This is the discoverable "this is also Mom" path.
  const { data: people } = useQuery({
    queryKey: ["people"],
    queryFn: listPeople,
    enabled: open,
    staleTime: 30_000,
  });

  const mutation = useMutation({
    mutationFn: (n: string) => nameCluster(cluster!.cluster_id, n),
    onSuccess: () => {
      toast.success(`Named "${name.trim()}"`);
      queryClient.invalidateQueries({ queryKey: ["people"] });
      queryClient.invalidateQueries({ queryKey: ["files"] });
      queryClient.invalidateQueries({ queryKey: ["image-people"] });
      onClose();
    },
    onError: (e) => {
      toast.error(e instanceof Error ? e.message : "Could not save name");
    },
  });

  const existing = (people?.persons ?? []).filter(
    (p): p is typeof p & { display_name: string } => !!p.display_name,
  );
  const canSave = name.trim().length > 0 && !mutation.isPending;

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 animate-fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[92%] max-w-md bg-card rounded-3xl shadow-float p-7 animate-scale-in">
          <Dialog.Title className="text-lg font-semibold tracking-tight text-fg flex items-center gap-2">
            <UserPlus className="h-5 w-5 text-accent" />
            Who is this?
          </Dialog.Title>
          <Dialog.Description className="text-sm text-fg-secondary mt-1">
            We found {cluster?.face_count ?? 0}{" "}
            {(cluster?.face_count ?? 0) === 1 ? "face" : "faces"} that look like
            the same person. Naming them once tags every photo.
          </Dialog.Description>

          {cluster && (
            <div className="flex items-center justify-center mt-5 mb-5">
              <div className="h-32 w-32 rounded-full overflow-hidden bg-elevated ring-4 ring-elevated">
                <AuthedImage
                  src={faceCropUrl(cluster.sample_face_id)}
                  className="w-full h-full object-cover"
                />
              </div>
            </div>
          )}

          {existing.length > 0 && (
            <div className="mb-3">
              <div className="text-[10px] uppercase tracking-[0.18em] text-fg-muted mb-2">
                Same as someone you've named?
              </div>
              <div className="flex gap-2 overflow-x-auto pb-1 no-scrollbar">
                {existing.map((p) => (
                  <button
                    key={p.id}
                    onClick={() => mutation.mutate(p.display_name)}
                    disabled={mutation.isPending}
                    className="shrink-0 flex items-center gap-2 rounded-full bg-elevated hover:bg-hover px-2 py-1.5 transition disabled:opacity-50"
                    title={`Add to ${p.display_name}`}
                  >
                    {p.sample_face_id ? (
                      <span className="h-6 w-6 rounded-full overflow-hidden bg-card">
                        <AuthedImage
                          src={faceCropUrl(p.sample_face_id)}
                          className="w-full h-full object-cover"
                        />
                      </span>
                    ) : (
                      <span className="h-6 w-6 rounded-full bg-card" />
                    )}
                    <span className="text-[12px] font-medium text-fg pr-1.5">
                      {p.display_name}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && canSave) mutation.mutate(name.trim());
            }}
            placeholder="e.g. Mom"
            className="input"
          />

          <div className="flex justify-end gap-2 mt-5">
            <button
              onClick={onClose}
              className="h-10 px-4 rounded-full bg-elevated hover:bg-hover text-fg text-sm font-medium transition"
            >
              Cancel
            </button>
            <button
              onClick={() => mutation.mutate(name.trim())}
              disabled={!canSave}
              className="h-10 px-5 rounded-full bg-fg text-fg-inverse text-sm font-medium shadow-card hover:shadow-float transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {mutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Save
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
