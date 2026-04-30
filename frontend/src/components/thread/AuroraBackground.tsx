import { useTheme } from '@/lib/theme'

export function AuroraBackground() {
  const { auroraEnabled } = useTheme()
  if (!auroraEnabled) return null
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 overflow-hidden"
    >
      <div className="aurora-blob aurora-blob-a" />
      <div className="aurora-blob aurora-blob-b" />
      <div className="aurora-blob aurora-blob-c" />
    </div>
  )
}
