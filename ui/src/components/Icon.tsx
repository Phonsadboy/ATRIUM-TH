/**
 * Pixel-art UI icon / logo helpers. Images live in /public/art/ui.
 * `.pixel-img` (index.css) lets high-resolution generated assets downsample smoothly.
 */

export type IconName =
  | 'alert'
  | 'approve'
  | 'archive'
  | 'autonomy'
  | 'budget'
  | 'chat'
  | 'clock'
  | 'close'
  | 'graph'
  | 'memory'
  | 'model'
  | 'pause'
  | 'play'
  | 'plus'
  | 'power'
  | 'reject'
  | 'send'
  | 'tasks'

export function Icon({
  name,
  size = 18,
  className = '',
  alt = '',
}: {
  name: IconName
  size?: number
  className?: string
  alt?: string
}) {
  return (
    <img
      src={`/art/ui/icons/${name}.png`}
      width={size}
      height={size}
      alt={alt}
      draggable={false}
      className={`pixel-img inline-block shrink-0 select-none ${className}`}
      style={{ width: size, height: size }}
    />
  )
}

export function Logo({
  height = 34,
  className = '',
}: {
  height?: number
  className?: string
}) {
  return (
    <img
      src="/art/ui/logo.png"
      alt="ATRIUM"
      draggable={false}
      className={`pixel-img select-none ${className}`}
      style={{ height, width: 'auto' }}
    />
  )
}
