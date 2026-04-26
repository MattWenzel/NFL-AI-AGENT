export function EmptyThread() {
  return (
    <div className="flex flex-1 items-center justify-center px-6">
      <div className="max-w-md space-y-4 text-center">
        <h1 className="font-display text-4xl font-medium tracking-tight">
          What do you want to know?
        </h1>
        <p className="text-base text-muted-foreground">
          Ask about NFL stats from 1999 to 2025 — players, seasons, snap counts, contracts, play-by-play.
          The agent will explain its reasoning and show the underlying data.
        </p>
      </div>
    </div>
  )
}
