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

export const renamePerson = (personId: number, display_name: string) =>
  api.patch<PersonRead>(`/people/${personId}`, { display_name });

export const deletePerson = (personId: number) =>
  api.delete<void>(`/people/${personId}`);

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
