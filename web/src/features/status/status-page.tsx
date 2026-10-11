import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, LoaderCircle } from "lucide-react";

import { PageHeader } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { config } from "@/lib/config";
import { healthQueryOptions } from "./status-queries";

export function StatusPage() {
  const { data, isPending, isError, isFetching, refetch } = useQuery(healthQueryOptions());

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-8 py-10">
      <PageHeader
        title="System status"
        description="Checks that this app can reach the DeepFix API."
        actions={
          <Button variant="outline" onClick={() => void refetch()} disabled={isFetching}>
            Check again
          </Button>
        }
      />
      <Card className="flex flex-col gap-3 p-6" aria-live="polite">
        <div className="flex items-center gap-3">
          {isPending ? (
            <LoaderCircle className="size-5 animate-spin text-muted-foreground" aria-hidden />
          ) : isError ? (
            <CircleAlert className="size-5 text-destructive" aria-hidden />
          ) : (
            <CheckCircle2 className="size-5 text-primary" aria-hidden />
          )}
          <span className="text-label-lg text-foreground">DeepFix API</span>
          {data ? <Badge>{data.status}</Badge> : null}
        </div>
        <p className="text-body-md text-muted-foreground">
          {isPending
            ? "Checking the API."
            : isError
              ? `The API at ${config.apiBaseUrl} did not respond. Start it and check again.`
              : `The API at ${config.apiBaseUrl} is up.`}
        </p>
      </Card>
    </div>
  );
}
