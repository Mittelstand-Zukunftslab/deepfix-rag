import { useState } from "react";

import { Button, type ButtonProps } from "@/components/ui/button";
import { authService } from "@/services/auth-service";

/** The app header's sign-out, and the chat sidebar's compact one. */
export function SignOutButton({
  variant = "outline",
  size,
}: Pick<ButtonProps, "variant" | "size"> = {}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const signOut = () => {
    setPending(true);
    setError(null);
    void authService
      .signOut()
      .then(() => {
        window.location.assign("/sign-in");
      })
      .catch(() => {
        setPending(false);
        setError("Unable to sign out. Try again.");
      });
  };

  return (
    <div className="flex items-center gap-3">
      {error ? (
        <p className="text-sm font-medium text-destructive" role="alert">
          {error}
        </p>
      ) : null}
      <Button
        type="button"
        variant={variant}
        size={size}
        className={size ? undefined : "h-11"}
        onClick={signOut}
        aria-busy={pending}
        disabled={pending}
      >
        Sign out
      </Button>
    </div>
  );
}
