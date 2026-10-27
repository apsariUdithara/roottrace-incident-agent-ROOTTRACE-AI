import { ApiError } from '@/api/client'

/** What to show when a form's request fails. */
export function errorMessage(error: Error): string {
  if (!(error instanceof ApiError)) return "Can't reach the server. Check your connection and try again."
  // A 422 names the field: "body.password" -> "password: String should have at least 8 ..."
  if (error.fieldErrors.length > 0) {
    return error.fieldErrors
      .map(({ field, message }) => `${field.replace(/^body\./, '')}: ${message}`)
      .join(' · ')
  }
  return error.message // the backend's own words, e.g. "Incorrect email or password"
}
