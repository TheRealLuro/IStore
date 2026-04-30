import { useRef } from "react";
import { Upload } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { uploadFileWithProgress } from "@/api/uploadWithProgress";
import { useUploadStore } from "@/stores/uploadStore";

export function UploadButton() {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const queryClient = useQueryClient();

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    const store = useUploadStore.getState();

    Array.from(files).forEach(async (f) => {
      const { promise, xhr } = uploadFileWithProgress(f, () => {
        /* set later */
      });
      const id = store.start(f.name, f.size, xhr);
      // Hook progress AFTER we have the id.
      xhr.upload.addEventListener("progress", (ev) => {
        if (ev.lengthComputable)
          useUploadStore.getState().setProgress(id, ev.loaded);
      });
      try {
        await promise;
        useUploadStore.getState().finish(id, "done");
        queryClient.invalidateQueries({ queryKey: ["files"] });
        queryClient.invalidateQueries({ queryKey: ["storage-usage"] });
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Upload failed";
        const cancelled = msg.toLowerCase().includes("cancel");
        useUploadStore
          .getState()
          .finish(id, cancelled ? "cancelled" : "error", cancelled ? undefined : msg);
        if (!cancelled) toast.error(`${f.name}: ${msg}`);
      }
    });
    e.target.value = "";
  };

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={onPick}
      />
      <button
        onClick={() => inputRef.current?.click()}
        aria-label="Upload files"
        className="flex items-center justify-center gap-2 h-11 px-5 rounded-full bg-fg text-fg-inverse text-[14px] font-medium shadow-card hover:shadow-float hover:-translate-y-0.5 transition-all"
      >
        <Upload className="h-4 w-4" strokeWidth={2.25} />
        Upload
      </button>
    </>
  );
}
