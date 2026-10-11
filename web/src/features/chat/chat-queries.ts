import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import type { ChatMessage } from "@/schemas/chat";
import { chatService } from "@/services/chat-service";

export const conversationQueryOptions = () =>
  queryOptions({ queryKey: ["chat", "conversation"], queryFn: chatService.getConversation });

export function useSendMessage() {
  const queryClient = useQueryClient();
  const key = conversationQueryOptions().queryKey;
  return useMutation({
    mutationFn: chatService.sendMessage,
    onMutate: ({ content }) => {
      const userMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content,
        createdAt: new Date().toISOString(),
      };
      queryClient.setQueryData<ChatMessage[]>(key, (prev = []) => [...prev, userMessage]);
    },
    onSuccess: (reply) => {
      queryClient.setQueryData<ChatMessage[]>(key, (prev = []) => [...prev, reply]);
    },
  });
}

export function useResetConversation() {
  const queryClient = useQueryClient();
  return () => queryClient.setQueryData<ChatMessage[]>(conversationQueryOptions().queryKey, []);
}
