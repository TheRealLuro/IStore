import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ChevronDown, ChevronUp, UserPlus, Users } from "lucide-react";
import { listPeople, faceCropUrl, type ClusterRead, type PersonRead } from "@/api/people";
import { useFilterStore } from "@/stores/filterStore";
import { AuthedImage } from "./AuthedImage";
import { NamePersonModal } from "./NamePersonModal";

/**
 * Collapsible drawer above the gallery showing:
 *   - named persons (click to filter)
 *   - unnamed face clusters (click "Name" to label)
 *
 * Hidden when there are zero persons + zero clusters (or consent inactive,
 * which makes the API return an empty list).
 */
export function PeopleTray() {
  const { data } = useQuery({
    queryKey: ["people"],
    queryFn: listPeople,
    staleTime: 30_000,
  });
  const [open, setOpen] = useState(true);
  const [namingCluster, setNamingCluster] = useState<ClusterRead | null>(null);

  if (!data) return null;
  if (data.persons.length === 0 && data.unlabeled_clusters.length === 0) return null;

  return (
    <section className="px-6 pt-3 pb-1">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-fg-muted hover:text-fg-secondary transition mb-3"
      >
        <Users className="h-3.5 w-3.5" />
        People
        <span className="normal-case tracking-normal text-fg-muted">
          ({data.persons.length} named · {data.unlabeled_clusters.length} to identify)
        </span>
        {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
      </button>

      {open && (
        <div className="flex gap-3 overflow-x-auto pb-3 no-scrollbar">
          {data.persons.map((p) => (
            <PersonCard key={`p-${p.id}`} person={p} />
          ))}
          {data.unlabeled_clusters.map((c) => (
            <ClusterCard
              key={`c-${c.cluster_id}`}
              cluster={c}
              onName={() => setNamingCluster(c)}
            />
          ))}
        </div>
      )}

      <NamePersonModal
        cluster={namingCluster}
        onClose={() => setNamingCluster(null)}
      />
    </section>
  );
}

function PersonCard({ person }: { person: PersonRead }) {
  const setPerson = useFilterStore((s) => s.setPerson);
  const activePerson = useFilterStore((s) => s.person);
  const isActive = activePerson === person.display_name;

  return (
    <button
      onClick={() => setPerson(isActive ? null : person.display_name)}
      className={`shrink-0 group flex flex-col items-center gap-1.5 transition ${
        isActive ? "" : "opacity-90 hover:opacity-100"
      }`}
    >
      <div
        className={`h-16 w-16 rounded-full overflow-hidden bg-elevated transition-all ring-2 ${
          isActive ? "ring-accent" : "ring-transparent group-hover:ring-fg-muted"
        }`}
      >
        {person.sample_face_id ? (
          <AuthedImage
            src={faceCropUrl(person.sample_face_id)}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-fg-muted">
            <Users className="h-5 w-5" />
          </div>
        )}
      </div>
      <div className="text-[11px] font-medium text-fg max-w-[80px] truncate">
        {person.display_name || "Unnamed"}
      </div>
      <div className="text-[10px] text-fg-muted -mt-0.5">{person.face_count}</div>
    </button>
  );
}

function ClusterCard({
  cluster,
  onName,
}: {
  cluster: ClusterRead;
  onName: () => void;
}) {
  return (
    <button
      onClick={onName}
      className="shrink-0 group flex flex-col items-center gap-1.5"
    >
      <div className="relative h-16 w-16 rounded-full overflow-hidden bg-elevated ring-2 ring-dashed ring-fg-muted/40 group-hover:ring-accent transition">
        <AuthedImage
          src={faceCropUrl(cluster.sample_face_id)}
          className="w-full h-full object-cover"
        />
        <div className="absolute inset-0 bg-fg/40 opacity-0 group-hover:opacity-100 transition flex items-center justify-center">
          <UserPlus className="h-5 w-5 text-white" />
        </div>
      </div>
      <div className="text-[11px] font-medium text-fg-secondary max-w-[80px] truncate">
        Name them
      </div>
      <div className="text-[10px] text-fg-muted -mt-0.5">{cluster.face_count}</div>
    </button>
  );
}
