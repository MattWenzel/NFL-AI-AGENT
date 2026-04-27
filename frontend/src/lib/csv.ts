/**
 * Shared CSV cell-render utilities used by both the saved-Reports viewer
 * (`CsvViewer`) and the live table-chat viewer (`LiveTableView`). Keep
 * these helpers free of React imports so they can be unit-tested without
 * a renderer.
 */

export function compareValues(a: unknown, b: unknown): number {
  if (a == null && b == null) return 0
  if (a == null) return 1
  if (b == null) return -1

  const an = toNumber(a)
  const bn = toNumber(b)
  if (an !== null && bn !== null) return an - bn

  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' })
}

export function toNumber(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v)
    if (Number.isFinite(n)) return n
  }
  return null
}

// CSV rows come back as strings. Floats round-tripped through Python end up
// with precision artifacts like "471.20000000000005" — round to 6 decimals
// (sports stats never need more) and strip trailing zeros so they read
// cleanly. Integer strings and non-numeric strings pass through unchanged.
export function cleanNumericString(s: string): string {
  if (!s.includes('.')) return s
  const n = Number(s)
  if (!Number.isFinite(n)) return s
  return Number(n.toFixed(6)).toString()
}

export function cellTitle(value: unknown): string | undefined {
  if (value == null) return undefined
  if (typeof value === 'string') return value.length > 0 ? value : undefined
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

/**
 * Serialize a row set to CSV. Escapes per RFC 4180: any cell containing `,`,
 * `"`, `\n`, or `\r` is wrapped in double quotes with internal quotes
 * doubled. Null/undefined renders as an empty cell. Used for client-side
 * downloads in the table-chat view.
 */
export function tableToCsv(columns: string[], rows: Record<string, unknown>[]): string {
  const lines: string[] = [columns.map(escapeCsvCell).join(',')]
  for (const row of rows) {
    lines.push(columns.map((c) => escapeCsvCell(row[c])).join(','))
  }
  return lines.join('\n') + '\n'
}

function escapeCsvCell(value: unknown): string {
  if (value == null) return ''
  const s = typeof value === 'string' ? value : typeof value === 'object' ? JSON.stringify(value) : String(value)
  if (/[",\r\n]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`
  }
  return s
}

/**
 * Sanitize a freeform title into a CSV filename — same rules the backend
 * uses (`backend/domain/tools/handlers/create_csv_export._sanitize_filename`)
 * so a download from the table-chat view matches what `Save to Reports`
 * would name the file.
 */
export function sanitizeCsvFilename(title: string): string {
  const stripped = title.trim().replace(/[^a-zA-Z0-9_\-]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '')
  return (stripped.slice(0, 80) || 'table') + '.csv'
}
