import { useQuery } from "@tanstack/react-query";
import { ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import { getConsentStatus } from "@/api/consent";
import { ConsentModal } from "./ConsentModal";

const DISMISS_KEY = "istore.consent.banner.dismissed";

/**
 * Subtle banner shown above the storage bar when face recognition has not
 * been granted yet. Dismissable per session via localStorage flag; user can
 * always re-open the modal from settings later (Phase 8 work).
 */
export function ConsentBanner() {
  const { data } = useQuery({
    queryKey: ["consent", "face_recognition"],
    queryFn: getConsentStatus,
    staleTime: 60_000,
  });
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(DISMISS_KEY) === "1",
  );

  if (data && data.state === "GRANTED") return null;
  if (dismissed) return <ConsentModal open={open} onClose={() => setOpen(false)} />;

  return (
    <>
      <div className="border-b border-divider bg-accent-soft/60 px-6 py-2.5 flex items-center gap-3">
        <ShieldCheck className="h-4 w-4 text-accent shrink-0" />
        <div className="flex-1 text-[13px] text-fg">
          <span className="font-medium">Recognize people across your photos.</span>{" "}
          <span className="text-fg-secondary">
            Optional. Face data is yours alone — never shared, deletable any time.
          </span>
        </div>
        <button
          onClick={() => setOpen(true)}
          className="h-8 px-3.5 rounded-full bg-fg text-fg-inverse text-[12px] font-medium hover:opacity-90 transition shrink-0"
        >
          Learn more
        </button>
        <button
          onClick={() => {
            localStorage.setItem(DISMISS_KEY, "1");
            setDismissed(true);
          }}
          aria-label="Dismiss"
          className="h-8 w-8 rounded-full hover:bg-hover text-fg-secondary flex items-center justify-center shrink-0"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <ConsentModal open={open} onClose={() => setOpen(false)} />
    </>
  );
}
