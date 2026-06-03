// SFTP access client (SFTP-1). Wraps the owner-scoped /sftp/* surface
// the backend exposes in `backend/api/sftp.py`:
//
//   GET    /sftp/info       — connection details + ready-to-paste mount
//                             commands (host hint, port, username = the
//                             account email, whether a password is set,
//                             key count, read-only flag).
//   GET    /sftp/keys       — list this user's registered public keys.
//   POST   /sftp/keys       — register an OpenSSH public key.
//   DELETE /sftp/keys/{id}  — remove a registered key.
//   PUT    /sftp/password   — set / change the dedicated SFTP password.
//   DELETE /sftp/password   — clear the SFTP password (disables pw auth).
//
// Keys are the primary, recommended auth method; the SFTP password is a
// SEPARATE secret from the account login (the backend hashes it with the
// same Argon2 + strength policy) and is offered only as a fallback.

import { api } from "./client";

/** One registered OpenSSH public key. Mirrors `SftpKeyOut` in
 *  backend/api/sftp.py — `id` is a UUID string, the timestamps are ISO
 *  strings, and `fingerprint` is the server-computed SHA256 fingerprint
 *  (e.g. "SHA256:abc…") so the user can match it against `ssh-keygen -lf`. */
export interface SftpKey {
  id: string;
  fingerprint: string;
  label: string | null;
  created_at: string;
  last_used_at: string | null;
}

/** Connection info + per-platform commands. Mirrors `SftpInfo`. The
 *  `commands` map keys are stable: backend emits `linux_mac_sshfs`,
 *  `sftp_cli`, `macos_finder`, `windows_winscp`. We render whatever keys
 *  come back rather than hard-coding the set, so a backend that adds a
 *  command shows up without an FE change. */
export interface SftpInfo {
  username: string;
  host: string;
  port: number;
  password_set: boolean;
  key_count: number;
  read_only: boolean;
  commands: Record<string, string>;
}

export async function getSftpInfo(): Promise<SftpInfo> {
  return api.get<SftpInfo>("/sftp/info");
}

export async function listSftpKeys(): Promise<SftpKey[]> {
  return api.get<SftpKey[]>("/sftp/keys");
}

/** Register an OpenSSH public key (`id_*.pub` contents). The backend
 *  validates + fingerprints it; a private key or unparseable input 400s,
 *  and a duplicate 409s — both surface via the thrown ApiError's
 *  `detail`. */
export async function addSftpKey(
  public_key: string,
  label?: string,
): Promise<SftpKey> {
  return api.post<SftpKey>("/sftp/keys", {
    public_key,
    label: label?.trim() ? label.trim() : null,
  });
}

export async function deleteSftpKey(id: string): Promise<void> {
  await api.delete(`/sftp/keys/${id}`);
}

/** Set / change the dedicated SFTP password. Reuses the account
 *  password-strength policy server-side, so a weak password 400s with a
 *  reason in `detail`. Returns nothing (204). */
export async function setSftpPassword(password: string): Promise<void> {
  await api.put("/sftp/password", { password });
}

/** Clear the SFTP password (disables password auth; keys still work). */
export async function clearSftpPassword(): Promise<void> {
  await api.delete("/sftp/password");
}
