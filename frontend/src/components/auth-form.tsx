import { useMutation } from '@tanstack/react-query'
import type { FormEvent, ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router'

import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { safeNext } from '@/lib/auth'
import { errorMessage } from '@/lib/errors'

type Credentials = { email: string; password: string }

type AuthFormProps = {
  title: string
  description: string
  submitLabel: string
  /** "new-password" lets password managers offer a strong one; it also asks for 8+ chars. */
  passwordAutoComplete: 'current-password' | 'new-password'
  onSubmit: (email: string, password: string) => Promise<void>
  footer: ReactNode
}

/** The login and register form: on success it goes on to `?next=`, or home. */
export function AuthForm({
  title,
  description,
  submitLabel,
  passwordAutoComplete,
  onSubmit,
  footer,
}: AuthFormProps) {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const isNew = passwordAutoComplete === 'new-password'
  const submit = useMutation({
    mutationFn: ({ email, password }: Credentials) => onSubmit(email, password),
    onSuccess: () => navigate(safeNext(searchParams.get('next')), { replace: true }),
  })

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    submit.mutate({ email: String(form.get('email')), password: String(form.get('password')) })
  }

  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <form onSubmit={handleSubmit}>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" name="email" type="email" autoComplete="email" required />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete={passwordAutoComplete}
                minLength={isNew ? 8 : undefined}
                maxLength={128}
                required
                aria-describedby={isNew ? 'password-hint' : undefined}
              />
              {isNew && (
                <p id="password-hint" className="text-xs text-muted-foreground">
                  At least 8 characters.
                </p>
              )}
            </div>
            {submit.error && (
              <p role="alert" className="text-xs text-destructive">
                {errorMessage(submit.error)}
              </p>
            )}
          </CardContent>
          <CardFooter className="mt-4 flex flex-col items-stretch gap-3">
            <Button type="submit" disabled={submit.isPending}>
              {submit.isPending ? 'Please wait…' : submitLabel}
            </Button>
            <div className="text-center text-xs text-muted-foreground">{footer}</div>
          </CardFooter>
        </form>
      </Card>
    </div>
  )
}
