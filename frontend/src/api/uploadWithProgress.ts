import { API_BASE_URL, tokens } from "./client";
import type { FileItem } from "@/types/file";

export interface UploadHandles {
  promise: Promise<FileItem>;
  xhr: XMLHttpRequest;
}

/**
 * Upload a single file via XMLHttpRequest so we get progress events.
 * Returns both the promise and the XHR (so callers can abort).
 */
export function uploadFileWithProgress(
  file: File,
  onProgress: (uploadedBytes: number) => void,
): UploadHandles {
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `${API_BASE_URL}/images/`);
  const token = tokens.get();
  if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

  const promise = new Promise<FileItem>((resolve, reject) => {
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) onProgress(e.loaded);
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as FileItem);
        } catch {
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
        reject(new Error(detail));
      }
    });
    xhr.addEventListener("error", () => reject(new Error("Network error")));
    xhr.addEventListener("abort", () => reject(new Error("Upload cancelled")));
  });

  const fd = new FormData();
  fd.append("file", file);
  xhr.send(fd);

  return { promise, xhr };
}
