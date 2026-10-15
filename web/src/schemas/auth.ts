import { z } from "zod";

export type AccountRole = "admin" | "member";

export type Account = {
  id: string;
  email: string;
  role: AccountRole;
};

export const credentialsSchema = z.object({
  email: z.string().trim().min(1, "Enter an email address.").email("Enter a valid email address."),
  password: z.string().min(8, "Use at least 8 characters."),
});

export type Credentials = z.infer<typeof credentialsSchema>;
