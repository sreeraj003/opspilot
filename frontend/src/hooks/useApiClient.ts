import { createContext, useContext } from "react";

import { apiClient, type ApiClient } from "@/services/apiClient";

/** Injects the API client so components and tests can swap the implementation. */
export const ApiClientContext = createContext<ApiClient>(apiClient);

export function useApiClient(): ApiClient {
  return useContext(ApiClientContext);
}
