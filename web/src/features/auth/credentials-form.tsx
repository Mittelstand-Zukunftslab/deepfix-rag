import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, type FieldErrors } from "react-hook-form";

import { NavLink, type AppPath } from "@/components/common/nav-link";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { credentialsSchema, type Credentials } from "@/schemas/auth";
import { AuthRequestError } from "@/services/auth-service";

interface CredentialsFormProps {
  title: string;
  description: string;
  submitLabel: string;
  passwordAutoComplete: "current-password" | "new-password";
  notice?: string | null;
  alternate: { href: AppPath; label: string; prompt: string };
  onSubmit: (values: Credentials) => Promise<void>;
}

export function CredentialsForm({
  title,
  description,
  submitLabel,
  passwordAutoComplete,
  notice,
  alternate,
  onSubmit,
}: CredentialsFormProps) {
  const form = useForm<Credentials>({
    resolver: zodResolver(credentialsSchema),
    defaultValues: { email: "", password: "" },
  });

  const focusFirstInvalid = (errors: FieldErrors<Credentials>) => {
    if (errors.email) form.setFocus("email");
    else if (errors.password) form.setFocus("password");
  };

  const submit = async (values: Credentials) => {
    form.clearErrors();
    try {
      await onSubmit(values);
    } catch (error) {
      if (error instanceof AuthRequestError && error.field !== "form") {
        form.setError(error.field, { message: error.message });
        form.setFocus(error.field);
        return;
      }
      const message =
        error instanceof Error ? error.message : "Unable to reach the server. Try again.";
      form.setError("root", { message });
    }
  };

  const rootError = form.formState.errors.root?.message;

  return (
    <Card className="flex flex-col gap-6 p-6 shadow-soft">
      <div className="flex flex-col gap-2">
        <h1 className="text-headline-xl text-foreground">{title}</h1>
        <p className="text-body-md text-muted-foreground">{description}</p>
      </div>
      {notice ? (
        <p className="text-sm text-foreground" role="status">
          {notice}
        </p>
      ) : null}
      {rootError ? (
        <p className="text-sm font-medium text-destructive" role="alert">
          {rootError}
        </p>
      ) : null}
      <Form {...form}>
        <form
          className="flex flex-col gap-5"
          noValidate
          onSubmit={form.handleSubmit(submit, focusFirstInvalid)}
        >
          <FormField
            control={form.control}
            name="email"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Email</FormLabel>
                <FormControl>
                  <Input
                    {...field}
                    type="email"
                    name="email"
                    autoComplete="email"
                    inputMode="email"
                    spellCheck={false}
                    className="h-11 text-base md:text-base"
                  />
                </FormControl>
                <FormMessage role="alert" />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Password</FormLabel>
                <FormControl>
                  <Input
                    {...field}
                    type="password"
                    name="password"
                    autoComplete={passwordAutoComplete}
                    className="h-11 text-base md:text-base"
                  />
                </FormControl>
                <FormMessage role="alert" />
              </FormItem>
            )}
          />
          <Button
            type="submit"
            className="h-11 w-full text-base"
            aria-busy={form.formState.isSubmitting}
            disabled={form.formState.isSubmitting}
          >
            {submitLabel}
          </Button>
        </form>
      </Form>
      <p className="text-sm text-muted-foreground">
        {alternate.prompt}{" "}
        <NavLink to={alternate.href} className="font-medium text-primary hover:underline">
          {alternate.label}
        </NavLink>
      </p>
    </Card>
  );
}
