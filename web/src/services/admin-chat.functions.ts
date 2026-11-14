import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

import {
  confirmAdminChatAction,
  createAdminChatThread,
  deleteAdminChatThread,
  getAdminChatThread,
  linkAdminChatThreadRun,
  listAdminChatThreadRuns,
  listAdminChatThreads,
  rejectAdminChatAction,
  type ChatActionResponse,
  type ChatThreadDetailResponse,
  type ChatThreadResponse,
  type ChatThreadRunResponse,
  type ChatThreadSummaryResponse,
  type OptimizationRunResponse,
} from "@/client";
import { requireRole } from "@/lib/auth-middleware";
import { unwrap } from "@/services/api-result";

/**
 * The JSON half of the admin chat, as admin-checked server functions.
 *
 * The streaming route is not here: the browser posts to `/api/admin/chat/...`
 * through the AI SDK transport, which the web server's existing `/api` proxy
 * forwards. Every function here is `requireRole("admin", …)` so a member cannot
 * reach a chat route even by calling the generated client directly.
 */

const adminOnly = (action: string) => requireRole("admin", `Only admins can ${action}.`);

const threadIdInput = z.object({ threadId: z.string().min(1) });
const actionIdInput = z.object({ actionId: z.string().min(1) });
const linkInput = z.object({ threadId: z.string().min(1), runId: z.string().min(1) });

export const fetchChatThreads = createServerFn({ method: "GET" })
  .middleware([adminOnly("use Hermes chat")])
  .handler(async ({ context }): Promise<ChatThreadSummaryResponse[]> => {
    return unwrap(await listAdminChatThreads({ headers: context.apiHeaders }));
  });

export const createChatThread = createServerFn({ method: "POST" })
  .middleware([adminOnly("use Hermes chat")])
  .handler(async ({ context }): Promise<ChatThreadResponse> => {
    return unwrap(await createAdminChatThread({ headers: context.apiHeaders }));
  });

export const fetchChatThread = createServerFn({ method: "GET" })
  .middleware([adminOnly("use Hermes chat")])
  .validator(threadIdInput)
  .handler(async ({ context, data }): Promise<ChatThreadDetailResponse> => {
    return unwrap(
      await getAdminChatThread({
        path: { thread_id: data.threadId },
        headers: context.apiHeaders,
      }),
    );
  });

export const deleteChatThread = createServerFn({ method: "POST" })
  .middleware([adminOnly("use Hermes chat")])
  .validator(threadIdInput)
  .handler(async ({ context, data }): Promise<{ deleted: true }> => {
    const result = await deleteAdminChatThread({
      path: { thread_id: data.threadId },
      headers: context.apiHeaders,
    });
    if (result.error) unwrap(result);
    return { deleted: true };
  });

export const confirmChatAction = createServerFn({ method: "POST" })
  .middleware([adminOnly("confirm a Hermes chat action")])
  .validator(actionIdInput)
  .handler(async ({ context, data }): Promise<OptimizationRunResponse> => {
    return unwrap(
      await confirmAdminChatAction({
        path: { action_id: data.actionId },
        headers: context.apiHeaders,
      }),
    );
  });

export const rejectChatAction = createServerFn({ method: "POST" })
  .middleware([adminOnly("dismiss a Hermes chat action")])
  .validator(actionIdInput)
  .handler(async ({ context, data }): Promise<ChatActionResponse> => {
    return unwrap(
      await rejectAdminChatAction({
        path: { action_id: data.actionId },
        headers: context.apiHeaders,
      }),
    );
  });

export const linkChatRun = createServerFn({ method: "POST" })
  .middleware([adminOnly("use Hermes chat")])
  .validator(linkInput)
  .handler(async ({ context, data }): Promise<ChatThreadRunResponse> => {
    return unwrap(
      await linkAdminChatThreadRun({
        path: { thread_id: data.threadId, run_id: data.runId },
        headers: context.apiHeaders,
      }),
    );
  });

export const fetchChatRuns = createServerFn({ method: "GET" })
  .middleware([adminOnly("use Hermes chat")])
  .validator(threadIdInput)
  .handler(async ({ context, data }): Promise<ChatThreadRunResponse[]> => {
    return unwrap(
      await listAdminChatThreadRuns({
        path: { thread_id: data.threadId },
        headers: context.apiHeaders,
      }),
    );
  });
