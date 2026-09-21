import { useEffect, useState } from 'react'

/**
 * The value, held back until it has stopped changing for `delayMs`.
 *
 * Typing `11104` is five renders and would be five searches; the query key is built from
 * what this returns, so it becomes one. The keystrokes still land in the input immediately —
 * only the request waits.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value)

  useEffect(() => {
    const timer = setTimeout(() => {
      setSettled(value)
    }, delayMs)

    return () => {
      clearTimeout(timer)
    }
  }, [value, delayMs])

  return settled
}
