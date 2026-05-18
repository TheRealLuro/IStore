/* §G2 — comments on any file the user can read (owner OR active
   share recipient). The backend writes a single thread per image;
   the FE threads them by `parent_id` at render time. */
import { api } from "./client";

export interface AnchorPdf {
  kind: "pdf";
  page: number;
  rect?: [number, number, number, number]; // [x, y, w, h] in page-normalized coords
}
export interface AnchorSlide {
  kind: "slide";
  index: number;
}
export interface AnchorVideo {
  kind: "video";
  t_start: number;
  t_end?: number;
}
export interface AnchorImage {
  kind: "image";
  x: number; // 0-1
  y: number; // 0-1
}
export type CommentAnchor = AnchorPdf | AnchorSlide | AnchorVideo | AnchorImage;

export interface CommentRead {
  id: number;
  image_id: string;
  user_id: string | null;
  parent_id: number | null;
  body: string;
  anchor_json: CommentAnchor | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  author_display_name: string | null;
  author_email: string | null;
}

export interface CommentCreate {
  body: string;
  parent_id?: number | null;
  anchor_json?: CommentAnchor | null;
}

export const listComments = (imageId: string): Promise<CommentRead[]> =>
  api.get<CommentRead[]>(`/images/${imageId}/comments`);

export const createComment = (imageId: string, body: CommentCreate): Promise<CommentRead> =>
  api.post<CommentRead>(`/images/${imageId}/comments`, body);

export const editComment = (commentId: number, body: string): Promise<CommentRead> =>
  api.patch<CommentRead>(`/comments/${commentId}`, { body });

export const deleteComment = (commentId: number): Promise<void> =>
  api.delete(`/comments/${commentId}`).then(() => undefined);
