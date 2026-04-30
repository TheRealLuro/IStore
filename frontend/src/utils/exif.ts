/** Simplified EXIF row used by the preview panel. */
export interface ExifRow {
  label: string;
  value: string;
}

const RAW_FIELDS: { key: string; label: string; format?: (v: unknown) => string }[] = [
  { key: "Make", label: "Camera make" },
  { key: "Model", label: "Camera model" },
  { key: "LensModel", label: "Lens" },
  { key: "Software", label: "Software" },
  { key: "DateTimeOriginal", label: "Captured", format: formatDate },
  { key: "CreateDate", label: "Created", format: formatDate },
  { key: "ModifyDate", label: "Modified", format: formatDate },
  { key: "FNumber", label: "Aperture", format: (v) => `ƒ/${v}` },
  { key: "ExposureTime", label: "Exposure", format: formatExposure },
  { key: "ISO", label: "ISO" },
  { key: "FocalLength", label: "Focal length", format: (v) => `${v} mm` },
  { key: "FocalLengthIn35mmFormat", label: "Focal length (35mm)", format: (v) => `${v} mm` },
  { key: "Flash", label: "Flash" },
  { key: "WhiteBalance", label: "White balance" },
  { key: "ExposureProgram", label: "Exposure program" },
  { key: "MeteringMode", label: "Metering" },
  { key: "Orientation", label: "Orientation" },
  { key: "ColorSpace", label: "Color space" },
  { key: "ExifImageWidth", label: "Pixel width", format: (v) => `${v}` },
  { key: "ExifImageHeight", label: "Pixel height", format: (v) => `${v}` },
  { key: "Artist", label: "Artist" },
  { key: "Copyright", label: "Copyright" },
];

function formatDate(v: unknown): string {
  if (v instanceof Date) {
    try {
      return v.toLocaleString();
    } catch {
      return v.toString();
    }
  }
  return String(v ?? "");
}

function formatExposure(v: unknown): string {
  if (typeof v === "number") {
    if (v >= 1) return `${v.toFixed(2)} s`;
    const denom = Math.round(1 / v);
    return `1/${denom} s`;
  }
  return String(v ?? "");
}

/**
 * Parse EXIF from a Blob using exifr. Returns a flat list of human-readable rows.
 * exifr is loaded lazily so it doesn't bloat the main bundle.
 */
export async function parseExifRows(blob: Blob): Promise<ExifRow[]> {
  const exifr = (await import("exifr")).default;
  let parsed: Record<string, unknown> | undefined;
  try {
    // exifr's default segment set covers TIFF/EXIF/GPS — adequate for our needs.
    parsed = (await exifr.parse(blob)) as Record<string, unknown> | undefined;
  } catch (e) {
    console.warn("exifr parse error", e);
    return [];
  }
  if (!parsed) return [];

  const rows: ExifRow[] = [];
  for (const f of RAW_FIELDS) {
    const v = parsed[f.key];
    if (v === undefined || v === null || v === "") continue;
    rows.push({ label: f.label, value: f.format ? f.format(v) : String(v) });
  }

  // GPS — exifr returns a `latitude`/`longitude` after the standard `gps` block.
  const lat = parsed.latitude ?? parsed.GPSLatitude;
  const lng = parsed.longitude ?? parsed.GPSLongitude;
  if (typeof lat === "number" && typeof lng === "number") {
    rows.push({
      label: "GPS",
      value: `${lat.toFixed(5)}, ${lng.toFixed(5)}`,
    });
  }

  return rows;
}
