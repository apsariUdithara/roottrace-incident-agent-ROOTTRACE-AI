import { Link } from 'react-router'

import { buttonVariants } from '@/components/ui/button'

export function NotFoundPage() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
      <h1 className="font-heading text-2xl font-semibold">Page not found</h1>
      <Link to="/" className={buttonVariants({ variant: 'outline' })}>
        Back to home
      </Link>
    </div>
  )
}
