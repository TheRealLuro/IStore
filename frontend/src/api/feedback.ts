import { api } from "./client";

export interface FeedbackResponse {
  recorded: boolean;
  reason?: string | null;
}

export const submitRating = (imageId: string, rating: number) =>
  api.post<FeedbackResponse>(`/images/${imageId}/feedback`, { rating });
