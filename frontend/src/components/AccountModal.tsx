import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Cloud,
  Copy,
  Database,
  Download,
  KeyRound,
  Loader2,
  RefreshCw,
  Settings,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import toast from "react-hot-toast";
import { deleteAccount, downloadExport } from "@/api/account";
import {
  getRecoveryCodesStatus,
  regenerateRecoveryCodes,
  requestVerify,
} from "@/api/auth";
import {
  connectProvider,
  listCloudLinks,
  revokeCloudLink,
  syncCloudLink,
} from "@/api/cloud";
import { rescanAllFaces } from "@/api/consent";
import { backfillSummaries } from "@/api/files";
import { getStorageUsage } from "@/api/storage";
import { useAuthStore } from "@/stores/authStore";
import { formatBytes } from "@/utils/format";
import { ConsentModal } from "./ConsentModal";
import { PrivacyPanel } from "./PrivacyPanel";
import { ProfileSection } from "./ProfileSection";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function AccountModal({ open, onClose }: Props) {
  const signOut = useAuthStore((s) => s.signOut);
  const user = useAuthStore((s) => s.user);
  const queryClient = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [consentOpen, setConsentOpen] = useState(false);
  const [privacyOpen, setPrivacyOpen] = useState(false);
  const [freshCodes, setFreshCodes] = useState<string[] | null>(null);

  const { data: storage } = useQuery({
    queryKey: ["storage-usage"],
    queryFn: getStorageUsage,
    enabled: open,
    staleTime: 30_000,
  });

  const { data: codesStatus } = useQuery({
    queryKey: ["recovery-codes"],
    queryFn: getRecoveryCodesStatus,
    enabled: open,
    staleTime: 30_000,
  });

  const regenerateMutation = useMutation({
    mutationFn: regenerateRecoveryCodes,
    onSuccess: (r) => {
      setFreshCodes(r.codes);
      queryClient.invalidateQueries({ queryKey: ["recovery-codes"] });
      toast.success("8 fresh recovery codes generated");
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Could not regenerate codes"),
  });

  const verifyMutation = useMutation({
    mutationFn: () => requestVerify(user?.email || ""),
    onSuccess: () => toast.success("Verification email sent"),
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Could not send email"),
  });

  const cloudConnectMutation = useMutation({
    mutationFn: () => connectProvider("google_drive"),
    onSuccess: (r) => {
      // Hand off to Google. The provider redirects back to
      // /cloud/callback/google_drive, which 302s the user back to the FE
      // root with `?cloud_connected=google_drive` so we can toast.
      window.location.href = r.auth_url;
    },
    onError: (e) => {
      // 503 is the "Drive OAuth client / encryption key not configured"
      // path — surface the server's hint so the operator knows what's
      // missing without having to read backend logs.
      toast.error(
        e instanceof Error
          ? `Cloud sync isn't ready: ${e.message}`
          : "Cloud sync isn't ready",
        { duration: 7000 },
      );
    },
  });

  // Existing cloud links — drives the "Sync now" + "Disconnect" rows.
  const { data: cloudLinks } = useQuery({
    queryKey: ["cloud-links"],
    queryFn: listCloudLinks,
    enabled: open,
    staleTime: 10_000,
  });

  const cloudSyncMutation = useMutation({
    mutationFn: (id: number) => syncCloudLink(id),
    onSuccess: (r) => {
      toast.success(
        r.pulled === 0
          ? `Drive sync done — nothing new (${r.seen} files seen).`
          : `Pulled ${r.pulled} new file${r.pulled === 1 ? "" : "s"} from Drive.`,
      );
      queryClient.invalidateQueries({ queryKey: ["files"] });
      queryClient.invalidateQueries({ queryKey: ["cloud-links"] });
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Sync failed"),
  });

  const cloudRevokeMutation = useMutation({
    mutationFn: (id: number) => revokeCloudLink(id),
    onSuccess: () => {
      toast.success("Disconnected");
      queryClient.invalidateQueries({ queryKey: ["cloud-links"] });
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Could not disconnect"),
  });

  const exportMutation = useMutation({
    mutationFn: downloadExport,
    onSuccess: () => toast.success("Export started"),
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Export failed"),
  });

  const backfillMutation = useMutation({
    mutationFn: ({ force }: { force: boolean }) =>
      backfillSummaries(500, force),
    onSuccess: (r) => {
      toast(
        r.queued === 0
          ? "No files needed summaries."
          : `${r.force ? "Regenerating" : "Generating"} summaries for ${r.queued} file${r.queued === 1 ? "" : "s"}…`,
        { icon: "✨" },
      );
      queryClient.invalidateQueries({ queryKey: ["files"] });
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Backfill failed"),
  });

  const rescanMutation = useMutation({
    mutationFn: rescanAllFaces,
    onSuccess: (r) => {
      if (!r.consent_active) {
        toast.error("Enable face recognition first");
        return;
      }
      toast(
        `Re-scanning ${r.queued} photo${r.queued === 1 ? "" : "s"}…`,
        { icon: "🔍" },
      );
      // Existing face rows were dropped; tray will repopulate as Pass B runs.
      queryClient.invalidateQueries({ queryKey: ["people"] });
      queryClient.invalidateQueries({ queryKey: ["image-people"] });
      queryClient.invalidateQueries({ queryKey: ["files"] });
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Re-scan failed"),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteAccount,
    onSuccess: (r) => {
      toast.success(
        `Account deleted (${r.images_deleted} images, ${r.blobs_deleted} blobs).`,
      );
      onClose();
      signOut();
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Could not delete account"),
  });

  return (
    <>
      <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 animate-fade-in" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[92%] max-w-lg bg-card rounded-3xl shadow-float p-7 animate-scale-in">
            <Dialog.Title className="text-lg font-semibold tracking-tight text-fg flex items-center gap-2">
              <Settings className="h-5 w-5 text-accent" />
              Account
            </Dialog.Title>
            <Dialog.Description className="text-sm text-fg-secondary mt-1">
              Privacy controls, data export, and account deletion.
            </Dialog.Description>

            <div className="mt-5 space-y-3 max-h-[70vh] overflow-y-auto pr-1">
              <ProfileSection />

              {user && !user.is_verified && (
                <Row
                  icon={<AlertTriangle className="h-4 w-4 text-warning" />}
                  title="Verify your email"
                  desc="We sent a link when you signed up. Resend if it expired."
                  action={
                    <button
                      onClick={() => verifyMutation.mutate()}
                      disabled={verifyMutation.isPending}
                      className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition disabled:opacity-50 flex items-center gap-1.5"
                    >
                      {verifyMutation.isPending ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : null}
                      Resend
                    </button>
                  }
                />
              )}

              <Row
                icon={<KeyRound className="h-4 w-4 text-accent" />}
                title="Recovery codes"
                desc={
                  codesStatus?.has_codes
                    ? `${codesStatus.remaining} of 8 codes remaining. Regenerate to invalidate the prior set.`
                    : "Generate single-use codes to sign in if you lose your password."
                }
                action={
                  <button
                    onClick={() => regenerateMutation.mutate()}
                    disabled={regenerateMutation.isPending}
                    className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition disabled:opacity-50 flex items-center gap-1.5"
                  >
                    {regenerateMutation.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : null}
                    {codesStatus?.has_codes ? "Regenerate" : "Generate"}
                  </button>
                }
              />

              <Row
                icon={<ShieldCheck className="h-4 w-4 text-accent" />}
                title="Privacy controls"
                desc="Per-scope toggles: face recognition, GPS retention, AI summaries, semantic search, compression telemetry."
                action={
                  <button
                    onClick={() => setPrivacyOpen(true)}
                    className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition"
                  >
                    Manage
                  </button>
                }
              />

              {storage && (
                <div className="rounded-2xl bg-elevated/50 px-4 py-3.5">
                  <div className="flex items-center gap-3">
                    <div className="h-8 w-8 rounded-full bg-card flex items-center justify-center shrink-0">
                      <Database className="h-4 w-4 text-accent" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-[13px] font-medium text-fg">
                        Storage used
                      </div>
                      <div className="text-[12px] text-fg-secondary">
                        {formatBytes(storage.used_bytes)} of{" "}
                        {formatBytes(storage.quota_bytes)}
                      </div>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-1.5 mt-3">
                    {Object.entries(storage.by_category).map(([cat, bytes]) => (
                      <div
                        key={cat}
                        className="rounded-xl bg-card px-3 py-2 text-[11px] flex items-center justify-between"
                      >
                        <span className="text-fg-secondary capitalize">
                          {cat}
                        </span>
                        <span className="text-fg font-medium">
                          {formatBytes(bytes)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <Row
                icon={<Sparkles className="h-4 w-4 text-accent" />}
                title="Generate AI Vision summaries"
                desc="Caption every file (image, video, document). Use Regenerate to replace existing summaries after a model upgrade."
                action={
                  <div className="flex gap-1.5">
                    <button
                      onClick={() => backfillMutation.mutate({ force: false })}
                      disabled={backfillMutation.isPending}
                      className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition disabled:opacity-50 flex items-center gap-1.5"
                    >
                      {backfillMutation.isPending ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : null}
                      Generate
                    </button>
                    <button
                      onClick={() => backfillMutation.mutate({ force: true })}
                      disabled={backfillMutation.isPending}
                      className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition disabled:opacity-50"
                    >
                      Regenerate
                    </button>
                  </div>
                }
              />

              <Row
                icon={<RefreshCw className="h-4 w-4 text-accent" />}
                title="Re-scan all photos"
                desc="Rebuild face clusters from scratch — useful after enabling face recognition or upgrading models."
                action={
                  <button
                    onClick={() => rescanMutation.mutate()}
                    disabled={rescanMutation.isPending}
                    className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition disabled:opacity-50 flex items-center gap-1.5"
                  >
                    {rescanMutation.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : null}
                    Re-scan
                  </button>
                }
              />

              {/* Cloud sync — Google Drive read-only. If the user has an
                  active link, show its sync state with manual-sync +
                  disconnect; otherwise show the connect button. */}
              {(cloudLinks ?? []).filter((l) => l.provider === "google_drive").map((link) => (
                <Row
                  key={link.id}
                  icon={<Cloud className="h-4 w-4 text-accent" />}
                  title="Google Drive — connected"
                  desc={
                    link.last_synced_at
                      ? `Last synced ${new Date(link.last_synced_at).toLocaleString()}`
                      : "Never synced. Click Sync now to pull your Drive images."
                  }
                  action={
                    <div className="flex gap-1.5">
                      <button
                        onClick={() => cloudSyncMutation.mutate(link.id)}
                        disabled={cloudSyncMutation.isPending}
                        className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition disabled:opacity-50 flex items-center gap-1.5"
                      >
                        {cloudSyncMutation.isPending ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : null}
                        Sync now
                      </button>
                      <button
                        onClick={() => cloudRevokeMutation.mutate(link.id)}
                        disabled={cloudRevokeMutation.isPending}
                        className="h-9 px-3.5 rounded-full bg-danger/10 text-danger hover:bg-danger/20 text-[13px] font-medium transition disabled:opacity-50"
                      >
                        Disconnect
                      </button>
                    </div>
                  }
                />
              ))}
              {!(cloudLinks ?? []).some((l) => l.provider === "google_drive") && (
                <Row
                  icon={<Cloud className="h-4 w-4 text-accent" />}
                  title="Connect Google Drive"
                  desc="Pull-only sync of your Drive images (read-only OAuth scope). Refresh tokens are encrypted at rest."
                  action={
                    <button
                      onClick={() => cloudConnectMutation.mutate()}
                      disabled={cloudConnectMutation.isPending}
                      className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition disabled:opacity-50 flex items-center gap-1.5"
                    >
                      {cloudConnectMutation.isPending ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : null}
                      Connect
                    </button>
                  }
                />
              )}

              <Row
                icon={<Download className="h-4 w-4 text-accent" />}
                title="Export your data"
                desc="GDPR Art. 20 — download a ZIP of every byte we store about you."
                action={
                  <button
                    onClick={() => exportMutation.mutate()}
                    disabled={exportMutation.isPending}
                    className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition disabled:opacity-50 flex items-center gap-1.5"
                  >
                    {exportMutation.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : null}
                    Download
                  </button>
                }
              />

              <Row
                icon={<Trash2 className="h-4 w-4 text-danger" />}
                title="Delete account"
                desc="Hard-deletes your user, images, faces, and audit log entries."
                action={
                  <button
                    onClick={() => setConfirmDelete(true)}
                    className="h-9 px-3.5 rounded-full bg-danger/10 text-danger hover:bg-danger/20 text-[13px] font-medium transition"
                  >
                    Delete…
                  </button>
                }
              />
            </div>

            {confirmDelete && (
              <div className="mt-5 rounded-2xl border border-danger/30 bg-danger/5 p-4">
                <div className="flex items-start gap-2 text-danger">
                  <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                  <div className="text-[13px] leading-relaxed">
                    This is irreversible. Type <code className="bg-card px-1 py-0.5 rounded text-[12px]">DELETE</code> to confirm.
                  </div>
                </div>
                <input
                  autoFocus
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  placeholder="DELETE"
                  className="input mt-3"
                />
                <div className="flex justify-end gap-2 mt-3">
                  <button
                    onClick={() => {
                      setConfirmDelete(false);
                      setConfirmText("");
                    }}
                    className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => deleteMutation.mutate()}
                    disabled={confirmText !== "DELETE" || deleteMutation.isPending}
                    className="h-9 px-3.5 rounded-full bg-danger text-white text-[13px] font-medium transition disabled:opacity-50 flex items-center gap-1.5"
                  >
                    {deleteMutation.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : null}
                    Permanently delete
                  </button>
                </div>
              </div>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <ConsentModal open={consentOpen} onClose={() => setConsentOpen(false)} />

      <PrivacyPanel
        open={privacyOpen}
        onClose={() => setPrivacyOpen(false)}
        onOpenFaceConsent={() => setConsentOpen(true)}
      />

      <RecoveryCodesDisplay
        codes={freshCodes}
        onClose={() => setFreshCodes(null)}
      />
    </>
  );
}

function RecoveryCodesDisplay({
  codes,
  onClose,
}: {
  codes: string[] | null;
  onClose: () => void;
}) {
  const copyAll = async () => {
    if (!codes) return;
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      toast.success("Copied to clipboard");
    } catch {
      toast.error("Could not copy — please write the codes down manually");
    }
  };

  return (
    <Dialog.Root open={codes !== null} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 animate-fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-[60] w-[92%] max-w-lg bg-card rounded-3xl shadow-float p-7 animate-scale-in">
          <Dialog.Title className="text-lg font-semibold tracking-tight text-fg flex items-center gap-2">
            <KeyRound className="h-5 w-5 text-accent" />
            Save your recovery codes
          </Dialog.Title>
          <Dialog.Description className="text-sm text-fg-secondary mt-1">
            Each code can be used once. Keep them somewhere safe — you
            won&apos;t be able to view them again. We&apos;ve also emailed a
            copy.
          </Dialog.Description>

          {codes && (
            <div className="mt-5 grid grid-cols-2 gap-2 font-mono text-[13px] tracking-wider">
              {codes.map((c) => (
                <div
                  key={c}
                  className="rounded-xl bg-elevated px-3 py-2.5 text-center text-fg"
                >
                  {c}
                </div>
              ))}
            </div>
          )}

          <div className="flex justify-end gap-2 mt-5">
            <button
              onClick={copyAll}
              className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition flex items-center gap-1.5"
            >
              <Copy className="h-3.5 w-3.5" />
              Copy all
            </button>
            <button
              onClick={onClose}
              className="h-9 px-3.5 rounded-full bg-fg text-fg-inverse text-[13px] font-medium transition"
            >
              I&apos;ve saved them
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Row({
  icon,
  title,
  desc,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
  action: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 rounded-2xl bg-elevated/50 px-4 py-3.5">
      <div className="h-8 w-8 rounded-full bg-card flex items-center justify-center shrink-0">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium text-fg">{title}</div>
        <div className="text-[12px] text-fg-secondary">{desc}</div>
      </div>
      <div className="shrink-0">{action}</div>
    </div>
  );
}
