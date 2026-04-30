import { useEffect, useRef, useState } from "react";
import { Search, X, Clock } from "lucide-react";
import { useFilterStore } from "@/stores/filterStore";

const COMMON_EXTENSIONS = ["pdf", "docx", "xlsx", "png", "jpg", "mp4", "mov"];

export function SearchBar({ resultsCount }: { resultsCount: number }) {
  const query = useFilterStore((s) => s.query);
  const setQuery = useFilterStore((s) => s.setQuery);
  const setCategory = useFilterStore((s) => s.setCategory);
  const recentQueries = useFilterStore((s) => s.recentQueries);
  const pushRecent = useFilterStore((s) => s.pushRecent);

  const [focused, setFocused] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!containerRef.current?.contains(e.target as Node)) {
        setFocused(false);
        if (query.length > 0) setQuery("");
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [query, setQuery]);

  const showSuggestions = focused && query.length === 0;

  const onClear = () => {
    setQuery("");
    setCategory("all");
  };

  return (
    <div ref={containerRef} className="relative w-full max-w-xl">
      <div
        className={`flex items-center gap-2 h-10 px-4 rounded-full bg-elevated transition-all ${
          focused ? "ring-2 ring-accent/30 bg-card" : ""
        }`}
      >
        <Search className="h-4 w-4 text-fg-muted shrink-0" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => {
            if (query.trim()) pushRecent(query);
          }}
          placeholder="Search files…"
          className="flex-1 bg-transparent border-0 outline-none placeholder:text-fg-muted text-[13px] text-fg"
        />
        {query && (
          <span className="text-[11px] text-fg-secondary tabular-nums shrink-0">
            {resultsCount} {resultsCount === 1 ? "result" : "results"}
          </span>
        )}
        {query && (
          <button
            onClick={onClear}
            aria-label="Clear search"
            className="p-1 rounded-full hover:bg-hover text-fg-muted hover:text-fg transition"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {showSuggestions && (
        <div className="absolute top-full mt-2 w-full bg-card border border-border rounded-2xl shadow-float py-2 z-30 animate-fade-in">
          {recentQueries.length > 0 && (
            <>
              <div className="px-4 py-1.5 text-[10px] uppercase tracking-wider text-fg-muted">
                Recent
              </div>
              {recentQueries.slice(0, 5).map((q) => (
                <button
                  key={q}
                  onClick={() => {
                    setQuery(q);
                    setFocused(false);
                  }}
                  className="w-full flex items-center gap-2 px-4 py-2 text-sm text-left hover:bg-hover transition text-fg"
                >
                  <Clock className="h-3.5 w-3.5 text-fg-muted" />
                  {q}
                </button>
              ))}
            </>
          )}
          <div className="px-4 py-1.5 text-[10px] uppercase tracking-wider text-fg-muted border-t border-border mt-1 pt-2">
            Try by extension
          </div>
          <div className="flex gap-1 flex-wrap px-3 pb-2 pt-1">
            {COMMON_EXTENSIONS.map((ext) => (
              <button
                key={ext}
                onClick={() => {
                  setQuery(`.${ext}`);
                  setFocused(false);
                }}
                className="px-2 py-1 text-[11px] uppercase tracking-wider rounded-md bg-elevated hover:bg-hover transition text-fg-secondary"
              >
                .{ext}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
