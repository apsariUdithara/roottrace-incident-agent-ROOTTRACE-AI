import { ApiError } from '@/api/client'

/** What to show when a form's request fails. */
export function errorMessage(error: Error): string {
  if (!(error instanceof ApiError)) return "Can't reach the server. Check your connection and try again."
  // A 422 names the field: "body.password" -> "password: String should have at least 8 ...".
  // A check across fields names only where it was: "query" -> "since must not be after until".
  if (error.fieldErrors.length > 0) {
    return error.fieldErrors
      .map(({ field, message }) => {
        const name = field.replace(/^(body|query)(\.|$)/, '')
        const text = message.replace(/^Value error, /, '')
        return name ? `${name}: ${text}` : text
      })
      .join(' · ')
  }
  return error.message // the backend's own words, e.g. "Incorrect email or password"
}
