import { Link, useLocation } from 'react-router'

import { AuthForm } from '@/components/auth-form'
import { register } from '@/lib/auth'

export function RegisterPage() {
  const { search } = useLocation() // keeps ?next= when switching to log in

  return (
    <AuthForm
      title="Create an account"
      description="Your projects and investigations are only visible to you."
      submitLabel="Create account"
      passwordAutoComplete="new-password"
      onSubmit={register}
      footer={
        <>
          Already have an account?{' '}
          <Link to={{ pathname: '/login', search }} className="text-primary hover:underline">
            Log in
          </Link>
        </>
      }
    />
  )
}
