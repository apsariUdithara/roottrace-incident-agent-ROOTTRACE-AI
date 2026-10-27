import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { cn } from '@/lib/utils'

/** Markdown from the model (chat answers, reports). Raw HTML in it is shown as text, never
 * rendered: the model's output is untrusted. Links open in a new tab. */
export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div
      className={cn(
        'flex flex-col gap-2 text-sm leading-relaxed wrap-break-word',
        '[&_ul]:list-disc [&_ol]:list-decimal [&_ul,&_ol]:flex [&_ul,&_ol]:flex-col [&_ul,&_ol]:gap-1 [&_ul,&_ol]:pl-5',
        '[&_h1,&_h2,&_h3]:font-heading [&_h1,&_h2,&_h3]:font-semibold [&_h1]:text-base [&_h2]:text-sm',
        '[&_a]:text-primary [&_a]:underline-offset-4 [&_a:hover]:underline',
        '[&_:not(pre)>code]:rounded [&_:not(pre)>code]:bg-muted [&_:not(pre)>code]:px-1 [&_:not(pre)>code]:text-xs',
        '[&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3 [&_pre]:text-xs',
        '[&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground',
        '[&_table]:w-full [&_table]:text-xs [&_td,&_th]:border [&_td,&_th]:px-2 [&_td,&_th]:py-1',
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
