import { api, tokens } from "./client";
import type { User } from "@/types/file";

export async function login(email: string, password: string): Promise<User> {
  const res = await api.postForm<{ access_token: string; token_type: string }>(
    "/auth/jwt/login",
    { username: email, password },
  );
  tokens.set(res.access_token);
  return await me();
}

export async function register(email: string, password: string): Promise<User> {
  return api.post<User>("/auth/register", { email, password });
}

export async function me(): Promise<User> {
  return api.get<User>("/users/me");
}

export function logout(): void {
  tokens.clear();
}
