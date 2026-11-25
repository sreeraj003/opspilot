import { createContext } from "react";

import { apiClient } from "@/services/apiClient";

/** Injects the API client so components and tests can swap the implementation. */
export const ApiClientContext = createContext(apiClient);
