import { ApiError } from "@/services/apiClient";

/** A message that is safe and useful to show for a failed API call. */
export function errorMessage(error: unknown, fallback = "Something went wrong. Try again."): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return "Too many attempts. Wait a few minutes and try again.";
    return error.message || fallback;
  }
  return fallback;
}
