import { useState, type RefObject } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

/** The composer: a labelled textarea, Send, and Stop while a reply streams. */
export function ChatComposer({
  busy,
  disabled,
  inputRef,
  onSend,
  onStop,
}: {
  busy: boolean;
  disabled: boolean;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onSend: (content: string) => void;
  onStop: () => void;
}) {
  const [value, setValue] = useState("");

  const submit = () => {
    const content = value.trim();
    if (!content || busy) return;
    setValue("");
    onSend(content);
  };

  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label htmlFor="admin-chat-message" className="text-label-sm text-muted-foreground">
        Message the assistant
      </label>
      <Textarea
        id="admin-chat-message"
        ref={inputRef}
        value={value}
        disabled={disabled}
        rows={3}
        placeholder={disabled ? "Start a thread to send a message." : "Ask about this server…"}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <div className="flex items-center gap-2">
        <Button type="submit" disabled={busy || disabled}>
          Send
        </Button>
        {busy ? (
          <Button type="button" variant="outline" onClick={onStop} aria-label="Stop the reply">
            Stop
          </Button>
        ) : null}
      </div>
    </form>
  );
}
