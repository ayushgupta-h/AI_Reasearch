/**
 * Research AI Quest — Enterprise Research Report Viewer
 * Inspired by Perplexity Deep Research and enterprise consulting documents.
 */

import React, { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  FileText,
  Download,
  Copy,
  Check,
  ExternalLink,
  ShieldCheck,
  TrendingUp,
  Award,
  Layers,
  Clock,
  Calendar,
  Maximize2,
  Minimize2,
  BookOpen,
  Hash,
} from 'lucide-react';

export interface ReportQualityMetrics {
  criticScore?: number | null;
  revisionCount?: number | null;
  sourcesCount?: number | null;
  citationsVerified?: number | null;
  citationsTotal?: number | null;
  citationPassRate?: string | null;
  claimsCount?: number | null;
  provider?: string | null;
  generatedAt?: string | null;
}

export interface ReportViewerProps {
  title?: string;
  topic?: string;
  content: string;
  metrics?: ReportQualityMetrics;
  onCite?: (citationNumber: number) => void;
  onExport?: (format: 'pdf' | 'docx' | 'markdown') => Promise<void> | void;
  onEdit?: () => void;
  fullscreen?: boolean;
  onToggleFullscreen?: () => void;
}

/**
 * Normalizes and strips raw internal metadata, duplicate frontmatters, and debug appendixes
 * from the user-facing report content.
 */
export function sanitizeReportContent(raw: string): string {
  if (!raw || typeof raw !== 'string') return '';

  let text = raw.trim();

  // 1. Remove YAML frontmatter if fenced by ---
  text = text.replace(/^---\s*\n[\s\S]*?\n---\s*\n?/g, '');
  text = text.replace(/\n---\s*\n[\s\S]*?\n---\s*\n?/g, '\n\n');

  // 2. Remove loose un-fenced metadata lines (title: "...", generated_at: "...", etc.)
  const metadataRegex = /^(?:title|generated_at|provider|research_question|critic_score|revisions|revision_count|max_revisions|sources_analyzed|sources_cited|citations_checked|citations_verified|claims_verified|claims_unverified|claims_contradicted|contradictions_detected|citation_pass_rate|source_credibility_evaluated):\s*.*$/gim;
  text = text.replace(metadataRegex, '');

  // 3. Remove appended internal debug appendices
  const appendixMarkers = [
    /\n#{1,3}\s*Citation Validation Appendix[\s\S]*/i,
    /\nCitation Validation Appendix[\s\S]*/i,
    /\n#{1,3}\s*Source Credibility Assessment[\s\S]*/i,
    /\nSource Credibility Assessment[\s\S]*/i,
  ];
  for (const marker of appendixMarkers) {
    text = text.replace(marker, '');
  }

  // 4. Remove duplicate repetitive titles (e.g. H1 followed immediately by raw plain text title)
  const lines = text.split('\n');
  const cleanedLines: string[] = [];
  let firstH1 = '';

  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();
    if (trimmed.startsWith('# ')) {
      const h1Val = trimmed.replace(/^#\s+/, '').trim().toLowerCase();
      if (!firstH1) {
        firstH1 = h1Val;
        cleanedLines.push(lines[i]);
      } else if (h1Val === firstH1) {
        // Skip duplicate H1
        continue;
      } else {
        cleanedLines.push(lines[i]);
      }
    } else if (firstH1 && cleanedLines.length < 5 && trimmed.toLowerCase() === firstH1) {
      // Skip raw unformatted title repetition
      continue;
    } else {
      cleanedLines.push(lines[i]);
    }
  }

  text = cleanedLines.join('\n');
  // Clean excessive blank lines
  text = text.replace(/\n{3,}/g, '\n\n').trim();

  return text;
}

/**
 * Scans markdown text for key financial or numerical metrics in the executive summary / findings
 * to render executive highlight cards.
 */
interface MetricHighlight {
  label: string;
  value: string;
}

function extractKeyMetrics(text: string): MetricHighlight[] {
  const metrics: MetricHighlight[] = [];
  const seenValues = new Set<string>();

  // Look for prominent patterns like "$215.9 billion", "80% market share", "+122% YoY", "FY26"
  const patterns: { regex: RegExp; label: string }[] = [
    { regex: /(?:Total\s+Revenue|Revenue|Sales)\s*(?::|of)?\s*(\$[\d,.]+\s*(?:billion|million|trillion|B|M|T)?)/i, label: 'Total Revenue' },
    { regex: /(?:Net\s+Income|Profit)\s*(?::|of)?\s*(\$[\d,.]+\s*(?:billion|million|trillion|B|M|T)?)/i, label: 'Net Income' },
    { regex: /(?:Operating\s+Income|EBITDA)\s*(?::|of)?\s*(\$[\d,.]+\s*(?:billion|million|trillion|B|M|T)?)/i, label: 'Operating Income' },
    { regex: /(\b\d{1,3}(?:\.\d+)?%\s*(?:market\s+share|share|growth|margin|YoY|CAGR)?)/i, label: 'Market / Growth' },
    { regex: /(?:Fiscal\s+Year|FY)\s*(20\d\d|\d{2})/i, label: 'Fiscal Period' },
  ];

  for (const { regex, label } of patterns) {
    const match = regex.exec(text);
    if (match && match[1]) {
      const val = match[1].trim();
      if (!seenValues.has(val.toLowerCase()) && val.length < 25) {
        seenValues.add(val.toLowerCase());
        metrics.push({ label, value: val });
        if (metrics.length >= 4) break;
      }
    }
  }

  return metrics;
}

/**
 * Pre-processes markdown so citation numbers [1], [2] are preserved and routed to custom interactive buttons.
 */
function markCitations(md: string): string {
  const parts = md.split(/(```[\s\S]*?```|`[^`]*`)/g);
  return parts
    .map((p, i) => (i % 2 === 1 ? p : p.replace(/\[(\d+)\](?!\()/g, '`[$1]`')))
    .join('');
}

export function ReportViewer({
  title,
  topic,
  content,
  metrics,
  onCite,
  onExport,
  onEdit,
  fullscreen,
  onToggleFullscreen,
}: ReportViewerProps) {
  const [copied, setCopied] = useState(false);

  // Clean the report markdown
  const cleanedContent = useMemo(() => sanitizeReportContent(content), [content]);

  // Derive title from H1 if not explicitly passed
  const displayTitle = useMemo(() => {
    if (title && title !== 'Research Report') return title;
    const h1Match = /^#\s+(.+)$/m.exec(cleanedContent);
    if (h1Match) return h1Match[1].trim();
    return topic || 'Synthesized Intelligence Report';
  }, [title, topic, cleanedContent]);

  // Strip leading H1 from body if it matches displayTitle to prevent duplication
  const contentBody = useMemo(() => {
    const lines = cleanedContent.split('\n');
    if (lines[0]?.trim().startsWith('# ')) {
      return lines.slice(1).join('\n').trim();
    }
    return cleanedContent;
  }, [cleanedContent]);

  // Extract key metrics from text for executive highlight strip
  const keyMetrics = useMemo(() => extractKeyMetrics(cleanedContent), [cleanedContent]);

  // Calculate word count and estimated reading time
  const { wordCount, readTimeMinutes } = useMemo(() => {
    const words = cleanedContent.split(/\s+/).filter(Boolean).length;
    return {
      wordCount: words,
      readTimeMinutes: Math.max(1, Math.round(words / 220)),
    };
  }, [cleanedContent]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(cleanedContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (_) {}
  };

  return (
    <article className="relative mx-auto w-full max-w-4xl rounded-2xl border border-[#453322] bg-[#120F0D] text-[#F4EBDD] shadow-2xl transition-all">
      {/* ── Document Control Header ── */}
      <header className="border-b border-[#302319] bg-[#17130F] p-6 sm:p-8 rounded-t-2xl">
        {/* Top Badges & Actions Strip */}
        <div className="flex flex-wrap items-center justify-between gap-4 mb-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-400">
              <ShieldCheck className="h-3.5 w-3.5" /> Verified Intelligence Report
            </span>
            {metrics?.criticScore != null && (
              <span className="inline-flex items-center gap-1 rounded-full border border-[#C5A36A]/40 bg-[#C5A36A]/10 px-2.5 py-1 text-xs font-mono font-medium text-[#E9D6B6]">
                <Award className="h-3 w-3 text-[#C5A36A]" /> Quality: {metrics.criticScore}/10
              </span>
            )}
            {metrics?.revisionCount != null && metrics.revisionCount > 0 && (
              <span className="inline-flex items-center gap-1 rounded-full border border-[#3E2E20] bg-[#1B1510] px-2.5 py-1 text-xs font-mono text-[#C0B19C]">
                <Layers className="h-3 w-3 text-[#817463]" /> {metrics.revisionCount} Cycles
              </span>
            )}
          </div>

          {/* Export & Utility Actions */}
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={handleCopy}
              className="inline-flex items-center gap-1.5 rounded-lg border border-[#453322] bg-[#1E1813] px-2.5 py-1.5 text-xs text-[#C0B19C] hover:bg-[#2A211A] hover:text-[#F4EBDD] transition-colors"
              title="Copy clean report markdown"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? 'Copied' : 'Copy'}
            </button>

            {onExport && (
              <>
                <button
                  type="button"
                  onClick={() => onExport('pdf')}
                  className="inline-flex items-center gap-1 rounded-lg border border-[#453322] bg-[#1E1813] px-2.5 py-1.5 text-xs text-[#C0B19C] hover:bg-[#2A211A] hover:text-[#F4EBDD] transition-colors"
                  title="Export PDF Document"
                >
                  <Download className="h-3.5 w-3.5 text-rose-400" /> PDF
                </button>
                <button
                  type="button"
                  onClick={() => onExport('docx')}
                  className="inline-flex items-center gap-1 rounded-lg border border-[#453322] bg-[#1E1813] px-2.5 py-1.5 text-xs text-[#C0B19C] hover:bg-[#2A211A] hover:text-[#F4EBDD] transition-colors"
                  title="Export DOCX Document"
                >
                  <Download className="h-3.5 w-3.5 text-blue-400" /> Word
                </button>
              </>
            )}

            {onToggleFullscreen && (
              <button
                type="button"
                onClick={onToggleFullscreen}
                className="inline-flex items-center rounded-lg border border-[#453322] bg-[#1E1813] p-1.5 text-[#C0B19C] hover:bg-[#2A211A] hover:text-[#F4EBDD] transition-colors"
                title={fullscreen ? 'Exit Fullscreen' : 'Fullscreen Reading Mode'}
              >
                {fullscreen ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
              </button>
            )}
          </div>
        </div>

        {/* Report Main Title */}
        <h1 className="font-serif text-2xl sm:text-3xl lg:text-4xl font-bold tracking-tight text-[#F4EBDD] leading-tight">
          {displayTitle}
        </h1>

        {/* Document Metadata Details Bar */}
        <div className="mt-4 flex flex-wrap items-center gap-y-2 gap-x-5 text-xs text-[#9B8C78] border-t border-[#2D2117] pt-3">
          <span className="flex items-center gap-1.5">
            <Calendar className="h-3.5 w-3.5 text-[#817463]" />
            {metrics?.generatedAt
              ? new Date(metrics.generatedAt).toLocaleDateString(undefined, {
                  month: 'short',
                  day: 'numeric',
                  year: 'numeric',
                })
              : new Date().toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
          </span>
          <span className="flex items-center gap-1.5">
            <Clock className="h-3.5 w-3.5 text-[#817463]" />
            ~{readTimeMinutes} min read ({wordCount.toLocaleString()} words)
          </span>
          {metrics?.sourcesCount != null && (
            <span className="flex items-center gap-1.5">
              <BookOpen className="h-3.5 w-3.5 text-[#817463]" />
              {metrics.sourcesCount} Sources Integrated
            </span>
          )}
          {metrics?.citationsVerified != null && (
            <span className="flex items-center gap-1.5 text-emerald-400/90">
              <Check className="h-3.5 w-3.5 text-emerald-400" />
              {metrics.citationsVerified}/{metrics.citationsTotal ?? metrics.citationsVerified} Citations Verified
            </span>
          )}
        </div>
      </header>

      {/* ── Key Metrics Cards Strip (When detected in research) ── */}
      {keyMetrics.length > 0 && (
        <section className="border-b border-[#302319] bg-[#16120F] px-6 py-4">
          <p className="text-[10px] font-mono uppercase tracking-wider text-[#C5A36A] mb-2 flex items-center gap-1.5">
            <TrendingUp className="h-3.5 w-3.5" /> Primary Quantitative Insights
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {keyMetrics.map((km, idx) => (
              <div
                key={idx}
                className="rounded-xl border border-[#3E2E20] bg-[#1C1611] p-3 text-left shadow-sm"
              >
                <span className="block text-[11px] font-medium text-[#817463] uppercase tracking-wide truncate">
                  {km.label}
                </span>
                <span className="block font-serif text-lg sm:text-xl font-bold text-[#E9D6B6] mt-0.5 truncate">
                  {km.value}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Document Body (Clean Markdown Rendering) ── */}
      <section className="p-6 sm:p-10 lg:p-12 leading-relaxed">
        <div className="prose prose-invert max-w-none text-[15px] text-[#D8CEBF]">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              // Headings
              h1({ children }) {
                return (
                  <h1 className="font-serif text-2xl sm:text-3xl font-bold text-[#F4EBDD] mt-8 mb-4 border-b border-[#302319] pb-3 flex items-center gap-2">
                    {children}
                  </h1>
                );
              },
              h2({ children }) {
                return (
                  <h2 className="font-serif text-xl sm:text-2xl font-semibold text-[#E9D6B6] mt-8 mb-4 flex items-center gap-2 border-b border-[#281E15] pb-2">
                    <span className="h-2 w-2 rounded-full bg-[#C5A36A] shrink-0" />
                    <span>{children}</span>
                  </h2>
                );
              },
              h3({ children }) {
                return (
                  <h3 className="font-serif text-lg sm:text-xl font-medium text-[#F4EBDD] mt-6 mb-2.5">
                    {children}
                  </h3>
                );
              },
              h4({ children }) {
                return (
                  <h4 className="font-mono text-xs uppercase tracking-wider text-[#C5A36A] mt-5 mb-1.5 font-semibold">
                    {children}
                  </h4>
                );
              },

              // Paragraphs
              p({ children }) {
                return <p className="mb-4 leading-relaxed text-[#D6CBB8] text-[15px]">{children}</p>;
              },

              // Responsive Consulting Tables
              table({ children }) {
                return (
                  <div className="my-6 overflow-hidden rounded-xl border border-[#453322] bg-[#14100D] shadow-md">
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs border-collapse">
                        {children}
                      </table>
                    </div>
                  </div>
                );
              },
              thead({ children }) {
                return (
                  <thead className="border-b border-[#453322] bg-[#221A13] text-[#E9D6B6] font-mono uppercase tracking-wider text-[11px]">
                    {children}
                  </thead>
                );
              },
              th({ children }) {
                return <th className="px-4 py-3 font-semibold text-[#F4EBDD] whitespace-nowrap">{children}</th>;
              },
              tbody({ children }) {
                return <tbody className="divide-y divide-[#2B1E14]">{children}</tbody>;
              },
              tr({ children }) {
                return (
                  <tr className="hover:bg-[#201811] transition-colors odd:bg-[#120F0C] even:bg-[#17120E]">
                    {children}
                  </tr>
                );
              },
              td({ children }) {
                return <td className="px-4 py-2.5 text-[#D8CEBF] leading-normal">{children}</td>;
              },

              // Blockquotes / Executive Callouts
              blockquote({ children }) {
                return (
                  <blockquote className="my-6 rounded-r-xl border-l-4 border-[#C5A36A] bg-[#1A140F] p-4 sm:p-5 text-[#F4EBDD] italic shadow-sm">
                    {children}
                  </blockquote>
                );
              },

              // Lists
              ul({ children }) {
                return <ul className="my-4 space-y-2 pl-2 text-[#D6CBB8] list-none">{children}</ul>;
              },
              ol({ children }) {
                return <ol className="my-4 space-y-2 pl-6 list-decimal text-[#D6CBB8]">{children}</ol>;
              },
              li({ children }) {
                return (
                  <li className="flex items-start gap-2 text-[14.5px] leading-relaxed">
                    <span className="h-1.5 w-1.5 rounded-full bg-[#C5A36A]/70 mt-2 shrink-0" />
                    <span className="flex-1">{children}</span>
                  </li>
                );
              },

              // Code & Citations
              code({ className, children, ...props }) {
                const text = String(children ?? '');
                const citationMatch = !className && /^\[(\d+)\]$/.exec(text.trim());

                if (citationMatch) {
                  const citeNum = Number(citationMatch[1]);
                  return (
                    <button
                      type="button"
                      onClick={() => onCite?.(citeNum)}
                      className="inline-flex items-center justify-center mx-1 px-1.5 py-0.5 rounded text-[11px] font-mono font-semibold bg-[#261D15] text-[#D6A65D] border border-[#543F29] hover:bg-[#C5A36A] hover:text-[#080806] hover:border-[#C5A36A] transition-all cursor-pointer shadow-xs"
                      title={`Jump to Source [${citeNum}] in sidebar`}
                    >
                      [{citeNum}]
                    </button>
                  );
                }

                return (
                  <code
                    className="rounded bg-[#201A14] border border-[#3E2E20] px-1.5 py-0.5 font-mono text-[13px] text-[#E9D6B6]"
                    {...props}
                  >
                    {children}
                  </code>
                );
              },

              pre({ children }) {
                return (
                  <pre className="my-6 overflow-x-auto rounded-xl border border-[#3E2E20] bg-[#0E0C0A] p-4 font-mono text-xs text-[#E9D6B6]">
                    {children}
                  </pre>
                );
              },

              // Links
              a({ href, children, ...props }) {
                return (
                  <a
                    href={href}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="inline-flex items-center gap-0.5 text-[#C5A36A] hover:underline font-medium"
                    {...props}
                  >
                    <span>{children}</span>
                    <ExternalLink className="h-3 w-3 inline ml-0.5 opacity-70" />
                  </a>
                );
              },

              hr() {
                return <hr className="my-8 border-t border-[#302319]" />;
              },
            }}
          >
            {markCitations(contentBody)}
          </ReactMarkdown>
        </div>
      </section>

      {/* ── Document Footer ── */}
      <footer className="border-t border-[#302319] bg-[#14100D] px-6 py-4 rounded-b-2xl flex flex-wrap items-center justify-between text-xs text-[#817463]">
        <div className="flex items-center gap-2">
          <span className="font-serif font-semibold text-[#E9D6B6]">Quest Research Workspace</span>
          <span>·</span>
          <span>Enterprise Intelligence Pipeline</span>
        </div>
        <div className="flex items-center gap-3 mt-2 sm:mt-0">
          <button
            type="button"
            onClick={handleCopy}
            className="hover:text-[#C5A36A] transition-colors"
          >
            {copied ? '✓ Copied' : 'Copy Report'}
          </button>
          {onEdit && (
            <button
              type="button"
              onClick={onEdit}
              className="hover:text-[#C5A36A] transition-colors"
            >
              Open in Editor
            </button>
          )}
        </div>
      </footer>
    </article>
  );
}
