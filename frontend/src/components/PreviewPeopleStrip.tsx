import { useQuery } from "@tanstack/react-query";
import { Loader2, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { faceCropUrl, getImagePeople, type ImagePerson } from "@/api/people";
import { useFilterStore } from "@/stores/filterStore";
import { useUIStore } from "@/stores/uiStore";
import { AuthedImage } from "./AuthedImage";
import { NamePersonModal } from "./NamePersonModal";

interface Props {
  imageId: string;
  /** True when the backend hasn't run Pass B on this image yet — render
   * a placeholder so the user knows scanning is in flight. */
  pendingScan: boolean;
  /** True when consent.face_recognition is GRANTED. When false, we show a
   * gentle nudge instead of the strip. */
  consentActive: boolean;
}

/**
 * "People in this photo" strip rendered above the metadata box in the
 * preview panel. One avatar per detected face. Click a named avatar to
 * filter the gallery to that person; click an unnamed avatar to open
 * the naming modal.
 */
export function PreviewPeopleStrip({ imageId, pendingScan, consentActive }: Props) {
  const setPerson = useFilterStore((s) => s.setPerson);
  const setPreview = useUIStore((s) => s.setPreview);
  const [naming, setNaming] = useState<ImagePerson | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["image-people", imageId],
    queryFn: () => getImagePeople(imageId),
    enabled: consentActive,
    staleTime: 10_000,
    // While Pass B hasn't completed, poll every 4s so the strip pops in
    // automatically once the background scan finishes — no manual refresh.
    refetchInterval: pendingScan ? 4000 : false,
  });

  if (!consentActive) {
    return (
      <Wrapper title="People">
        <div className="flex items-center gap-3 rounded-2xl bg-elevated px-4 py-3.5 text-[12px] text-fg-secondary">
          <Users className="h-4 w-4 text-fg-muted" />
          Face recognition is off. Enable it in Account settings to identify
          people in your photos.
        </div>
      </Wrapper>
    );
  }

  if (isLoading) {
    return (
      <Wrapper title="People">
        <div className="flex items-center gap-2 text-[12px] text-fg-secondary py-1">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Loading…
        </div>
      </Wrapper>
    );
  }

  const faces = data ?? [];

  if (faces.length === 0) {
    if (pendingScan) {
      return (
        <Wrapper title="People">
          <div className="flex items-center gap-2 text-[12px] text-fg-secondary py-1">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Scanning faces…
          </div>
        </Wrapper>
      );
    }
    return null;
  }

  const named = faces.filter((f) => f.person_display_name);
  const unnamed = faces.filter((f) => !f.person_display_name);

  // Distinct names, in order of first appearance, for the inline summary.
  const namesInline = Array.from(
    new Set(named.map((n) => n.person_display_name!).filter(Boolean)),
  );

  return (
    <>
      <Wrapper
        title="People"
        rightSlot={
          <span className="text-fg-muted normal-case tracking-normal">
            {namesInline.length > 0 && (
              <span className="text-fg-secondary font-medium">
                {namesInline.join(" · ")}
              </span>
            )}
            {namesInline.length > 0 && unnamed.length > 0 && (
              <span className="mx-1.5 text-fg-muted">·</span>
            )}
            {unnamed.length > 0 && `${unnamed.length} to identify`}
          </span>
        }
      >
        <div className="flex gap-3 overflow-x-auto pb-1 pr-1 -mx-1 px-1 no-scrollbar">
          {faces.map((f) => (
            <FaceAvatar
              key={f.face_id}
              face={f}
              onClickNamed={(name) => {
                setPerson(name);
                setPreview(null);
              }}
              onClickUnnamed={() => setNaming(f)}
            />
          ))}
        </div>
      </Wrapper>

      {naming && (
        <NamePersonModal
          cluster={
            naming.cluster_id !== null
              ? {
                  cluster_id: naming.cluster_id,
                  face_count: 1,  // exact count not surfaced here; modal copy is generic
                  sample_face_id: naming.face_id,
                }
              : null
          }
          onClose={() => setNaming(null)}
        />
      )}
    </>
  );
}

function FaceAvatar({
  face,
  onClickNamed,
  onClickUnnamed,
}: {
  face: ImagePerson;
  onClickNamed: (name: string) => void;
  onClickUnnamed: () => void;
}) {
  const named = !!face.person_display_name;
  const ringClass = named
    ? "ring-2 ring-transparent group-hover:ring-accent"
    : "ring-2 ring-dashed ring-fg-muted/40 group-hover:ring-accent";

  return (
    <button
      onClick={() =>
        named
          ? onClickNamed(face.person_display_name!)
          : onClickUnnamed()
      }
      className="shrink-0 group flex flex-col items-center gap-1.5"
      title={named ? `Filter by ${face.person_display_name}` : "Name this person"}
    >
      <div
        className={`relative h-14 w-14 rounded-full overflow-hidden bg-card transition ${ringClass}`}
      >
        <AuthedImage
          src={faceCropUrl(face.face_id)}
          className="w-full h-full object-cover"
        />
        {!named && (
          <div className="absolute inset-0 bg-fg/50 opacity-0 group-hover:opacity-100 transition flex items-center justify-center">
            <UserPlus className="h-4 w-4 text-white" />
          </div>
        )}
      </div>
      <div
        className={`text-[11px] max-w-[72px] truncate ${
          named ? "font-medium text-fg" : "text-fg-secondary"
        }`}
      >
        {face.person_display_name || "Name them"}
      </div>
    </button>
  );
}

function Wrapper({
  title,
  rightSlot,
  children,
}: {
  title: string;
  rightSlot?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-fg-muted mb-2">
        <span>{title}</span>
        {rightSlot}
      </div>
      <div>{children}</div>
    </div>
  );
}
