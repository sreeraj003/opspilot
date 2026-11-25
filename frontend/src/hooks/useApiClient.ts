import { useContext } from "react";

import { ApiClientContext } from "@/hooks/apiClientContext";
import type { ApiClient } from "@/services/apiClient";

export function useApiClient(): ApiClient {
  return useContext(ApiClientContext);
}
