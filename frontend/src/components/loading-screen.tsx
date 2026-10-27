/** Shown while a page's loader runs on first load, e.g. while a reload restores the session. */
export function LoadingScreen() {
  return (
    <div className="flex min-h-svh items-center justify-center text-sm text-muted-foreground">
      Loading…
    </div>
  )
}
