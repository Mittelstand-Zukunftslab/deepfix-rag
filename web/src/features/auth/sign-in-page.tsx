import { AuthLayout } from "./auth-layout";
import { CredentialsForm } from "./credentials-form";
import { authService } from "@/services/auth-service";

export function SignInPage({ notice }: { notice?: string | null }) {
  return (
    <AuthLayout>
      <CredentialsForm
        title="Sign in"
        description="Use your email and password."
        submitLabel="Sign in"
        passwordAutoComplete="current-password"
        notice={notice}
        alternate={{ href: "/register", label: "Create account", prompt: "New here?" }}
        onSubmit={async (values) => {
          await authService.signIn(values);
          window.location.assign("/search");
        }}
      />
    </AuthLayout>
  );
}
