import { api, API_BASE_URL } from "./client";

export interface PersonRead {
  id: number;
  display_name: string | null;
  face_count: number;
  sample_face_id: number | null;
}

export interface ClusterRead {
  cluster_id: number;
  face_count: number;
  sample_face_id: number;
}

export interface PeopleResponse {
  persons: PersonRead[];
  unlabeled_clusters: ClusterRead[];
  total_faces: number;
}

export const listPeople = () => api.get<PeopleResponse>("/people/");

export const nameCluster = (clusterId: number, display_name: string) =>
  api.post<PersonRead>(`/people/clusters/${clusterId}`, { display_name });

// Delete an UNNAMED face cluster (junk detection — mask, cartoon, false
// positive). Removes the user's unlabeled Face rows in that cluster
// (their FaceDetection rows + crop blobs are cleaned up server-side).
// 404s for a named or other-user cluster. Mirrors the People-tab named
// `deletePerson` affordance but keys off the cluster id.
export const deleteCluster = (clusterId: number) =>
  api.delete<void>(`/people/clusters/${clusterId}`);

// Shape returned by both "not a person" endpoints. `rejected` = how many
// real face embeddings were written into the user's rejection memory (so
// the next scan suppresses them); `faces_deleted` = how many Face rows
// were removed. The underlying photos are never touched.
export interface NotAPersonResponse {
  rejected: number;
  faces_deleted: number;
}

// Mark an UNNAMED cluster as a false positive ("not a person"). Records
// each face's embedding into the user's rejection memory, then deletes
// the cluster's Face rows (+ detections + crops) — same removal as
// `deleteCluster`, but the face model REMEMBERS the rejection so a later
// re-scan won't re-create the same bogus detection. Photos are untouched.
// 404s for a named or other-user cluster. This is the primary People-tab
// hover affordance.
export const markNotAPerson = (clusterId: number) =>
  api.post<NotAPersonResponse>(
    `/people/clusters/${clusterId}/not-a-person`,
  );

// Mark a SINGLE detected face as a false positive ("not a person"). Used
// by the face-chip menu. Records that face's embedding into the rejection
// memory, deletes the one Face row (+ its detection + crop), and — if the
// face was attached to a named person — recomputes that person's centroid
// (the person + name survive). The underlying photo is untouched. 404s
// for an unknown / other-user face.
export const markFaceNotAPerson = (faceId: number) =>
  api.post<NotAPersonResponse>(
    `/people/faces/${faceId}/not-a-person`,
  );

export const renamePerson = (personId: number, display_name: string) =>
  api.patch<PersonRead>(`/people/${personId}`, { display_name });

export const deletePerson = (personId: number) =>
  api.delete<void>(`/people/${personId}`);

// Per-face correction — re-targets a single Face row to a different
// Person, or detaches it entirely. `personId: null` removes the face
// from whatever person it's currently under (fixing a bad cluster
// match); a number moves it to that person. Distinct from
// `reassignDetection` above: this keys off the *face* id and is the
// contract the People-tab / preview "Not this person" + "Move to…"
// controls build against. Endpoint added by the backend in parallel.
export const reassignFace = (faceId: number, personId: number | null) =>
  api.patch<{ face_id: number; person_id: number | null }>(
    `/people/faces/${faceId}`,
    { person_id: personId },
  );

export const faceCropUrl = (faceId: number): string =>
  `${API_BASE_URL}/faces/${faceId}/crop`;

export interface ImagePerson {
  face_id: number;
  detection_id: number;
  person_id: number | null;
  person_display_name: string | null;
  cluster_id: number | null;
  bbox: [number, number, number, number];
  detection_confidence: number | null;
}

export const getImagePeople = (imageId: string) =>
  api.get<ImagePerson[]>(`/images/${imageId}/people`);

/** D8 — user-signal cascade. Returns the stage that fired
 *  ("retina-0.3" | "retina-0.15" | "mediapipe" | "empty") plus how
 *  many faces ended up persisted. */
export const redetectFaces = (imageId: string) =>
  api.post<{ stage: string; detected: number; persisted: number }>(
    `/images/${imageId}/redetect-faces`,
  );

// Per-face-detection relabel: re-targets a single FaceDetection to a
// different (or new) Person without renaming the existing Person.
// Used by the preview panel's face chip so correcting a misdetection
// doesn't cascade to every photo of the originally-grouped person.
export const reassignDetection = (
  detectionId: number,
  body: { person_id?: number | null; display_name?: string | null },
) =>
  api.post<{ detection_id: number; face_id: number; person_id: number | null }>(
    `/faces/detections/${detectionId}/reassign`,
    body,
  );

// Multi-select bulk action: run face detection on the given images and
// label every newly-detected face with `display_name`. Reuses an
// existing Person with that name if there is one.
export const detectAndLabel = (image_ids: string[], display_name: string) =>
  api.post<{
    person_id: number;
    display_name: string;
    images_scanned: number;
    new_faces: number;
  }>("/people/detect-and-label", { image_ids, display_name });
