import { useCallback, useRef, useState } from "react";
import { describeError } from "../api/errors";
import type { Attachment } from "../api/types";
import type { Api } from "../api/endpoints";

export type UploadState = "waiting" | "uploading" | "done" | "failed";

export interface UploadItem {
  key: string;
  file: File;
  state: UploadState;
  /** Bytes sent so far, as the browser reports them. */
  loaded: number;
  error: string | null;
  /** What the server made of it, once it is there. */
  attachment: Attachment | null;
}

let counter = 0;

/**
 * The files a person has chosen for a submission, and what became of each: waiting (a form that is not sent yet), uploading
 * with its progress, done with what the server found the file to be, or failed with the reason and a way to try again.
 * One file at a time: a failure of one never stops the others.
 */
export function useUploads(api: Api, submissionId: string | null) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const itemsRef = useRef<UploadItem[]>([]);
  const busy = useRef(false);

  const update = useCallback((next: UploadItem[]) => {
    itemsRef.current = next;
    setItems(next);
  }, []);

  const patch = useCallback(
    (key: string, change: Partial<UploadItem>) => {
      update(itemsRef.current.map((i) => (i.key === key ? { ...i, ...change } : i)));
    },
    [update],
  );

  const sendOne = useCallback(
    async (item: UploadItem, target: string): Promise<void> => {
      patch(item.key, { state: "uploading", loaded: 0, error: null });
      try {
        const attachment = await api.uploadAttachment(target, item.file, {
          onProgress: (loaded) => patch(item.key, { loaded }),
        });
        patch(item.key, { state: "done", loaded: item.file.size, attachment, error: null });
      } catch (error) {
        patch(item.key, { state: "failed", error: describeError(error) });
      }
    },
    [api, patch],
  );

  /** Send everything that is waiting or failed to `target` (the submission), one at a time. Resolves with the counts. */
  const sendAll = useCallback(
    async (target: string): Promise<{ done: number; failed: number }> => {
      if (busy.current) return { done: 0, failed: 0 };
      busy.current = true;
      try {
        for (const item of itemsRef.current.filter((i) => i.state === "waiting" || i.state === "failed")) {
          await sendOne(item, target);
        }
      } finally {
        busy.current = false;
      }
      const all = itemsRef.current;
      return { done: all.filter((i) => i.state === "done").length, failed: all.filter((i) => i.state === "failed").length };
    },
    [sendOne],
  );

  const add = useCallback(
    (files: File[]) => {
      const added = files.map<UploadItem>((file) => ({
        key: `upload-${++counter}`,
        file,
        state: "waiting",
        loaded: 0,
        error: null,
        attachment: null,
      }));
      update([...itemsRef.current, ...added]);
      if (submissionId) {
        // a submission that already exists: the file goes at once
        void (async () => {
          for (const item of added) await sendOne(item, submissionId);
        })();
      }
    },
    [submissionId, sendOne, update],
  );

  const retry = useCallback(
    (key: string, target?: string) => {
      const item = itemsRef.current.find((i) => i.key === key);
      const where = target ?? submissionId;
      if (item && where) void sendOne(item, where);
    },
    [sendOne, submissionId],
  );

  /** Take a file out: one that was already sent is removed from the submission too. */
  const remove = useCallback(
    async (key: string, target?: string): Promise<void> => {
      const item = itemsRef.current.find((i) => i.key === key);
      if (!item) return;
      const where = target ?? submissionId;
      if (item.attachment && where) {
        try {
          await api.deleteAttachment(where, item.attachment.id);
        } catch (error) {
          patch(key, { error: describeError(error) });
          return;
        }
      }
      update(itemsRef.current.filter((i) => i.key !== key));
    },
    [api, patch, submissionId, update],
  );

  /** Drop finished files from the list without touching the submission (they are shown with the submission's own files). */
  const forget = useCallback(
    (keys: string[]) => update(itemsRef.current.filter((i) => !keys.includes(i.key))),
    [update],
  );

  const reset = useCallback(() => update([]), [update]);
  return { items, add, retry, remove, forget, sendAll, reset };
}
