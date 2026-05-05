import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation } from "@tanstack/react-query";
import { Star } from "lucide-react";
import toast from "react-hot-toast";
import { submitRating } from "@/api/feedback";
import type { FileItem } from "@/types/file";

interface Props {
  /** The image the user just downloaded — `null` while the modal is closed. */
  file: FileItem | null;
  onClose: () => void;
}

const DISMISS_KEY = "istore.rating.session_dismissed";

/**
 * Pops on download-original to ask "how was the quality?". Optional —
 * the user can close it without answering. Skipped clicks are not
 * a signal; only an explicit star click writes a feedback_event row.
 *
 * Per session, after the user dismisses without rating, we don't pester
 * them again until reload. Star clicks always submit immediately.
 */
export function RatingModal({ file, onClose }: Props) {
  const [hover, setHover] = useState<number | null>(null);

  const mutation = useMutation({
    mutationFn: ({ id, rating }: { id: string; rating: number }) =>
      submitRating(id, rating),
    onSuccess: (r) => {
      if (r.recorded) toast.success("Thanks for the feedback");
      onClose();
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Couldn't save rating"),
  });

  // Reset hover state when a new file opens.
  useEffect(() => {
    setHover(null);
  }, [file?.id]);

  const open = file !== null;

  const skip = () => {
    sessionStorage.setItem(DISMISS_KEY, "1");
    onClose();
  };

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && skip()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/30 backdrop-blur-sm z-40 animate-fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[92%] max-w-sm bg-card rounded-3xl shadow-float p-7 animate-scale-in">
          <Dialog.Title className="text-base font-semibold tracking-tight text-fg">
            How was the quality?
          </Dialog.Title>
          <Dialog.Description className="text-[12px] text-fg-secondary mt-1">
            Your rating trains the compressor on photos like this one.
          </Dialog.Description>

          <div className="flex items-center justify-center gap-1 mt-5 mb-2">
            {[1, 2, 3, 4, 5].map((n) => {
              const active = (hover ?? 0) >= n;
              return (
                <button
                  key={n}
                  onMouseEnter={() => setHover(n)}
                  onMouseLeave={() => setHover(null)}
                  onClick={() =>
                    file && mutation.mutate({ id: file.id, rating: n })
                  }
                  disabled={mutation.isPending}
                  aria-label={`Rate ${n} star${n === 1 ? "" : "s"}`}
                  className="p-1.5 rounded-full hover:bg-hover transition disabled:opacity-50"
                >
                  <Star
                    className={`h-7 w-7 transition ${
                      active
                        ? "text-amber-400 fill-amber-400"
                        : "text-fg-muted"
                    }`}
                    strokeWidth={1.5}
                  />
                </button>
              );
            })}
          </div>

          <div className="flex justify-center mt-3">
            <button
              onClick={skip}
              disabled={mutation.isPending}
              className="text-[12px] text-fg-secondary hover:text-fg transition px-3 py-1 disabled:opacity-50"
            >
              Skip
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function shouldOfferRating(): boolean {
  return sessionStorage.getItem(DISMISS_KEY) !== "1";
}
