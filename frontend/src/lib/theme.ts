// Light or dark, remembered per browser. index.html applies the saved choice before the first
// paint; this switches it.
import { useSyncExternalStore } from 'react'

export type Theme = 'light' | 'dark'
const KEY = 'roottrace.theme'
const listeners = new Set<() => void>()

const current = (): Theme => (document.documentElement.classList.contains('dark') ? 'dark' : 'light')

export function setTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  try {
    localStorage.setItem(KEY, theme)
  } catch {
    // storage blocked: it still switches, just isn't remembered
  }
  listeners.forEach((listener) => listener())
}

export function useTheme(): Theme {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    current,
    () => 'light',
  )
}
