import { useState } from "react";
import { Tag, Check, X } from "lucide-react";
import { StatusChip } from "./FolderCard";

const PALETTE = [
  { key: "neutral", label: "Neutral" },
  { key: "blue", label: "In progress" },
  { key: "yellow", label: "Pending" },
  { key: "green", label: "Done" },
  { key: "red", label: "Blocked" },
];

interface Props {
  value: string | null;
  color: string | null;
  /** Called with (label, color) when the user saves; (null, null) clears. */
  onChange: (label: string | null, color: string | null) => void;
  busy?: boolean;
}

/** Compact inline status editor: shows the current chip when present;
 * clicking opens a small picker with a free-text label and color
 * swatches. Used by both PreviewPanel (file) and folder action menu. */
export function StatusPicker({ value, color, onChange, busy = false }: Props) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState(value ?? "");
  const [chosenColor, setChosenColor] = useState<string>(color || "neutral");

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 text-[12px] text-fg-secondary hover:text-fg transition"
      >
        <Tag className="h-3.5 w-3.5" />
        {value ? (
          <StatusChip label={value} color={color} />
        ) : (
          <span>Add status</span>
        )}
      </button>
    );
  }

  return (
    <div className="rounded-2xl bg-elevated p-3 space-y-2.5">
      <div className="flex items-center gap-2">
        <input
          autoFocus
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. submitted"
          maxLength={40}
          className="input h-8 text-[13px]"
        />
        <button
          onClick={() => {
            setOpen(false);
            setLabel(value ?? "");
            setChosenColor(color || "neutral");
          }}
          className="h-8 w-8 rounded-full hover:bg-hover text-fg-secondary flex items-center justify-center"
          aria-label="Cancel"
          disabled={busy}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {PALETTE.map((p) => (
          <button
            key={p.key}
            onClick={() => setChosenColor(p.key)}
            className={`h-7 px-2.5 rounded-full text-[11px] flex items-center gap-1 transition ring-1 ${
              chosenColor === p.key
                ? "ring-accent"
                : "ring-transparent hover:bg-hover"
            }`}
          >
            <StatusChip label={p.label} color={p.key} />
          </button>
        ))}
      </div>
      <div className="flex gap-2 pt-1">
        {value !== null && (
          <button
            onClick={() => {
              onChange(null, null);
              setOpen(false);
            }}
            disabled={busy}
            className="h-8 px-3 rounded-full bg-card hover:bg-hover text-[12px] text-fg-secondary disabled:opacity-50 transition"
          >
            Clear
          </button>
        )}
        <button
          onClick={() => {
            const trimmed = label.trim();
            if (!trimmed) return;
            onChange(trimmed, chosenColor === "neutral" ? null : chosenColor);
            setOpen(false);
          }}
          disabled={busy || !label.trim()}
          className="ml-auto h-8 px-3.5 rounded-full bg-fg text-fg-inverse text-[12px] font-medium disabled:opacity-50 transition flex items-center gap-1.5"
        >
          <Check className="h-3.5 w-3.5" strokeWidth={2.5} />
          Save
        </button>
      </div>
    </div>
  );
}
