import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { FeedbackComment } from "@/features/feedback/feedback-comment";
import { useFeedbackComments } from "@/features/feedback/use-feedback-comments";
import { nextVoteAction, type Vote } from "@/features/feedback/vote-actions";
import { VoteButtons } from "@/features/feedback/vote-buttons";
import type { CommentDraft, FeedbackTarget } from "@/schemas/feedback";

type Send = (key: string, draft: CommentDraft) => Promise<void>;

const ITEMS: { key: string; target: FeedbackTarget; name: string }[] = [
  { key: "answer", target: "answer", name: "answer" },
  { key: "p1", target: "passage", name: "passage 1" },
];

function Harness({ onSend }: { onSend: Send }) {
  const comments = useFeedbackComments();
  const [votes, setVotes] = useState<Record<string, Vote>>({});
  return (
    <>
      {ITEMS.map((item) => {
        const vote = votes[item.key] ?? null;
        return (
          <section key={item.key} aria-label={item.name}>
            <VoteButtons
              vote={vote}
              upLabel={`Like ${item.name}`}
              downLabel={`Dislike ${item.name}`}
              onAction={(action) => {
                const next = action === "clear" ? null : action;
                setVotes((current) => ({ ...current, [item.key]: next }));
                comments.voted(item.key, item.target, next);
              }}
            />
            <FeedbackComment
              target={item.target}
              vote={vote}
              slot={comments.slot(item.key)}
              onOpen={() => comments.open(item.key)}
              onChange={(draft) => comments.change(item.key, draft)}
              onSkip={() => comments.skip(item.key)}
              onSend={async (draft) => {
                await onSend(item.key, draft);
                comments.markSent(item.key);
              }}
            />
          </section>
        );
      })}
    </>
  );
}

function setup(onSend: Send = async () => {}) {
  render(<Harness onSend={onSend} />);
  return {
    answer: within(screen.getByRole("region", { name: "answer" })),
    passage: within(screen.getByRole("region", { name: "passage 1" })),
  };
}

describe("vote actions", () => {
  it("clears a vote when the same thumb is pressed again", () => {
    expect(nextVoteAction("up", "up")).toBe("clear");
    expect(nextVoteAction("up", "down")).toBe("down");
    expect(nextVoteAction(null, "down")).toBe("down");
  });
});

describe("comment after a vote", () => {
  it("opens with reasons right after a thumbs-down and leaves focus on the thumb", () => {
    const { answer } = setup();
    expect(answer.queryByRole("form")).toBeNull();
    const thumb = answer.getByRole("button", { name: "Dislike answer" });
    thumb.focus();
    fireEvent.click(thumb);
    expect(thumb.className).toContain("bg-destructive");

    const form = answer.getByRole("form", { name: "Tell us what was wrong" });
    expect(document.activeElement).toBe(thumb);
    expect(
      within(form)
        .getAllByRole("button", { pressed: false })
        .map((button) => button.textContent),
    ).toEqual(["Wrong", "Incomplete", "Didn't use my documents", "Other"]);
    expect(within(form).getByText("Only admins can read this.")).toBeTruthy();
    expect(within(form).getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("offers a single text field after a thumbs-up, and passages get their own reasons", () => {
    const { answer, passage } = setup();
    fireEvent.click(answer.getByRole("button", { name: "Like answer" }));
    const praise = answer.getByRole("form", { name: "Add a comment" });
    expect(within(praise).getByLabelText("What made it helpful? (optional)")).toBeTruthy();
    expect(within(praise).queryByRole("group")).toBeNull();

    fireEvent.click(passage.getByRole("button", { name: "Dislike passage 1" }));
    const reasons = within(passage.getByRole("group", { name: "What was wrong?" }));
    expect(reasons.getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Not relevant",
      "Outdated",
      "Other",
    ]);
  });

  it("sends a reason and text, then thanks the user without showing the comment again", async () => {
    const onSend = vi.fn<Send>(async () => {});
    const { answer } = setup(onSend);
    fireEvent.click(answer.getByRole("button", { name: "Dislike answer" }));
    fireEvent.click(answer.getByRole("button", { name: "Incomplete" }));
    expect(answer.getByRole("button", { name: "Incomplete" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.change(answer.getByLabelText("Anything else? (optional)"), {
      target: { value: "  Misses the 2025 budget.  " },
    });
    fireEvent.keyDown(answer.getByLabelText("Anything else? (optional)"), {
      key: "Enter",
      ctrlKey: true,
    });

    const thanks = await answer.findByText("Thanks, your comment was sent.");
    expect(onSend).toHaveBeenCalledWith("answer", {
      reason: "incomplete",
      text: "Misses the 2025 budget.",
    });
    expect(answer.queryByRole("form")).toBeNull();
    expect(answer.queryByText(/Misses the 2025 budget/)).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(thanks));
  });

  it("closes on Skip or Escape and keeps the vote", async () => {
    const { answer } = setup();
    fireEvent.click(answer.getByRole("button", { name: "Dislike answer" }));
    fireEvent.click(answer.getByRole("button", { name: "Skip" }));
    expect(answer.queryByRole("form")).toBeNull();
    expect(answer.getByRole("button", { name: "Dislike answer" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const add = answer.getByRole("button", { name: "Add a comment" });
    await waitFor(() => expect(document.activeElement).toBe(add));

    fireEvent.click(add);
    await waitFor(() =>
      expect(document.activeElement).toBe(answer.getByRole("button", { name: "Wrong" })),
    );
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(answer.queryByRole("form")).toBeNull();
  });

  it("keeps one form open and holds unsent text for the item that was closed", () => {
    const { answer, passage } = setup();
    fireEvent.click(answer.getByRole("button", { name: "Dislike answer" }));
    fireEvent.change(answer.getByLabelText("Anything else? (optional)"), {
      target: { value: "Cites the wrong year" },
    });
    fireEvent.click(passage.getByRole("button", { name: "Dislike passage 1" }));

    expect(screen.getAllByRole("form")).toHaveLength(1);
    expect(passage.getByRole("form")).toBeTruthy();
    fireEvent.click(answer.getByRole("button", { name: "Comment not sent · Continue" }));
    expect(answer.getByLabelText("Anything else? (optional)")).toHaveValue("Cites the wrong year");
    expect(answer.getByRole("form")).toContainElement(document.activeElement as HTMLElement);
    expect(passage.queryByRole("form")).toBeNull();
  });

  it("closes when the vote is cleared and drops a reason that no longer fits", () => {
    const { answer } = setup();
    fireEvent.click(answer.getByRole("button", { name: "Dislike answer" }));
    fireEvent.click(answer.getByRole("button", { name: "Wrong" }));
    fireEvent.change(answer.getByLabelText("Anything else? (optional)"), {
      target: { value: "Actually fine" },
    });
    fireEvent.click(answer.getByRole("button", { name: "Like answer" }));
    expect(answer.getByLabelText("What made it helpful? (optional)")).toHaveValue("Actually fine");
    fireEvent.click(answer.getByRole("button", { name: "Dislike answer" }));
    expect(answer.getByRole("button", { name: "Wrong" })).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(answer.getByRole("button", { name: "Dislike answer" }));
    expect(answer.queryByRole("form")).toBeNull();
    expect(answer.queryByRole("button", { name: /comment/i })).toBeNull();
  });

  it("keeps the text and offers a retry when sending fails", async () => {
    const onSend = vi
      .fn<Send>()
      .mockRejectedValueOnce(new Error("Unable to reach the server. Try again."))
      .mockResolvedValueOnce(undefined);
    const { passage } = setup(onSend);
    fireEvent.click(passage.getByRole("button", { name: "Like passage 1" }));
    fireEvent.change(passage.getByLabelText("What made it helpful? (optional)"), {
      target: { value: "Exactly the clause I needed" },
    });
    fireEvent.click(passage.getByRole("button", { name: "Send" }));

    expect(await passage.findByRole("alert")).toHaveTextContent(
      "Unable to reach the server. Try again.",
    );
    expect(passage.getByLabelText("What made it helpful? (optional)")).toHaveValue(
      "Exactly the clause I needed",
    );
    expect(passage.getByRole("button", { name: "Like passage 1" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await act(async () => {
      fireEvent.click(passage.getByRole("button", { name: "Try again" }));
    });
    expect(await passage.findByText("Thanks, your comment was sent.")).toBeTruthy();
    expect(onSend).toHaveBeenCalledTimes(2);
  });

  it("shows how many characters are left only near the limit", () => {
    const { answer } = setup();
    fireEvent.click(answer.getByRole("button", { name: "Dislike answer" }));
    const field = answer.getByLabelText("Anything else? (optional)");
    expect(field).toHaveAttribute("maxLength", "1000");
    fireEvent.change(field, { target: { value: "x".repeat(800) } });
    expect(answer.queryByText(/characters left/)).toBeNull();
    fireEvent.change(field, { target: { value: "x".repeat(950) } });
    expect(answer.getByText("50 characters left")).toBeTruthy();
  });
});
