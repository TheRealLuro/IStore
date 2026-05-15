import { api } from "./client";

export interface PlanRead {
  tier: "free" | "pro" | "business" | string;
  display_name: string;
  monthly_cents: number | null;
  annual_cents: number | null;
  quota_bytes: number;
  upload_max_per_hour: number;
  upload_max_bytes_per_day: number;
  features: Record<string, boolean | string | number>;
  monthly_available: boolean;
  annual_available: boolean;
}

export interface SubscriptionRead {
  tier: string;
  status: string;
  interval: "month" | "year" | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  stripe_configured: boolean;
  publishable_key: string;
}

export interface CheckoutResponse {
  client_secret: string;
  session_id: string;
  publishable_key: string;
}

export async function listPlans(): Promise<PlanRead[]> {
  return api.get<PlanRead[]>("/billing/plans");
}

export async function getSubscription(): Promise<SubscriptionRead> {
  return api.get<SubscriptionRead>("/billing/subscription");
}

export async function createCheckout(
  tier: string,
  interval: "monthly" | "annual",
): Promise<CheckoutResponse> {
  return api.post<CheckoutResponse>("/billing/checkout", { tier, interval });
}

export async function openPortal(returnUrl: string): Promise<{ url: string }> {
  return api.post<{ url: string }>("/billing/portal", { return_url: returnUrl });
}
