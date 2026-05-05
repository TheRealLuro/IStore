import { useEffect, useState } from "react";
import { fetchAsBlobUrl } from "@/api/files";

interface Props {
  src: string;
  alt?: string;
  className?: string;
}

/**
 * <img> equivalent that attaches the bearer JWT before fetching, then
 * renders the result as a blob URL. Used for endpoints (face crops, served
 * variants) that require auth and can't be loaded by a plain <img src>.
 */
export function AuthedImage({ src, alt = "", className }: Props) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let revoke: string | null = null;
    let cancelled = false;
    fetchAsBlobUrl(src)
      .then((u) => {
        if (cancelled) {
          URL.revokeObjectURL(u);
          return;
        }
        revoke = u;
        setUrl(u);
      })
      .catch(() => setUrl(null));
    return () => {
      cancelled = true;
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [src]);

  if (!url) {
    return <div className={`bg-elevated animate-pulse ${className ?? ""}`} />;
  }
  return (
    <img src={url} alt={alt} className={className} loading="lazy" />
  );
}
