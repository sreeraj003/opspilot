import { z } from "zod";

/** Mirrors the server's policy for instant feedback; the server remains the authority. */
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 256;

export const emailSchema = z
  .string()
  .trim()
  .min(1, "Enter your email address.")
  .pipe(z.email("Enter a valid email address."));

export const newPasswordSchema = z
  .string()
  .min(PASSWORD_MIN_LENGTH, `Use at least ${PASSWORD_MIN_LENGTH} characters.`)
  .max(PASSWORD_MAX_LENGTH, `Use at most ${PASSWORD_MAX_LENGTH} characters.`);

export const PASSWORD_HINT = `At least ${PASSWORD_MIN_LENGTH} characters. Long passphrases are encouraged; any characters are allowed.`;
