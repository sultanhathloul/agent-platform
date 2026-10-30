"use client";

import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { safeHref } from "@/lib/return-path";

// An assistant answer as Markdown (ADR-0326), live while it streams and from the history. Safe:
// raw HTML in the text is shown as text, never parsed into elements; links keep http(s) only
// (after react-markdown's own URL filter) and open in a new tab; images are not loaded (a model's
// answer must not fetch from another host).
// RTL: every block takes its direction from its own text (dir="auto"), lists indent on the
// start side (padding-inline-start), and code stays left to right.

type Node = { type: string; value?: string; children?: Node[] };

/** Raw HTML nodes become plain text, so `<b>` or `<script>` is shown as typed and never runs. */
export function remarkHtmlAsText() {
  const walk = (node: Node) => {
    if (node.type === "html") node.type = "text";
    node.children?.forEach(walk);
  };
  return (tree: Node) => walk(tree);
}

function Link({ href, children }: ComponentPropsWithoutRef<"a">) {
  const safe = safeHref(href);
  if (!safe) return <>{children}</>;
  return (
    <a href={safe} target="_blank" rel="noopener noreferrer" className="text-info underline">
      {children}
    </a>
  );
}

const COMPONENTS: Components = {
  // A single line break stays a line break (pre-line): a memo's header and signature lines are
  // one per line without a blank line between them.
  p: ({ children }) => <p dir="auto" className="my-2 whitespace-pre-line first:mt-0 last:mb-0">{children}</p>,
  h1: ({ children }) => <h3 dir="auto" className="mt-3 mb-1 text-lg font-semibold">{children}</h3>,
  h2: ({ children }) => <h3 dir="auto" className="mt-3 mb-1 text-base font-semibold">{children}</h3>,
  h3: ({ children }) => <h4 dir="auto" className="mt-3 mb-1 font-semibold">{children}</h4>,
  h4: ({ children }) => <h4 dir="auto" className="mt-2 mb-1 font-semibold">{children}</h4>,
  h5: ({ children }) => <h5 dir="auto" className="mt-2 mb-1 font-semibold">{children}</h5>,
  h6: ({ children }) => <h6 dir="auto" className="mt-2 mb-1 font-semibold">{children}</h6>,
  ul: ({ children }) => <ul dir="auto" className="my-2 list-disc ps-6">{children}</ul>,
  ol: ({ children }) => <ol dir="auto" className="my-2 list-decimal ps-6">{children}</ol>,
  li: ({ children }) => <li className="my-0.5">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote dir="auto" className="my-2 border-s-4 border-line ps-3 text-muted">{children}</blockquote>
  ),
  a: Link,
  img: ({ alt }) => (alt ? <span>{alt}</span> : null),
  pre: ({ children }) => (
    <pre dir="ltr" className="my-2 overflow-x-auto rounded-lg bg-surface-2 p-3 text-start text-sm ring-1 ring-line">
      {children}
    </pre>
  ),
  code: ({ children }) => (
    <code dir="ltr" className="rounded bg-surface-2 px-1 font-mono text-[0.9em]">{children}</code>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table dir="auto" className="border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="border border-line bg-surface-2 px-2 py-1 text-start font-semibold">{children}</th>,
  td: ({ children }) => <td className="border border-line px-2 py-1 text-start">{children}</td>,
  hr: () => <hr className="my-3 border-line" />,
};

export function MessageMarkdown({ text }: { text: string }) {
  return (
    <div dir="auto" className="leading-7 break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkHtmlAsText]} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
