// Folder modals: create, rename, delete-confirm. Wired to the real
// /folders endpoints; cache-invalidates the right folders query so the
// gallery refreshes immediately on success.
import React, { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import { Modal, ModalClose } from "./primitives.jsx";
import { createFolder, updateFolder, deleteFolder } from "@/api/folders";

export function NewFolderModal({ open, onClose, parentFolderId = null }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();

  useEffect(() => {
    if (open) setName("");
  }, [open]);

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      await createFolder({
        name: trimmed,
        parent_folder_id: parentFolderId,
      });
      toast.success(`Created "${trimmed}"`);
      qc.invalidateQueries({ queryKey: ["folders"] });
      onClose?.();
    } catch (e) {
      toast.error(e?.detail || "Could not create folder");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} labelledBy="newfolder-title">
      <div className="modal__head">
        <h2 id="newfolder-title">
          <span className="modal__head-icon"><Icon name="folderPlus" size={16}/></span>
          New folder
        </h2>
        <p>Folders organize files together. You can drag and drop files in.</p>
        <ModalClose onClose={onClose}/>
      </div>
      <div className="modal__body">
        <div className="label-block" style={{ marginTop: 0 }}>
          <div className="label-block__label">Folder name</div>
          <input
            className="input input--lg"
            autoFocus
            value={name}
            placeholder="e.g. Coast 2026"
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          />
        </div>
      </div>
      <div className="modal__foot">
        <span className="modal__foot-left mono">
          {parentFolderId ? "in this folder" : "at root"}
        </span>
        <div className="modal__foot-actions">
          <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
          <button
            className="btn btn--primary"
            disabled={!name.trim() || busy}
            onClick={submit}
          >
            {busy ? "Creating…" : "Create folder"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function RenameFolderModal({ open, folder, onClose }) {
  const [name, setName] = useState(folder?.name || "");
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();

  useEffect(() => {
    if (open && folder) setName(folder.name || "");
  }, [open, folder]);

  if (!folder) return null;

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed || trimmed === folder.name || busy) {
      onClose?.();
      return;
    }
    setBusy(true);
    try {
      await updateFolder(folder.id, { name: trimmed });
      toast.success("Folder renamed");
      qc.invalidateQueries({ queryKey: ["folders"] });
      onClose?.();
    } catch (e) {
      toast.error(e?.detail || "Could not rename folder");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} labelledBy="renamefolder-title">
      <div className="modal__head">
        <h2 id="renamefolder-title">
          <span className="modal__head-icon"><Icon name="pencil" size={15}/></span>
          Rename folder
        </h2>
        <p className="mono">{folder.name}</p>
        <ModalClose onClose={onClose}/>
      </div>
      <div className="modal__body">
        <div className="label-block" style={{ marginTop: 0 }}>
          <div className="label-block__label">New name</div>
          <input
            className="input input--lg"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          />
        </div>
      </div>
      <div className="modal__foot">
        <span className="modal__foot-left"/>
        <div className="modal__foot-actions">
          <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" disabled={busy} onClick={submit}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function DeleteFolderModal({ open, folder, onClose }) {
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();

  if (!folder) return null;

  const submit = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await deleteFolder(folder.id);
      toast.success(`Deleted "${folder.name}"`);
      qc.invalidateQueries({ queryKey: ["folders"] });
      qc.invalidateQueries({ queryKey: ["files"] });
      onClose?.();
    } catch (e) {
      toast.error(e?.detail || "Could not delete folder");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} labelledBy="deletefolder-title">
      <div className="modal__head">
        <h2 id="deletefolder-title">
          <span className="modal__head-icon modal__head-icon--danger"><Icon name="trash" size={15}/></span>
          Delete folder?
        </h2>
        <p>
          <strong className="mono">{folder.name}</strong> will be moved to trash. Files
          inside the folder are preserved (they go back to the root).
        </p>
        <ModalClose onClose={onClose}/>
      </div>
      <div className="modal__foot">
        <span className="modal__foot-left mono">{folder.item_count || 0} items</span>
        <div className="modal__foot-actions">
          <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
          <button className="btn btn--danger" disabled={busy} onClick={submit}>
            {busy ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
