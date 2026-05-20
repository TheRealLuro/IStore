import { API_BASE_URL } from "./client";
import type { FileItem } from "@/types/file";

export interface UploadHandles {
  promise: Promise<FileItem>;
  xhr: XMLHttpRequest;
}

export type UploadPhase =
  | "uploading"   // bytes flowing
  | "processing"  // bytes done, server still running validate / vision / DB write
  | "done"
  | "error";

export interface UploadProgress {
  /** Bytes actually transferred from the client. */
  uploadedBytes: number;
  /** Total bytes the client is sending (file size). */
  totalBytes: number;
  /** Current phase — let the UI show "uploading" vs "processing" vs "done". */
  phase: UploadPhase;
}

/**
 * Upload a single file via XMLHttpRequest so we get true progress events.
 * Reports phase transitions (uploading → processing → done) so the UI can
 * distinguish "bytes still going up the wire" from "server is doing
 * Florence/vision work before responding." Returns both the promise and
 * the XHR so callers can abort mid-upload.
 */
export function uploadFileWithProgress(
  file: File,
  onProgress: (p: UploadProgress) => void,
): UploadHandles {
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `${API_BASE_URL}/images/`);
  // Cookie auth: send the session cookie with the upload. XHR
  // doesn't ship cookies on cross-origin requests unless
  // `withCredentials` is set explicitly.
  xhr.withCredentials = true;
  // Legacy localStorage Bearer fallback for users mid-migration.
  try {
    const legacy = localStorage.getItem("neuthek.jwt");
    if (legacy) xhr.setRequestHeader("Authorization", `Bearer ${legacy}`);
  } catch { /* private browsing */ }

  const total = file.size || 1;
  let uploaded = 0;

  const promise = new Promise<FileItem>((resolve, reject) => {
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        uploaded = e.loaded;
        onProgress({
          uploadedBytes: uploaded,
          totalBytes: total,
          phase: uploaded >= total ? "processing" : "uploading",
        });
      }
    });
    // The browser fires `loadend` on the upload object once the body is
    // fully transmitted, even before the server responds. That's our
    // signal to flip to "processing" — the byte progress is at 100%
    // but the server still hasn't returned an ImageRead row.
    xhr.upload.addEventListener("loadend", () => {
      onProgress({ uploadedBytes: total, totalBytes: total, phase: "processing" });
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const item = JSON.parse(xhr.responseText) as FileItem;
          onProgress({ uploadedBytes: total, totalBytes: total, phase: "done" });
          resolve(item);
        } catch {
          onProgress({ uploadedBytes: uploaded, totalBytes: total, phase: "error" });
          reject(new Error("Invalid response from server"));
        }
      } else {
        let detail = xhr.statusText;
        try {
          const j = JSON.parse(xhr.responseText) as { detail?: string };
          if (j.detail) detail = j.detail;
        } catch {
          /* ignore */
        }
        onProgress({ uploadedBytes: uploaded, totalBytes: total, phase: "error" });
        reject(new Error(detail));
      }
    });
    xhr.addEventListener("error", () => {
      onProgress({ uploadedBytes: uploaded, totalBytes: total, phase: "error" });
      reject(new Error("Network error"));
    });
    xhr.addEventListener("abort", () => {
      onProgress({ uploadedBytes: uploaded, totalBytes: total, phase: "error" });
      reject(new Error("Upload cancelled"));
    });
  });

  const fd = new FormData();
  fd.append("file", file);
  xhr.send(fd);

  return { promise, xhr };
}
