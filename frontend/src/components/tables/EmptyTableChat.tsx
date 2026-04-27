interface EmptyTableChatProps {
  streaming?: boolean
}

/** Placeholder shown in the table-chat panel before the user has sent
 *  their first turn. Once the transcript has any turns, the regular
 *  Thread component takes over. */
export function EmptyTableChat({ streaming = false }: EmptyTableChatProps) {
  return (
    <div className="grid flex-1 place-items-center px-6 text-center">
      <p className="text-sm text-muted-foreground">
        {streaming ? 'Working…' : 'Send a message to get started.'}
      </p>
    </div>
  )
}
