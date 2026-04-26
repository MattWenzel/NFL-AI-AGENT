export function Inspector() {
  return (
    <div className="space-y-6 p-5">
      <section className="space-y-3">
        <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Session
        </p>
        <p className="text-sm text-muted-foreground">
          No conversation selected. Open a chat or start a new one to inspect runtime details.
        </p>
      </section>
    </div>
  )
}
