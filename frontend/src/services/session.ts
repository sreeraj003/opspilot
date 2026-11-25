import { apiClient } from "@/services/apiClient";
import { AuthSession } from "@/services/authSession";

/** The application's single auth session, wired into the shared API client. */
export const authSession = new AuthSession(apiClient);
