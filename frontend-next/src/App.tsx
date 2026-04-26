/**
 * Token preview surface — exists to verify the design tokens render the way
 * the brief intends, before any real components are built. Will be replaced
 * during the build phase by the actual app shell.
 */
import { useState } from 'react'

export default function App() {
  const [theme, setTheme] = useState<'light' | 'dark'>('light')

  return (
    <div className={theme === 'dark' ? 'dark' : ''}>
      <main className="min-h-screen bg-background text-foreground">
        <div className="mx-auto max-w-3xl px-8 py-16 space-y-14">
          <header className="space-y-3">
            <p className="text-2xs uppercase tracking-[0.14em] text-muted-foreground font-medium">
              Chat Workspace · Token Preview
            </p>
            <h1 className="font-display text-5xl tracking-tight">
              <span className="font-semibold">1,247</span> career carries.
            </h1>
            <p className="text-lg text-muted-foreground max-w-xl">
              Apple-polished productivity workspace tokens — Plus Jakarta Sans
              for UI, Fraunces for display, JetBrains Mono for code.
              Sage-teal accent, warm neutrals, restrained chrome.
            </p>
          </header>

          <section className="space-y-3">
            <p className="text-2xs uppercase tracking-[0.14em] text-muted-foreground font-medium">
              Type Pairing
            </p>
            <div className="rounded-lg border bg-card p-6 space-y-2">
              <p className="font-display text-3xl tracking-tight">Fraunces, variable serif</p>
              <p className="text-base">Plus Jakarta Sans for body. Tight letter-spacing, 1.55 leading, font-feature-settings for small-caps friendly numerics.</p>
              <p className="font-mono text-sm text-muted-foreground">SELECT player_gsis_id, season FROM season_stats WHERE …</p>
            </div>
          </section>

          <section className="space-y-3">
            <p className="text-2xs uppercase tracking-[0.14em] text-muted-foreground font-medium">
              Surfaces
            </p>
            <div className="grid grid-cols-3 gap-3">
              <div className="rounded-md border bg-background p-4 text-sm">background</div>
              <div className="rounded-md border bg-card p-4 text-sm">card</div>
              <div className="rounded-md border bg-muted p-4 text-sm text-muted-foreground">muted</div>
              <div className="rounded-md border bg-secondary p-4 text-sm text-secondary-foreground">secondary</div>
              <div className="rounded-md bg-primary p-4 text-sm text-primary-foreground">primary</div>
              <div className="rounded-md bg-accent p-4 text-sm text-accent-foreground">accent</div>
            </div>
          </section>

          <section className="space-y-3">
            <p className="text-2xs uppercase tracking-[0.14em] text-muted-foreground font-medium">
              Tabular Numerics
            </p>
            <table className="w-full text-sm border-separate border-spacing-0 [&_td]:py-2 [&_th]:py-2 [&_td]:border-t [&_th]:border-b">
              <thead className="text-left text-muted-foreground">
                <tr>
                  <th className="font-medium">Player</th>
                  <th className="font-medium text-right tabular">Att</th>
                  <th className="font-medium text-right tabular">Yds</th>
                  <th className="font-medium text-right tabular">YPC</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Saquon Barkley</td>
                  <td className="text-right tabular">378</td>
                  <td className="text-right tabular">2,005</td>
                  <td className="text-right tabular">5.30</td>
                </tr>
                <tr>
                  <td>Derrick Henry</td>
                  <td className="text-right tabular">325</td>
                  <td className="text-right tabular">1,921</td>
                  <td className="text-right tabular">5.91</td>
                </tr>
                <tr>
                  <td>Josh Jacobs</td>
                  <td className="text-right tabular">301</td>
                  <td className="text-right tabular">1,329</td>
                  <td className="text-right tabular">4.41</td>
                </tr>
              </tbody>
            </table>
          </section>

          <button
            type="button"
            onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
            className="rounded-md bg-secondary px-4 py-2 text-sm text-secondary-foreground transition-colors hover:bg-muted"
          >
            Toggle {theme === 'light' ? 'dark' : 'light'} mode
          </button>
        </div>
      </main>
    </div>
  )
}
