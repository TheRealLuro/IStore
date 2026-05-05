import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Database,
  History,
  Loader2,
  ShieldAlert,
  Users,
} from "lucide-react";
import toast from "react-hot-toast";
import {
  getAdminStorage,
  listAdminAudit,
  listAdminUsers,
  updateUserQuota,
  type AdminUserRead,
} from "@/api/admin";
import { formatBytes, relativeTime } from "@/utils/format";

interface Props {
  open: boolean;
  onClose: () => void;
}

/** Superuser-only operations console: cluster storage, user list with
 * quota controls, and audit log viewer.
 *
 * Rendered as a fullscreen dialog so the tables have room to breathe.
 * The component itself doesn't gate on `is_superuser` — App.tsx hides
 * the entry point when the bit is false. The backend gates every
 * request via `current_superuser`, so a non-superuser who somehow opens
 * this modal just sees 403s. */
export function AdminPanel({ open, onClose }: Props) {
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50 backdrop-blur-sm z-40 animate-fade-in" />
        <Dialog.Content className="fixed inset-4 md:inset-10 z-50 bg-card rounded-3xl shadow-float overflow-hidden animate-scale-in flex flex-col">
          <div className="flex items-center justify-between border-b border-divider px-7 py-5">
            <div>
              <Dialog.Title className="text-lg font-semibold tracking-tight text-fg flex items-center gap-2">
                <ShieldAlert className="h-5 w-5 text-accent" />
                Admin
              </Dialog.Title>
              <Dialog.Description className="text-sm text-fg-secondary mt-0.5">
                Cluster operations — visible to superusers only.
              </Dialog.Description>
            </div>
            <button
              onClick={onClose}
              className="h-9 px-3.5 rounded-full bg-elevated hover:bg-hover text-fg text-[13px] font-medium transition"
            >
              Close
            </button>
          </div>

          <Tabs.Root defaultValue="storage" className="flex-1 flex flex-col min-h-0">
            <Tabs.List className="border-b border-divider px-7 flex gap-1">
              <TabTrigger value="storage" icon={<Database className="h-4 w-4" />}>
                Storage
              </TabTrigger>
              <TabTrigger value="users" icon={<Users className="h-4 w-4" />}>
                Users
              </TabTrigger>
              <TabTrigger value="audit" icon={<History className="h-4 w-4" />}>
                Audit log
              </TabTrigger>
            </Tabs.List>

            <Tabs.Content value="storage" className="flex-1 overflow-y-auto p-7">
              <StorageTab />
            </Tabs.Content>
            <Tabs.Content value="users" className="flex-1 overflow-y-auto p-7">
              <UsersTab />
            </Tabs.Content>
            <Tabs.Content value="audit" className="flex-1 overflow-y-auto p-7">
              <AuditTab />
            </Tabs.Content>
          </Tabs.Root>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function TabTrigger({
  value,
  icon,
  children,
}: {
  value: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Tabs.Trigger
      value={value}
      className="px-4 py-3 text-[13px] font-medium text-fg-secondary hover:text-fg data-[state=active]:text-accent data-[state=active]:border-b-2 data-[state=active]:border-accent flex items-center gap-2 -mb-px"
    >
      {icon}
      {children}
    </Tabs.Trigger>
  );
}

function StorageTab() {
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "storage"],
    queryFn: () => getAdminStorage(50),
    refetchOnWindowFocus: false,
  });

  if (isLoading) {
    return (
      <div className="text-fg-secondary flex items-center gap-2">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading…
      </div>
    );
  }
  if (!data) return null;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Stat label="Total bytes" value={formatBytes(data.total_bytes)} />
        <Stat label="Total images" value={data.total_images.toLocaleString()} />
        <Stat
          label="Categories"
          value={Object.keys(data.by_category).length.toString()}
        />
      </div>

      <div>
        <h3 className="text-[13px] font-medium text-fg-secondary mb-2">By category</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {Object.entries(data.by_category).map(([cat, bytes]) => (
            <div key={cat} className="rounded-2xl bg-elevated px-4 py-3">
              <div className="text-[12px] text-fg-secondary">{cat}</div>
              <div className="text-[14px] font-medium text-fg">
                {formatBytes(bytes)}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h3 className="text-[13px] font-medium text-fg-secondary mb-2">
          Top users by usage
        </h3>
        <div className="rounded-2xl bg-elevated/50 overflow-hidden">
          <table className="w-full text-[13px]">
            <thead className="bg-elevated text-fg-secondary text-[12px] uppercase tracking-wide">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium">User</th>
                <th className="text-right px-4 py-2.5 font-medium">Images</th>
                <th className="text-right px-4 py-2.5 font-medium">Used</th>
                <th className="text-right px-4 py-2.5 font-medium">Quota</th>
              </tr>
            </thead>
            <tbody>
              {data.top_users.map((u) => (
                <tr key={u.user_id} className="border-t border-divider/50">
                  <td className="px-4 py-2.5 text-fg">
                    <div className="font-medium">
                      {u.display_name || "(no name)"}
                    </div>
                    <div className="text-[11px] text-fg-secondary truncate">
                      {u.email}
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-right text-fg-secondary">
                    {u.image_count.toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {formatBytes(u.used_bytes)}
                  </td>
                  <td className="px-4 py-2.5 text-right text-fg-secondary">
                    {formatBytes(u.quota_bytes)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function UsersTab() {
  const queryClient = useQueryClient();
  const [q, setQ] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "users", q],
    queryFn: () => listAdminUsers(q || null, 100, 0),
    refetchOnWindowFocus: false,
  });

  const quotaMutation = useMutation({
    mutationFn: ({ id, gb }: { id: string; gb: number | null }) =>
      updateUserQuota(id, gb === null ? null : gb * 1024 ** 3),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "storage"] });
      toast.success("Quota updated");
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Could not update quota"),
  });

  return (
    <div className="space-y-4">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search by email or display name…"
        className="input"
      />
      {isLoading && (
        <div className="text-fg-secondary flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      )}
      {data && data.length === 0 && (
        <div className="text-fg-secondary text-[13px]">No users match.</div>
      )}
      {data && data.length > 0 && (
        <div className="rounded-2xl bg-elevated/50 overflow-hidden">
          <table className="w-full text-[13px]">
            <thead className="bg-elevated text-fg-secondary text-[12px] uppercase tracking-wide">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium">User</th>
                <th className="text-left px-4 py-2.5 font-medium">Status</th>
                <th className="text-right px-4 py-2.5 font-medium">Used</th>
                <th className="text-right px-4 py-2.5 font-medium">Quota</th>
              </tr>
            </thead>
            <tbody>
              {data.map((u) => (
                <UserRow
                  key={u.id}
                  user={u}
                  onSetQuota={(gb) =>
                    quotaMutation.mutate({ id: u.id, gb })
                  }
                  pending={quotaMutation.isPending}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function UserRow({
  user,
  onSetQuota,
  pending,
}: {
  user: AdminUserRead;
  onSetQuota: (gb: number | null) => void;
  pending: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [gb, setGb] = useState((user.quota_bytes / 1024 ** 3).toFixed(0));
  return (
    <tr className="border-t border-divider/50">
      <td className="px-4 py-2.5 text-fg">
        <div className="font-medium">{user.display_name || "(no name)"}</div>
        <div className="text-[11px] text-fg-secondary truncate">{user.email}</div>
      </td>
      <td className="px-4 py-2.5 text-fg-secondary">
        <div className="flex flex-wrap gap-1">
          {user.is_superuser && <Badge>superuser</Badge>}
          {!user.is_active && <Badge tone="danger">inactive</Badge>}
          {!user.is_verified && <Badge tone="warning">unverified</Badge>}
        </div>
      </td>
      <td className="px-4 py-2.5 text-right">{formatBytes(user.used_bytes)}</td>
      <td className="px-4 py-2.5 text-right">
        {editing ? (
          <div className="inline-flex items-center gap-1.5">
            <input
              value={gb}
              onChange={(e) => setGb(e.target.value)}
              className="w-20 rounded-lg bg-card border border-border px-2 py-1 text-right text-[12px]"
            />
            <span className="text-fg-secondary text-[11px]">GB</span>
            <button
              disabled={pending}
              onClick={() => {
                const n = Number(gb);
                if (!Number.isFinite(n) || n < 0) {
                  toast.error("Quota must be a positive number");
                  return;
                }
                onSetQuota(n);
                setEditing(false);
              }}
              className="h-7 px-2.5 rounded-full bg-fg text-fg-inverse text-[11px] font-medium disabled:opacity-50"
            >
              Save
            </button>
            <button
              onClick={() => setEditing(false)}
              className="h-7 px-2.5 rounded-full bg-elevated text-fg text-[11px]"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            onClick={() => setEditing(true)}
            className="text-fg-secondary hover:text-accent hover:underline"
          >
            {formatBytes(user.quota_bytes)}
          </button>
        )}
      </td>
    </tr>
  );
}

function AuditTab() {
  const [actionPrefix, setActionPrefix] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "audit", actionPrefix],
    queryFn: () =>
      listAdminAudit({
        limit: 200,
        actionPrefix: actionPrefix || null,
      }),
    refetchOnWindowFocus: false,
  });

  return (
    <div className="space-y-4">
      <input
        value={actionPrefix}
        onChange={(e) => setActionPrefix(e.target.value)}
        placeholder="Filter by action prefix (e.g. 'consent.', 'admin.')…"
        className="input"
      />
      {isLoading && (
        <div className="text-fg-secondary flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      )}
      {data && data.length === 0 && (
        <div className="text-fg-secondary text-[13px] flex items-center gap-2">
          <AlertTriangle className="h-4 w-4" />
          No audit entries match.
        </div>
      )}
      {data && data.length > 0 && (
        <div className="rounded-2xl bg-elevated/50 overflow-hidden">
          <table className="w-full text-[13px]">
            <thead className="bg-elevated text-fg-secondary text-[12px] uppercase tracking-wide">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium">When</th>
                <th className="text-left px-4 py-2.5 font-medium">Action</th>
                <th className="text-left px-4 py-2.5 font-medium">User</th>
                <th className="text-left px-4 py-2.5 font-medium">Details</th>
              </tr>
            </thead>
            <tbody>
              {data.map((entry) => (
                <tr key={entry.id} className="border-t border-divider/50 align-top">
                  <td className="px-4 py-2.5 text-fg-secondary whitespace-nowrap">
                    {relativeTime(entry.created_at)}
                  </td>
                  <td className="px-4 py-2.5 text-fg font-mono text-[12px]">
                    {entry.action}
                  </td>
                  <td className="px-4 py-2.5 text-fg-secondary font-mono text-[11px]">
                    {entry.user_id ? entry.user_id.slice(0, 8) : "—"}
                  </td>
                  <td className="px-4 py-2.5 text-fg-secondary text-[11px] font-mono">
                    {entry.details ? (
                      <code className="block max-w-md truncate">
                        {JSON.stringify(entry.details)}
                      </code>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl bg-elevated px-4 py-3">
      <div className="text-[12px] text-fg-secondary">{label}</div>
      <div className="text-[18px] font-semibold text-fg mt-0.5">{value}</div>
    </div>
  );
}

function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "danger" | "warning";
}) {
  const cls =
    tone === "danger"
      ? "bg-danger/10 text-danger"
      : tone === "warning"
        ? "bg-warning/10 text-warning"
        : "bg-card text-fg-secondary ring-1 ring-divider";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${cls}`}
    >
      {children}
    </span>
  );
}
