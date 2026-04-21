"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

// Shared styling for AI-generated markdown. GFM is required for tables — our
// Gemini trade-idea prompts and Claude interpretations both emit | ... | ... |
// tables that plain react-markdown treats as inline text.
//
// Each element is styled via explicit Tailwind classes rather than the
// typography plugin because `@tailwindcss/typography` isn't wired into the
// project and adding it on Tailwind v4 requires a globals.css change.
//
// Font-size / text-color on text-bearing elements (p, li, ul, ol) intentionally
// does NOT include a `text-*` class — the wrapper's className cascades in.
// This lets callers control density (e.g. the home Intelligence panel at
// text-[0.78rem]) without fighting overrides.
const COMPONENTS: Components = {
  h1: ({ children }) => <h2 className="text-lg font-bold text-text mt-4 mb-2">{children}</h2>,
  h2: ({ children }) => <h3 className="text-base font-bold text-text mt-4 mb-2">{children}</h3>,
  h3: ({ children }) => <h4 className="text-sm font-bold text-text mt-3 mb-1.5">{children}</h4>,
  h4: ({ children }) => <h5 className="text-xs font-bold text-text uppercase tracking-wider mt-3 mb-1">{children}</h5>,
  p: ({ children }) => <p className="leading-relaxed my-2">{children}</p>,
  strong: ({ children }) => <strong className="text-text font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  // GFM strikethrough (~~text~~)
  del: ({ children }) => <del className="text-text-muted line-through">{children}</del>,
  ul: ({ children }) => <ul className="list-disc pl-5 my-2 space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal pl-5 my-2 space-y-1">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  // Fenced code block: pre wraps the <code>, so style pre for the frame and
  // keep `code` lean so the inline variant (without pre parent) still looks right.
  pre: ({ children }) => (
    <pre className="my-3 p-3 rounded border border-border bg-surface-alt overflow-x-auto text-xs font-data leading-relaxed">
      {children}
    </pre>
  ),
  code: ({ children }) => <code className="px-1 py-0.5 rounded bg-surface-alt text-xs font-data">{children}</code>,
  hr: () => <hr className="my-4 border-border" />,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
      {children}
    </a>
  ),
  // GFM tables — the core reason this component exists.
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto border border-border rounded">
      <table className="w-full text-xs border-collapse">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-surface-alt">{children}</thead>,
  tbody: ({ children }) => <tbody>{children}</tbody>,
  tr: ({ children }) => <tr className="border-b border-border last:border-b-0">{children}</tr>,
  th: ({ children }) => (
    <th className="px-2 py-1.5 text-left font-semibold text-text-muted uppercase tracking-wider text-[10px] whitespace-nowrap">
      {children}
    </th>
  ),
  td: ({ children }) => <td className="px-2 py-1.5 text-text align-top">{children}</td>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-accent/40 pl-3 my-3 italic">
      {children}
    </blockquote>
  ),
  // GFM task list checkboxes. Pass through the `disabled` + `checked` props
  // react-markdown supplies; style without the default browser look.
  input: ({ type, checked, disabled }) => {
    if (type !== "checkbox") return null;
    return (
      <input
        type="checkbox"
        checked={!!checked}
        disabled={!!disabled}
        readOnly
        className="mr-1.5 align-middle accent-accent"
      />
    );
  },
};

export function AIMarkdown({ text, className }: { text: string; className?: string }) {
  // Default to a sensible body size when no wrapper class is passed so that
  // existing callsites without a className keep their previous density
  // (text-sm, text-text-secondary) rather than inheriting the page default.
  const wrapper = className ?? "text-sm text-text-secondary";
  return (
    <div className={wrapper}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
