const UNREACHABLE = "Unable to reach the server. Try again.";

function errorDetail(error: unknown): string {
  if (
    error &&
    typeof error === "object" &&
    "detail" in error &&
    typeof error.detail === "string" &&
    error.detail.length > 0
  ) {
    return error.detail;
  }
  return UNREACHABLE;
}

/** The data of a generated-client call, or an Error carrying FastAPI's detail. */
export function unwrap<T>(result: { data?: T; error?: unknown }): T {
  if (result.error || result.data === undefined) throw new Error(errorDetail(result.error));
  return result.data;
}

export function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : UNREACHABLE;
}
