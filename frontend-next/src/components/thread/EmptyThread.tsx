import type { ReactNode } from 'react'

interface EmptyThreadProps {
  /** Composer rendered alongside the headline so the input sits with the prompt. */
  children?: ReactNode
}

export function EmptyThread({ children }: EmptyThreadProps) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-10 px-6 pb-24">
      <div className="max-w-md space-y-4 text-center">
        <h1 className="font-display text-4xl font-medium tracking-tight">
          What do you want to know?
        </h1>
        <p className="text-base text-muted-foreground">
          Ask about NFL stats from 1999 to 2025 — players, seasons, snap counts, contracts, play-by-play.
          The agent will explain its reasoning and show the underlying data.
        </p>
      </div>
      {children ? <div className="w-full">{children}</div> : null}
    </div>
  )
}
