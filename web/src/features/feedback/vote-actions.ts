export type Vote = "up" | "down" | null;

export function nextVoteAction(current: Vote, choice: "up" | "down"): "up" | "down" | "clear" {
  return current === choice ? "clear" : choice;
}
