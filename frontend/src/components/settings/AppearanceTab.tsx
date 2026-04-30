import { Check, Monitor } from 'lucide-react'

import { Switch } from '@/components/ui/switch'
import { useTheme, type ThemeMode } from '@/lib/theme'
import { cn } from '@/lib/utils'

interface ThemeOption {
  value: ThemeMode
  label: string
  description: string
  /** Three-color preview swatch (bg / surface / accent) shown inside
   *  the card. Hard-coded so the preview stays accurate regardless of
   *  the currently-active theme. */
  swatch: { bg: string; card: string; accent: string }
  /** Optional split-preview hint — used by `system` to show a half-and-
   *  half tile signalling "follows the OS". */
  split?: boolean
}

const OPTIONS: ThemeOption[] = [
  {
    value: 'light',
    label: 'Light',
    description: 'Paper-warm canvas, navy accent.',
    swatch: { bg: '#f5f0e6', card: '#fcf9f3', accent: '#1f4f87' },
  },
  {
    value: 'dark',
    label: 'Dark',
    description: 'Cool near-black, vivid azure.',
    swatch: { bg: '#0a1118', card: '#141d27', accent: '#3fa0ff' },
  },
  {
    value: 'cobalt',
    label: 'Cobalt',
    description: 'GitHub-style dark with the legacy blue.',
    swatch: { bg: '#0d1117', card: '#161b22', accent: '#2f81f7' },
  },
  {
    value: 'cyberpunk',
    label: 'Cyberpunk',
    description: 'Neon on black, mono type, grid overlay.',
    swatch: { bg: '#000000', card: '#0a1620', accent: '#22d3ee' },
  },
  {
    value: 'system',
    label: 'System',
    description: 'Match the OS — warm-charcoal at night.',
    swatch: { bg: '#f5f0e6', card: '#1a1a18', accent: '#c5cad4' },
    split: true,
  },
]

export function AppearanceTab() {
  const theme = useTheme()

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <div>
          <h3 className="text-base font-semibold">Theme</h3>
          <p className="text-2xs text-muted-foreground">
            Visual palette and typography for the workspace.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {OPTIONS.map((opt) => (
            <ThemeCard
              key={opt.value}
              option={opt}
              selected={theme.mode === opt.value}
              onSelect={() => theme.setMode(opt.value)}
            />
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3 border-t border-border pt-6">
        <div>
          <h3 className="text-base font-semibold">Effects</h3>
          <p className="text-2xs text-muted-foreground">
            Subtle motion behind empty states. Turn off if you prefer a static canvas.
          </p>
        </div>
        <label className="flex items-center justify-between gap-4 rounded-lg border border-border bg-card p-4">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-medium">Background effects</span>
            <span className="text-2xs text-muted-foreground">
              Slow-drifting aurora glow on the new-chat / new-report screens.
            </span>
          </div>
          <Switch
            checked={theme.auroraEnabled}
            onCheckedChange={theme.setAuroraEnabled}
            aria-label="Background effects"
          />
        </label>
      </section>
    </div>
  )
}

function ThemeCard({
  option,
  selected,
  onSelect,
}: {
  option: ThemeOption
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        'group relative flex flex-col gap-3 rounded-lg border bg-card p-3 text-left transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        selected
          ? 'border-accent ring-1 ring-accent'
          : 'border-border hover:border-accent/40 hover:bg-muted/30',
      )}
    >
      <ThemePreview swatch={option.swatch} split={option.split} />
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{option.label}</span>
        {selected ? (
          <Check className="size-3.5 text-accent" aria-label="Selected" />
        ) : null}
      </div>
      <p className="text-2xs leading-snug text-muted-foreground">{option.description}</p>
    </button>
  )
}

function ThemePreview({
  swatch,
  split,
}: {
  swatch: ThemeOption['swatch']
  split?: boolean
}) {
  if (split) {
    // System: half-and-half — left side shows light surface, right side
    // shows dark, with the Monitor glyph centered to read as "auto".
    return (
      <div
        className="relative flex h-16 w-full items-center justify-center overflow-hidden rounded-md border border-border/40"
        style={{ background: `linear-gradient(90deg, ${swatch.bg} 50%, ${swatch.card} 50%)` }}
      >
        <Monitor className="size-5 text-foreground/70" />
      </div>
    )
  }
  return (
    <div
      className="flex h-16 w-full overflow-hidden rounded-md border border-border/40"
      style={{ backgroundColor: swatch.bg }}
    >
      <div className="flex w-2/3 items-center px-2" style={{ backgroundColor: swatch.bg }}>
        <div
          className="h-3 w-3/4 rounded-full"
          style={{ backgroundColor: swatch.card }}
          aria-hidden
        />
      </div>
      <div
        className="flex w-1/3 items-center justify-center"
        style={{ backgroundColor: swatch.card }}
      >
        <div
          className="size-4 rounded-full"
          style={{ backgroundColor: swatch.accent }}
          aria-hidden
        />
      </div>
    </div>
  )
}
