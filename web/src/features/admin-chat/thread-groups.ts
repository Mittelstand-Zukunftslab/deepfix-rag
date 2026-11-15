import { differenceInCalendarDays, startOfDay } from "date-fns";

import type { ChatThreadSummary } from "@/schemas/admin-chat";

/**
 * The sidebar's date buckets.
 *
 * LibreChat groups conversations by when they were last touched, which is the
 * only ordering cue a reader needs to find yesterday's thread again. The
 * buckets are fixed rather than relative ("3 days ago") so a label never has to
 * be recomputed while the page is open.
 */
export const THREAD_GROUPS = ["Today", "Yesterday", "Previous 7 days", "Older"] as const;

export type ThreadGroupLabel = (typeof THREAD_GROUPS)[number];

export interface ThreadGroup {
  label: ThreadGroupLabel;
  threads: ChatThreadSummary[];
}

function groupOf(updatedAt: string, today: Date): ThreadGroupLabel {
  const updated = new Date(updatedAt);
  if (Number.isNaN(updated.getTime())) return "Older";
  const days = differenceInCalendarDays(today, updated);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  return "Older";
}

/**
 * Buckets threads by `updated_at`, newest first, dropping empty buckets so the
 * list never shows a heading with nothing under it.
 */
export function groupThreadsByDate(
  threads: ChatThreadSummary[],
  now: Date = new Date(),
): ThreadGroup[] {
  const today = startOfDay(now);
  const buckets = new Map<ThreadGroupLabel, ChatThreadSummary[]>();
  const newestFirst = [...threads].sort(
    (left, right) => new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
  );
  for (const thread of newestFirst) {
    const label = groupOf(thread.updated_at, today);
    const bucket = buckets.get(label);
    if (bucket) bucket.push(thread);
    else buckets.set(label, [thread]);
  }
  return THREAD_GROUPS.flatMap((label) => {
    const bucket = buckets.get(label);
    return bucket ? [{ label, threads: bucket }] : [];
  });
}
