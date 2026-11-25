import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useApiClient } from "@/hooks/useApiClient";
import * as users from "@/services/userService";
import type { UserCreateInput, UserListParams, UserUpdateInput } from "@/types/auth";

export const userKeys = {
  all: ["users"] as const,
  list: (params: UserListParams) => [...userKeys.all, "list", params] as const,
  detail: (id: string) => [...userKeys.all, "detail", id] as const,
  sessions: (id: string) => [...userKeys.all, "sessions", id] as const,
};

export function useUserList(params: UserListParams) {
  const client = useApiClient();
  return useQuery({
    queryKey: userKeys.list(params),
    queryFn: ({ signal }) => users.listUsers(client, params, signal),
    placeholderData: keepPreviousData,
  });
}

export function useUserDetail(id: string) {
  const client = useApiClient();
  return useQuery({ queryKey: userKeys.detail(id), queryFn: ({ signal }) => users.getUser(client, id, signal) });
}

export function useUserSessions(id: string) {
  const client = useApiClient();
  return useQuery({ queryKey: userKeys.sessions(id), queryFn: ({ signal }) => users.listUserSessions(client, id, signal) });
}

/** Every user mutation refreshes all cached user data afterwards. */
function useUserMutation<TVariables, TResult>(fn: (variables: TVariables) => Promise<TResult>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => queryClient.invalidateQueries({ queryKey: userKeys.all }),
  });
}

export function useCreateUser() {
  const client = useApiClient();
  return useUserMutation((input: UserCreateInput) => users.createUser(client, input));
}

export function useUpdateUser(id: string) {
  const client = useApiClient();
  return useUserMutation((input: UserUpdateInput) => users.updateUser(client, id, input));
}

export function useSetUserActive() {
  const client = useApiClient();
  return useUserMutation(({ id, active }: { id: string; active: boolean }) =>
    active ? users.activateUser(client, id) : users.deactivateUser(client, id),
  );
}

export function useRevokeUserSessions() {
  const client = useApiClient();
  return useUserMutation((id: string) => users.revokeUserSessions(client, id));
}

export function useStartPasswordReset() {
  const client = useApiClient();
  return useUserMutation((id: string) => users.startPasswordReset(client, id));
}

export function useDeleteUser() {
  const client = useApiClient();
  return useUserMutation((id: string) => users.deleteUser(client, id));
}
