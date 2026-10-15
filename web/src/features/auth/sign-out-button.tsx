import { useState } from "react";

import { Button } from "@/components/ui/button";
import { authService } from "@/services/auth-service";

export function SignOutButton() {
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
        variant="outline"
        className="h-11"
        onClick={signOut}
        aria-busy={pending}
        disabled={pending}
      >
        Sign out
      </Button>
    </div>
  );
}
