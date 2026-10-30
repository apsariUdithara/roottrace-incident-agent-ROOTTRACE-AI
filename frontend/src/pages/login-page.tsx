import { useMutation } from '@tanstack/react-query'
import { Link, useLocation, useNavigate } from 'react-router'

import { AuthForm } from '@/components/auth-form'
import { Button } from '@/components/ui/button'
import { demo, login } from '@/lib/auth'
import { errorMessage } from '@/lib/errors'

export function LoginPage() {
  const { search } = useLocation() // keeps ?next= when switching to register

  return (
    <div className="flex flex-col items-center gap-4">
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
          </>
        }
      />
      <TryDemo />
    </div>
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
    <div className="flex flex-col items-center gap-1.5">
      <Button variant="outline" onClick={() => start.mutate()} disabled={start.isPending}>
        Try the demo, no account needed
      </Button>
      {start.isError && (
        <p role="alert" className="text-xs text-destructive">
          {errorMessage(start.error)}
        </p>
      )}
    </div>
  )
}
