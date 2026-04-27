import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { cn } from '@/lib/utils'

interface MarkdownProps {
  source: string
  className?: string
}

/**
 * Document-feel markdown renderer used in assistant text parts. Tables
 * defer to InlineDataTable styling; everything else is restrained
 * typography that matches the chat-workspace brief.
 */
export function Markdown({ source, className }: MarkdownProps) {
  return (
    <div className={cn('text-base leading-relaxed text-foreground', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
          h1: ({ children }) => (
            <h2 className="font-display text-2xl font-medium tracking-tight mt-6 mb-2">
              {children}
            </h2>
          ),
          h2: ({ children }) => (
            <h3 className="font-display text-xl font-medium tracking-tight mt-5 mb-2">
              {children}
            </h3>
          ),
          h3: ({ children }) => (
            <h4 className="text-lg font-semibold mt-4 mb-2">{children}</h4>
          ),
          ul: ({ children }) => (
            <ul className="mb-3 list-disc space-y-1 pl-5 marker:text-muted-foreground">
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-3 list-decimal space-y-1 pl-5 marker:text-muted-foreground tabular">
              {children}
            </ol>
          ),
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          strong: ({ children }) => (
            <strong className="font-semibold text-foreground">{children}</strong>
          ),
          em: ({ children }) => <em className="italic">{children}</em>,
          a: ({ children, href }) => (
            <a
              href={href}
              className="underline decoration-accent decoration-2 underline-offset-4 transition-colors hover:text-accent"
              target="_blank"
              rel="noopener noreferrer"
            >
              {children}
            </a>
          ),
          code: ({ children, className: codeClass }) => {
            const isBlock = !!codeClass
            if (isBlock) {
              return (
                <code
                  className={cn(
                    'block w-full rounded-md bg-muted px-3 py-2 font-mono text-sm leading-relaxed text-foreground overflow-x-auto',
                    codeClass,
                  )}
                >
                  {children}
                </code>
              )
            }
            return (
              <code className="rounded-sm bg-muted px-1 py-px font-mono text-[0.875em] text-foreground">
                {children}
              </code>
            )
          },
          pre: ({ children }) => <pre className="mb-3">{children}</pre>,
          table: ({ children }) => (
            <div className="my-4 overflow-x-auto rounded-md border border-border">
              <table className="w-full border-separate border-spacing-0 text-sm">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-muted/40">{children}</thead>,
          th: ({ children, style }) => (
            <th
              style={style}
              className="border-b border-border px-3 py-2 text-left font-medium text-muted-foreground first:pl-4 last:pr-4 [&[align=right]]:text-right [&[align=right]]:tabular"
            >
              {children}
            </th>
          ),
          td: ({ children, style }) => (
            <td
              style={style}
              className="border-t border-border px-3 py-2 first:pl-4 last:pr-4 [&[align=right]]:text-right [&[align=right]]:tabular"
            >
              {children}
            </td>
          ),
          blockquote: ({ children }) => (
            <blockquote className="my-4 border-l-2 border-accent pl-4 italic text-muted-foreground">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="my-6 border-t border-border" />,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
