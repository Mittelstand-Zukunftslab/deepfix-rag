import { AuthLayout } from "./auth-layout";
import { CredentialsForm } from "./credentials-form";
import { authService } from "@/services/auth-service";

export function RegisterPage() {
  return (
    <AuthLayout>
      <CredentialsForm
        title="Create account"
        description="You'll be a member of this workspace."
        submitLabel="Create account"
        passwordAutoComplete="new-password"
        alternate={{ href: "/sign-in", label: "Sign in", prompt: "Already have an account?" }}
        onSubmit={async (values) => {
          await authService.register(values);
          window.location.assign("/sign-in?created=1");
        }}
      />
    </AuthLayout>
  );
}
