import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HistoryDenied, HistoryReviewPage, type ReviewState } from "@/features/admin/history-page";
import { historySearchSchema, type HistoryFilters } from "@/schemas/history";
import type { HistoryEntry, HistoryPage, InteractionReview } from "@/services/history-service";

const defaults: HistoryFilters = { page: 1, feedback: "all" };

function entry(overrides: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    interaction_id: "int-1",
    kind: "ask",
    user_email: "ada@example.com",
    query: "When does the harbor open?",
    answer: "The harbor opens at **six**.",
    error: null,
    config_path: "/workspace/configs/champion.yaml",
    created_at: "2026-10-16T09:30:00.000Z",
    hit_count: 2,
    answer_votes: { up: 0, down: 1 },
    passage_votes: { up: 1, down: 0 },
    comment_count: 0,
    ...overrides,
  };
}

function historyPage(items: HistoryEntry[], total = items.length, offset = 0): HistoryPage {
  return { items, total, limit: 25, offset };
}

const review: InteractionReview = {
  entry: entry(),
  build_id: "build-7",
  trace_id: null,
  answer_comments: [
    {
      vote: "down",
      reason: "incomplete",
      text: "It skips the winter hours.",
      created_at: "2026-10-16T09:31:00.000Z",
    },
  ],
  hits: [
    {
      rank: 1,
      doc_id: "doc-1",
      document_filename: "harbor.txt",
      text: "The harbor opens at six in the morning.",
      score: 0.8123,
      votes: { up: 1, down: 0 },
      comments: [
        { vote: "down", reason: "outdated", text: "", created_at: "2026-10-16T09:32:00.000Z" },
      ],
    },
  ],
};

function renderPage(
  props: Partial<{
    page: HistoryPage;
    filters: HistoryFilters;
    selectedId: string | undefined;
    review: ReviewState;
    onFiltersChange: (next: HistoryFilters) => void;
    onSelect: (id: string | undefined) => void;
  }> = {},
) {
  return render(
    <HistoryReviewPage
      page={props.page ?? historyPage([entry()])}
      filters={props.filters ?? defaults}
      selectedId={props.selectedId}
      review={props.review ?? { loading: false }}
      onFiltersChange={props.onFiltersChange ?? (() => {})}
      onSelect={props.onSelect ?? (() => {})}
    />,
  );
}

describe("history search params", () => {
  it("falls back to safe defaults for unknown values", () => {
    expect(historySearchSchema.parse({})).toEqual({ page: 1, feedback: "all" });
    expect(
      historySearchSchema.parse({ page: "3", kind: "ask", feedback: "negative", q: " harbor " }),
    ).toEqual({ page: 3, kind: "ask", feedback: "negative", q: "harbor" });
    expect(historySearchSchema.parse({ page: "-2", kind: "chat", feedback: "x" })).toEqual({
      page: 1,
      kind: undefined,
      feedback: "all",
    });
  });
});

describe("History screen", () => {
  it("tells members the page is only for admins", () => {
    render(<HistoryDenied />);
    expect(screen.getByRole("heading", { name: "History" })).toBeTruthy();
    expect(screen.getByText("This page is only available to admins.")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("lists who asked what, the answer, and the feedback", () => {
    renderPage({
      page: historyPage([
        entry(),
        entry({
          interaction_id: "int-2",
          kind: "retrieval",
          user_email: "",
          query: "harbor fees",
          answer: null,
          hit_count: 1,
          answer_votes: { up: 0, down: 0 },
          passage_votes: { up: 0, down: 0 },
        }),
        entry({
          interaction_id: "int-3",
          answer: null,
          error: "The model timed out.",
          answer_votes: { up: 0, down: 0 },
          passage_votes: { up: 0, down: 0 },
          comment_count: 2,
        }),
      ]),
    });
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);

    const ask = within(rows[0]!);
    expect(ask.getByText("ada@example.com")).toBeTruthy();
    expect(ask.getByText("Ask")).toBeTruthy();
    expect(ask.getByRole("button", { name: "When does the harbor open?" })).toBeTruthy();
    expect(ask.getByText("The harbor opens at **six**.")).toBeTruthy();
    expect(ask.getByText(/Answer/)).toBeTruthy();
    expect(ask.getAllByLabelText("1 not helpful")).toHaveLength(1);
    expect(ask.getAllByLabelText("1 helpful")).toHaveLength(1);

    const search = within(rows[1]!);
    expect(search.getByText("Unknown")).toBeTruthy();
    expect(search.getByText("Search")).toBeTruthy();
    expect(search.getByText("1 passage shown")).toBeTruthy();
    expect(search.getByText("None")).toBeTruthy();

    expect(ask.queryByText(/comment/)).toBeNull();
    const failed = within(rows[2]!);
    expect(failed.getByText("Failed: The model timed out.")).toBeTruthy();
    expect(failed.getByText("2 comments")).toBeTruthy();
    expect(failed.queryByText("None")).toBeNull();
    expect(screen.getByText("Showing 1–3 of 3")).toBeTruthy();
  });

  it("explains an empty history and an empty filter result differently", () => {
    const view = renderPage({ page: historyPage([]) });
    expect(
      screen.getByText(
        "No searches or questions yet. They appear here once people use Search or Ask.",
      ),
    ).toBeTruthy();
    view.unmount();
    renderPage({ page: historyPage([]), filters: { ...defaults, feedback: "negative" } });
    expect(screen.getByText("Nothing matches these filters.")).toBeTruthy();
    expect(screen.getByText("No results")).toBeTruthy();
  });

  it("resets to the first page whenever a filter changes", () => {
    const onFiltersChange = vi.fn();
    renderPage({ filters: { ...defaults, page: 3 }, onFiltersChange });

    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "retrieval" } });
    expect(onFiltersChange).toHaveBeenLastCalledWith({
      page: 1,
      feedback: "all",
      kind: "retrieval",
    });

    fireEvent.change(screen.getByLabelText("Feedback"), { target: { value: "negative" } });
    expect(onFiltersChange).toHaveBeenLastCalledWith({ page: 1, feedback: "negative" });
    fireEvent.change(screen.getByLabelText("Feedback"), { target: { value: "commented" } });
    expect(onFiltersChange).toHaveBeenLastCalledWith({ page: 1, feedback: "commented" });

    fireEvent.change(screen.getByLabelText("Question or person"), {
      target: { value: "  bea@example.com " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(onFiltersChange).toHaveBeenLastCalledWith({
      page: 1,
      feedback: "all",
      q: "bea@example.com",
    });
  });

  it("pages forward and back within the total", () => {
    const onFiltersChange = vi.fn();
    const items = Array.from({ length: 25 }, (_, index) =>
      entry({ interaction_id: `int-${index}` }),
    );
    const view = renderPage({ page: historyPage(items, 30), onFiltersChange });
    expect(screen.getByText("Showing 1–25 of 30")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onFiltersChange).toHaveBeenLastCalledWith({ page: 2, feedback: "all" });

    view.rerender(
      <HistoryReviewPage
        page={historyPage(items.slice(0, 5), 30, 25)}
        filters={{ ...defaults, page: 2 }}
        selectedId={undefined}
        review={{ loading: false }}
        onFiltersChange={onFiltersChange}
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText("Showing 26–30 of 30")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(onFiltersChange).toHaveBeenLastCalledWith({ page: 1, feedback: "all" });
  });

  it("opens an entry and shows the answer and every passage with its votes", () => {
    const onSelect = vi.fn();
    const view = renderPage({ onSelect });
    fireEvent.click(screen.getByRole("button", { name: "When does the harbor open?" }));
    expect(onSelect).toHaveBeenCalledWith("int-1");

    view.rerender(
      <HistoryReviewPage
        page={historyPage([entry()])}
        filters={defaults}
        selectedId="int-1"
        review={{ data: review, loading: false }}
        onFiltersChange={() => {}}
        onSelect={onSelect}
      />,
    );
    const panel = screen.getByRole("dialog");
    expect(within(panel).getByRole("heading", { name: "When does the harbor open?" })).toBeTruthy();
    expect(within(panel).getByText("six").tagName).toBe("STRONG");
    expect(within(panel).getByText("1. harbor.txt")).toBeTruthy();
    expect(within(panel).getByText("Score 0.812")).toBeTruthy();
    expect(within(panel).getByText("The harbor opens at six in the morning.")).toBeTruthy();
    expect(within(panel).getByText(/build build-7/)).toBeTruthy();
    const [answerComments, passageComments] = within(panel).getAllByRole("list", {
      name: "Comments",
    });
    expect(within(answerComments!).getByText("Incomplete")).toBeTruthy();
    expect(within(answerComments!).getByText("It skips the winter hours.")).toBeTruthy();
    expect(within(answerComments!).getByLabelText("Not helpful")).toBeTruthy();
    expect(within(passageComments!).getByText("Outdated")).toBeTruthy();

    fireEvent.click(within(panel).getByRole("button", { name: "Close" }));
    expect(onSelect).toHaveBeenLastCalledWith(undefined);
  });

  it("shows loading and errors in the detail panel", () => {
    const view = renderPage({ selectedId: "int-1", review: { loading: true } });
    expect(within(screen.getByRole("dialog")).getByRole("status")).toHaveTextContent(
      "Loading this entry.",
    );
    view.unmount();
    renderPage({
      selectedId: "missing",
      review: { loading: false, error: "No interaction with that id." },
    });
    expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent(
      "No interaction with that id.",
    );
  });
});
