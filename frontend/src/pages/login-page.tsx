import { useMutation } from '@tanstack/react-query'
import { Link, useLocation, useNavigate } from 'react-router'

import { AuthForm } from '@/components/auth-form'
import { Button } from '@/components/ui/button'
import { demo, login } from '@/lib/auth'
import { errorMessage } from '@/lib/errors'

export function LoginPage() {
  const { search } = useLocation() // keeps ?next= when switching to register

  return (
    <AuthForm
      title="Log in to RootTrace"
      description="Investigate incidents across code, commits, docs and logs."
      submitLabel="Log in"
      passwordAutoComplete="current-password"
      onSubmit={login}
      footer={
        <>
          No account yet?{' '}
          <Link to={{ pathname: '/register', search }} className="text-primary hover:underline">
            Create one
          </Link>
          <TryDemo />
        </>
      }
    />
  )
}

/** For judges and visitors: straight into the demo project, on the Investigations page. */
function TryDemo() {
  const navigate = useNavigate()
  const start = useMutation({
    mutationFn: demo,
    onSuccess: () => navigate('/investigations', { replace: true }),
  })
  return (
    <div className="mt-4 flex flex-col gap-3">
      <div className="flex items-center gap-3 text-[0.8125rem] text-muted-foreground">
        <span className="h-px flex-1 bg-border" />
        or
        <span className="h-px flex-1 bg-border" />
      </div>
      {/* Inside the login form: type="button", so it never submits the form. */}
      <Button type="button" variant="outline" className="w-full" onClick={() => start.mutate()} disabled={start.isPending}>
        Try the demo, no account needed
      </Button>
      {start.isError && (
        <p role="alert" className="text-[0.8125rem] text-destructive">
          {errorMessage(start.error)}
        </p>
      )}
    </div>
  )
}
