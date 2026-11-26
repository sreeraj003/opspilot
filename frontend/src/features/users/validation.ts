import { z } from "zod";

import { emailSchema } from "@/features/auth/validation";

/** Mirrors the server's username rule for instant feedback; the server remains the authority. */
export const usernameSchema = z
  .string()
  .trim()
  .refine((value) => value === "" || /^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$/.test(value), {
    message: "3-64 letters, digits, '.', '_' or '-', starting with a letter or digit.",
  });

export const fullNameSchema = z.string().trim().min(1, "Enter the person's name.").max(200, "Use at most 200 characters.");

/** Fields an administrator may edit on an existing user. */
export const userDetailsSchema = z.object({ full_name: fullNameSchema, email: emailSchema, username: usernameSchema });
export type UserDetailsValues = z.infer<typeof userDetailsSchema>;
