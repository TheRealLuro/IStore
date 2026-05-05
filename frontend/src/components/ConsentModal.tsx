import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, ShieldCheck } from "lucide-react";
import toast from "react-hot-toast";
import {
  backfillFaces,
  getPolicy,
  grantConsent,
  withdrawConsent,
  type ConsentStatus,
} from "@/api/consent";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function ConsentModal({ open, onClose }: Props) {
  const queryClient = useQueryClient();
  const consentStatus = queryClient.getQueryData<ConsentStatus>([
    "consent",
    "face_recognition",
  ]);
  const isGranted = consentStatus?.state === "GRANTED";

  const policyQuery = useQuery({
    queryKey: ["consent-policy"],
    queryFn: getPolicy,
    enabled: open,
    staleTime: Infinity,
  });

  const [collection, setCollection] = useState(false);
  const [retention, setRetention] = useState(false);
  const [signature, setSignature] = useState("");

  const grantMutation = useMutation({
    mutationFn: grantConsent,
    onSuccess: async () => {
      toast.success("Face recognition enabled");
      queryClient.invalidateQueries({ queryKey: ["consent", "face_recognition"] });
      onClose();
      try {
        const r = await backfillFaces();
        if (r.queued > 0) {
          toast(`Scanning ${r.queued} existing photo${r.queued === 1 ? "" : "s"}…`, {
            icon: "🔍",
          });
        }
      } catch {
        /* not critical */
      }
    },
    onError: (e) => {
      const msg = e instanceof Error ? e.message : "Could not enable";
      toast.error(msg);
    },
  });

  const withdrawMutation = useMutation({
    mutationFn: withdrawConsent,
    onSuccess: () => {
      toast.success("Face recognition disabled, biometric data deleted");
      queryClient.invalidateQueries({ queryKey: ["consent", "face_recognition"] });
      queryClient.invalidateQueries({ queryKey: ["people"] });
      onClose();
    },
  });

  const formValid = collection && retention && signature.trim().length >= 2;

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 animate-fade-in" />
        <Dialog.Content className="fixed inset-x-4 top-8 bottom-8 sm:inset-x-auto sm:left-1/2 sm:-translate-x-1/2 sm:top-1/2 sm:-translate-y-1/2 sm:max-w-2xl sm:max-h-[88vh] w-auto z-50 bg-card rounded-3xl shadow-float flex flex-col animate-scale-in">
          <header className="px-7 pt-7 pb-4 border-b border-divider flex items-center gap-3">
            <div className="h-10 w-10 rounded-full bg-accent-soft text-accent flex items-center justify-center shrink-0">
              <ShieldCheck className="h-5 w-5" />
            </div>
            <div className="flex-1">
              <Dialog.Title className="text-lg font-semibold tracking-tight text-fg">
                Face recognition consent
              </Dialog.Title>
              <Dialog.Description className="text-sm text-fg-secondary mt-0.5">
                Required by GDPR Art. 9 and Illinois BIPA. Read the full policy
                before granting.
              </Dialog.Description>
            </div>
          </header>

          <div className="flex-1 overflow-y-auto px-7 py-5">
            {policyQuery.isLoading ? (
              <div className="flex items-center gap-2 text-fg-secondary">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading policy…
              </div>
            ) : policyQuery.data ? (
              <article className="prose prose-sm max-w-none text-fg whitespace-pre-wrap leading-relaxed text-[13px]">
                {policyQuery.data.text}
              </article>
            ) : (
              <div className="text-sm text-danger">Policy unavailable.</div>
            )}
          </div>

          <footer className="px-7 py-5 border-t border-divider bg-elevated/50 rounded-b-3xl">
            {isGranted ? (
              <div className="flex items-center justify-between gap-3">
                <div className="text-[13px] text-fg-secondary">
                  Face recognition is currently <span className="font-medium text-success">enabled</span>.
                </div>
                <button
                  onClick={() => withdrawMutation.mutate()}
                  disabled={withdrawMutation.isPending}
                  className="h-10 px-4 rounded-full bg-danger/10 text-danger hover:bg-danger/20 text-sm font-medium transition disabled:opacity-50"
                >
                  {withdrawMutation.isPending ? "Withdrawing…" : "Withdraw consent"}
                </button>
              </div>
            ) : (
              <>
                <Checkbox
                  checked={collection}
                  onChange={setCollection}
                  label="I consent to the collection of biometric face data described above."
                />
                <Checkbox
                  checked={retention}
                  onChange={setRetention}
                  label="I understand the retention period (3 years from last activity, or until I withdraw consent)."
                />
                <label className="block mt-4">
                  <span className="text-[12px] uppercase tracking-wider text-fg-muted">
                    Type your full legal name
                  </span>
                  <input
                    type="text"
                    value={signature}
                    onChange={(e) => setSignature(e.target.value)}
                    placeholder="e.g. Alex Doe"
                    autoComplete="off"
                    className="input mt-1.5"
                  />
                </label>

                <div className="flex justify-end gap-2 mt-5">
                  <button
                    onClick={onClose}
                    className="h-10 px-4 rounded-full bg-elevated hover:bg-hover text-fg text-sm font-medium transition"
                  >
                    Cancel
                  </button>
                  <button
                    disabled={!formValid || grantMutation.isPending}
                    onClick={() =>
                      grantMutation.mutate({
                        signature_text: signature.trim(),
                        consent_collection: collection,
                        consent_retention: retention,
                      })
                    }
                    className="h-10 px-5 rounded-full bg-fg text-fg-inverse text-sm font-medium shadow-card hover:shadow-float disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                  >
                    {grantMutation.isPending ? (
                      <span className="flex items-center gap-2">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Saving…
                      </span>
                    ) : (
                      "Enable face recognition"
                    )}
                  </button>
                </div>
              </>
            )}
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (b: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex items-start gap-2.5 mb-2 cursor-pointer">
      <span
        onClick={() => onChange(!checked)}
        className={`mt-0.5 h-5 w-5 shrink-0 rounded-md flex items-center justify-center transition ${
          checked
            ? "bg-accent text-white"
            : "bg-card ring-1 ring-border hover:ring-fg-muted"
        }`}
      >
        {checked && <Check className="h-3.5 w-3.5" strokeWidth={3} />}
      </span>
      <span className="text-[13px] text-fg leading-relaxed select-none">
        {label}
      </span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="sr-only"
      />
    </label>
  );
}
