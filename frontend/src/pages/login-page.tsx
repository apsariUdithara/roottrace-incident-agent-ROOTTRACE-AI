import { Link, useLocation } from 'react-router'

import { AuthForm } from '@/components/auth-form'
import { login } from '@/lib/auth'

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
        </>
      }
    />
  )
}
