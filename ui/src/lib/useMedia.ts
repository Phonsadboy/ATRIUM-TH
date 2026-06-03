import { useEffect, useState } from 'react'

/**
 * Tracks whether the viewport is at or above the desktop breakpoint (901px) —
 * i.e. the three-column layout, where panel collapse/resize applies. Below it
 * the layout is a single switched column and these affordances are hidden.
 */
export function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(() =>
    typeof window === 'undefined' ? true : window.matchMedia('(min-width: 901px)').matches,
  )
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 901px)')
    const onChange = () => setIsDesktop(mq.matches)
    mq.addEventListener('change', onChange)
    onChange()
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return isDesktop
}
