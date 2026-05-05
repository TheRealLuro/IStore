import { useEffect, useState } from "react";
import { ArrowUp } from "lucide-react";

/** Floating "jump to top" button. Shows once the user has scrolled
 * past one viewport height; clicking smooth-scrolls to the top. Sits
 * at z-30 so it overlays the gallery but stays under modals. */
export function JumpToTop() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > window.innerHeight);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  if (!visible) return null;

  return (
    <button
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
      aria-label="Jump to top"
      title="Jump to top"
      className="fixed bottom-6 left-6 z-30 h-11 w-11 rounded-full bg-card border border-divider shadow-float flex items-center justify-center text-fg hover:-translate-y-0.5 hover:shadow-card active:translate-y-0 transition-all animate-fade-in"
    >
      <ArrowUp className="h-4 w-4" strokeWidth={2.25} />
    </button>
  );
}
