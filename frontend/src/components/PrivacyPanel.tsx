import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, ShieldCheck, X } from "lucide-react";
import toast from "react-hot-toast";
import {
  getConsentScopes,
  grantScope,
  withdrawScope,
  type ConsentScope,
} from "@/api/consent";

interface Props {
  open: boolean;
  onClose: () => void;
  onOpenFaceConsent: () => void;
}

/** C4 — per-scope consent toggles. One row per supported scope. The
 * face_recognition toggle redirects to the BIPA-grade ConsentModal
 * (signed-statement flow); the others use the lightweight generic
 * grant/withdraw endpoints. */
export function PrivacyPanel({ open, onClose, onOpenFaceConsent }: Props) {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["consent-scopes"],
    queryFn: getConsentScopes,
    enabled: open,
  });

  const grantMutation = useMutation({
    mutationFn: (scope: ConsentScope) => grantScope(scope),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["consent-scopes"] });
      queryClient.invalidateQueries({ queryKey: ["geo"] });
      toast.success("Granted");
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Could not grant"),
  });

  const withdrawMutation = useMutation({
    mutationFn: (scope: ConsentScope) => withdrawScope(scope),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["consent-scopes"] });
      queryClient.invalidateQueries({ queryKey: ["geo"] });
      toast.success("Withdrawn — derived data deleted");
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Could not withdraw"),
  });

  const states = data?.states;
  const scopes: { key: ConsentScope; title: string; desc: string }[] = [
    {
      key: "face_recognition",
      title: "Face recognition",
      desc: "Detect, embed, and cluster faces so you can browse photos by person. Biometric data — separate retention.",
    },
    {
      key: "gps_retention",
      title: "GPS retention",
      desc: "Keep EXIF GPS coordinates so the Map view can plot your photos. Coordinates are never shared.",
    },
    {
      key: "ai_summary",
      title: "AI Vision summaries",
      desc: "Caption images and documents with Florence-2 + Qwen for smarter search.",
    },
    {
      key: "semantic_search",
      title: "Semantic search",
      desc: "Keep CLIP embeddings on uploads so search can match by meaning, not just filename.",
    },
    {
      key: "bandit_compression_telemetry",
      title: "Compression learning",
      desc: "Use your ratings to fine-tune the per-user codec choices (LinUCB). Telemetry is anonymized after 90 days.",
    },
  ];

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 animate-fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-[60] w-[92%] max-w-xl bg-card rounded-3xl shadow-float p-7 animate-scale-in">
          <Dialog.Title className="text-lg font-semibold tracking-tight text-fg flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-accent" />
            Privacy controls
          </Dialog.Title>
          <Dialog.Description className="text-sm text-fg-secondary mt-1">
            Each scope is independent. Withdraw at any time — derived data is
            deleted on revoke, not the next cycle.
          </Dialog.Description>

          <div className="mt-5 space-y-2 max-h-[60vh] overflow-y-auto pr-1">
            {isLoading && (
              <div className="text-fg-secondary flex items-center gap-2 px-2 py-3">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading…
              </div>
            )}
            {states &&
              scopes.map((s) => {
                const state = states[s.key] || "NONE";
                const granted = state === "GRANTED";
                return (
                  <div
                    key={s.key}
                    className="flex items-center gap-3 rounded-2xl bg-elevated/50 px-4 py-3"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-[13px] font-medium text-fg flex items-center gap-2">
                        {s.title}
                        {granted ? (
                          <span className="inline-flex items-center gap-1 rounded-full bg-success/15 text-success px-2 py-0.5 text-[10px] font-medium">
                            <Check className="h-2.5 w-2.5" strokeWidth={3} />
                            granted
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-full bg-elevated text-fg-secondary ring-1 ring-divider px-2 py-0.5 text-[10px] font-medium">
                            <X className="h-2.5 w-2.5" strokeWidth={3} />
                            off
                          </span>
                        )}
                      </div>
                      <div className="text-[12px] text-fg-secondary mt-0.5">
                        {s.desc}
                      </div>
                    </div>
                    {s.key === "face_recognition" ? (
                      <button
                        onClick={() => {
                          onClose();
                          onOpenFaceConsent();
                        }}
                        className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition shrink-0"
                      >
                        Manage
                      </button>
                    ) : granted ? (
                      <button
                        onClick={() => withdrawMutation.mutate(s.key)}
                        disabled={withdrawMutation.isPending}
                        className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition shrink-0 disabled:opacity-50"
                      >
                        Withdraw
                      </button>
                    ) : (
                      <button
                        onClick={() => grantMutation.mutate(s.key)}
                        disabled={grantMutation.isPending}
                        className="h-9 px-3.5 rounded-full bg-accent text-white hover:opacity-90 text-[13px] font-medium transition shrink-0 disabled:opacity-50"
                      >
                        Grant
                      </button>
                    )}
                  </div>
                );
              })}
          </div>

          <div className="mt-5 flex justify-end">
            <button
              onClick={onClose}
              className="h-9 px-3.5 rounded-full bg-fg text-fg-inverse text-[13px] font-medium transition"
            >
              Done
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
