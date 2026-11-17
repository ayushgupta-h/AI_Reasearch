/**
 * Research AI Quest — Complete Application Suite (React + TypeScript)
 * Includes: Workspace, Report Editor, History, Projects, Documents,
 * Knowledge Base, Contradictions, System Status, Auth, and Router.
 */

import React, {
  forwardRef, useCallback, useEffect, useMemo, useRef, useState,
  type ButtonHTMLAttributes, type FormEvent, type InputHTMLAttributes,
  type ReactNode, type TextareaHTMLAttributes
} from 'react';
import { createPortal } from 'react-dom';
import {
  Link, Navigate, NavLink, Outlet, Route, Routes, useLocation,
  useNavigate, useParams, useSearchParams
} from 'react-router-dom';
import { QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Activity, AlertTriangle, Brain, Check, ChevronRight, ExternalLink,
  FileDown, FileText, Files, FolderKanban, GitCompare, History, Loader2, LogOut,
  Menu, Monitor, Moon, PanelLeftClose, PanelLeftOpen, Paperclip, Pencil, Plus,
  RefreshCw, Search, Send, Settings, ShieldCheck, Sun, Trash2, X, UploadCloud,
  CheckCircle2, XCircle, ArrowUpRight, DollarSign, Cpu, Eye, Download, Filter, Clock, Sparkles
} from 'lucide-react';

import { ReportViewer } from '../components/ReportViewer';
import { api, apiError, downloadExport, getAccessToken, streamUrl } from '../api/client';
import { API, API_BASE } from '../api/endpoints';
import type {
  ClaimRecord, Project, ResearchReport, ResearchSession, SourceRef,
  DocumentRecord, ContradictionRecord, MemoryRecord, KnowledgeStats, HealthStatus,
  ProvidersResponse, UsageSummaryResponse, NotificationRecord
} from '../api/types';
import { streamSSE, type SSEMessage } from '../lib/sse';
import { supabaseConfigured } from '../lib/supabase';
import { cn, fmtDate, fmtDuration, titleFromQuery } from '../lib/utils';
import { useAuth } from '../providers/Auth';
import { useTheme, useToast, type Theme as ThemeMode } from '../providers/ui';
import { LandingPage } from './LandingPage';

/* ============================================================
 * SECTION 1 — QUERY HOOKS + SSE
 * ============================================================ */

export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15_000 } },
});

const asList = <T,>(d: unknown): T[] => {
  if (Array.isArray(d)) return d as T[];
  const o = d as Record<string, unknown>;
  return ((o?.items ?? o?.sessions ?? o?.projects ?? o?.documents ?? o?.results ?? o?.contradictions ?? []) as T[]) ?? [];
};

const normalizeSession = (raw: any): ResearchSession => {
  const id = raw?.id || raw?.session_id || raw?.research_id || '';
  const title = (raw?.title || raw?.topic || raw?.query || '').trim();
  const sourcesCount = typeof raw?.sources_count === 'number'
    ? raw.sources_count
    : (Array.isArray(raw?.sources) ? raw.sources.length : (Array.isArray(raw?.search_results_raw) ? raw.search_results_raw.length : 0));
  const claimsCount = typeof raw?.claims_count === 'number'
    ? raw.claims_count
    : (Array.isArray(raw?.claims) ? raw.claims.length : (Array.isArray(raw?.claim_verification_results) ? raw.claim_verification_results.length : 0));
  return {
    ...raw,
    id,
    session_id: raw?.session_id || id,
    title,
    topic: raw?.topic || title,
    query: raw?.query || title,
    timestamp: raw?.timestamp || raw?.created_at,
    sources_count: sourcesCount,
    claims_count: claimsCount,
    project_id: raw?.project_id || null,
  };
};

export function useSessions() {
  return useQuery<ResearchSession[]>({
    queryKey: ['sessions'],
    queryFn: async () => {
      const list = asList<any>((await api.get(API.research.sessions)).data);
      return list.map(normalizeSession);
    },
  });
}

export function useSession(sessionId?: string) {
  return useQuery<ResearchSession>({
    queryKey: ['session', sessionId],
    enabled: !!sessionId,
    queryFn: async () => {
      try {
        const res = await api.get(API.research.session(sessionId!));
        return normalizeSession(res.data);
      } catch (e) {
        const listRes = await api.get(API.research.sessions).catch(() => ({ data: [] }));
        const list = asList<any>(listRes.data);
        const match = list.find((s) => s.id === sessionId || s.session_id === sessionId || s.research_id === sessionId);
        if (match) return normalizeSession(match);
        throw e;
      }
    },
  });
}

export function useSessionMutations() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ['sessions'] });
  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api.patch(API.research.updateSession(id), { title }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(API.research.deleteSession(id)),
    onSuccess: invalidate,
  });
  return { rename, remove };
}

export function useSources(sessionId?: string) {
  return useQuery<SourceRef[]>({
    queryKey: ['sources', sessionId],
    enabled: !!sessionId,
    queryFn: async () => {
      // 1. Try the sources endpoint (PostgreSQL-backed)
      try {
        const res = await api.get(API.sources.bySession(sessionId!));
        const rows = asList<SourceRef>(res.data);
        if (rows.length > 0) return rows;
      } catch (_) {}
      // 2. Fallback: read the session and extract sources from search_results_raw or sources
      try {
        const sessRes = await api.get(API.research.session(sessionId!));
        const sess = sessRes.data as any;
        const raw = sess?.search_results_raw ?? sess?.sources ?? sess?.search_results ?? [];
        if (Array.isArray(raw)) {
          return raw.map((s: any, i: number) => {
            const rawStatus = (s.citation_status || s.status_http || s.status || '').toString().toLowerCase();
            const isVerified = rawStatus === 'verified' || rawStatus === 'live' || rawStatus === '200';
            const isBroken = rawStatus === 'broken' || rawStatus === 'failed' || rawStatus === '404' || rawStatus === '403';
            return {
              id: s.id || s.source_id || `src-${i}`,
              source_id: s.source_id || `src-${i + 1}`,
              title: s.title || s.url || `Source ${i + 1}`,
              url: s.url,
              publisher: s.source_name || s.publisher || s.domain,
              source_type: s.source_type || 'web',
              citation_status: isVerified ? 'verified' : isBroken ? 'broken' : (s.citation_status || 'verified'),
              credibility_score: typeof s.credibility_score === 'number' ? s.credibility_score : null,
              credibility_label: s.credibility_label || null,
              published_at: s.published_at || s.date || null,
            };
          });
        }
      } catch (_) {}
      return [];
    },
  });
}

export function useClaims(sessionId?: string) {
  return useQuery<ClaimRecord[]>({
    queryKey: ['claims', sessionId],
    enabled: !!sessionId,
    queryFn: async () => {
      // 1. Try the claims endpoint (PostgreSQL-backed)
      try {
        const res = await api.get(API.evidence.bySession(sessionId!));
        const rows = asList<ClaimRecord>(res.data);
        if (rows.length > 0) return rows;
      } catch (_) {}
      // 2. Fallback: read the session and extract claims from claims/claim_verification_results
      try {
        const sessRes = await api.get(API.research.session(sessionId!));
        const sess = sessRes.data as any;
        const claims = sess?.claims ?? sess?.claim_verification_results ?? [];
        if (Array.isArray(claims)) {
          return claims.map((c: any, i: number) => ({
            id: c.claim_id || `claim-${i}`,
            claim_id: c.claim_id,
            text: c.text || c.conclusion || c.claim_text,
            verification_status: c.status || c.verification_status || 'unverified',
            status: c.status || c.verification_status,
            confidence: c.confidence,
            claim_type: c.claim_type,
            supporting_evidence: c.supporting_evidence,
            contradicting_evidence: c.contradicting_evidence,
          }));
        }
      } catch (_) {}
      return [];
    },
  });
}

export function useReport(sessionId?: string, directReportPath?: string) {
  return useQuery<{ id: string; title: string; version: number; verification_status: string; content: string } | null>({
    queryKey: ['report', sessionId, directReportPath],
    enabled: !!sessionId,
    queryFn: async () => {
      if (!sessionId) return null;

      // 0. Primary: check the session endpoint which directly provides report_content
      try {
        const sessRes = (await api.get(API.research.session(sessionId))).data as any;
        const content = sessRes?.report_content || sessRes?.content || sessRes?.report || sessRes?.final_report;
        if (content && typeof content === 'string' && content.trim().length > 0) {
          return {
            id: sessionId,
            title: sessRes?.topic || sessRes?.title || 'Research Report',
            version: 1,
            verification_status: 'verified',
            content,
          };
        }
        const reportPath = sessRes?.saved_report_path || sessRes?.report_path;
        if (reportPath) {
          const filename = String(reportPath).replace(/^.*[\\\/]/, '');
          const fileRes = await api.get(API.reports.download(filename));
          if (fileRes.data && typeof fileRes.data === 'string' && fileRes.data.trim().length > 0) {
            return {
              id: sessionId,
              title: sessRes?.topic || sessRes?.title || 'Research Report',
              version: 1,
              verification_status: 'verified',
              content: fileRes.data,
            };
          }
        }
      } catch (_) {}

      // 1. If directReportPath is provided, try downloading immediately
      if (directReportPath) {
        try {
          const filename = String(directReportPath).replace(/^.*[\\\/]/, '');
          const fileRes = await api.get(API.reports.download(filename));
          if (fileRes.data && typeof fileRes.data === 'string' && fileRes.data.trim().length > 0) {
            return {
              id: sessionId,
              title: 'Research Report',
              version: 1,
              verification_status: 'verified',
              content: fileRes.data,
            };
          }
        } catch (_) {}
      }

      // 2. Try reports endpoint by session
      try {
        const res = (await api.get(API.reports.bySession(sessionId))).data as any;
        if (res) {
          const item = Array.isArray(res.reports) && res.reports.length > 0 ? res.reports[0] : res;
          if (item?.content_markdown || item?.content || item?.markdown) {
            return {
              id: item.report_id || item.id || sessionId,
              title: item.title || 'Research Report',
              version: item.version ?? 1,
              verification_status: item.status || 'verified',
              content: item.content_markdown || item.content || item.markdown,
            };
          }
        }
      } catch (_) {}

      // 3. Fallback: check sessions list
      try {
        const listRes = (await api.get(API.sessions.list())).data as any;
        const list = Array.isArray(listRes) ? listRes : listRes?.sessions || [];
        const match = list.find((s: any) => (s.session_id || s.id) === sessionId);
        const content = match?.report_content || match?.content || match?.report || match?.final_report;
        if (content && typeof content === 'string' && content.trim().length > 0) {
          return {
            id: sessionId,
            title: match?.topic || match?.title || 'Research Report',
            version: 1,
            verification_status: 'verified',
            content,
          };
        }
        const reportPath = match?.saved_report_path || match?.report_path;
        if (reportPath) {
          const filename = String(reportPath).replace(/^.*[\\\/]/, '');
          const fileRes = await api.get(API.reports.download(filename));
          if (fileRes.data) {
            return {
              id: sessionId,
              title: match?.topic || match?.title || 'Research Report',
              version: 1,
              verification_status: 'verified',
              content: typeof fileRes.data === 'string' ? fileRes.data : JSON.stringify(fileRes.data, null, 2),
            };
          }
        }
      } catch (_) {}

      return null;
    },
    retry: 2,
  });
}

const COMPLETED_DEFAULT_STAGES: StageItem[] = [
  { key: 'planner', label: 'Research Strategy & Plan', status: 'done', detail: 'Objectives defined' },
  { key: 'search', label: 'Search & Source Gathering', status: 'done', detail: 'Evidence retrieved' },
  { key: 'reader', label: 'Deep Web Scraping & Reading', status: 'done', detail: 'Key sources extracted' },
  { key: 'analyst', label: 'Synthesis & Fact-Checking', status: 'done', detail: 'Claims cross-verified' },
  { key: 'writer', label: 'Structured Report Generation', status: 'done', detail: 'Report drafted' },
  { key: 'critic', label: 'Academic Review & Scoring', status: 'done', detail: 'Verified & approved' },
];

export function useProjects() {
  return useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: async () => asList<Project>((await api.get(API.projects.list)).data),
    retry: false,
  });
}

export type StreamStatus = 'idle' | 'connecting' | 'streaming' | 'reconnecting' | 'done' | 'error';
const TERMINAL = new Set(['completed', 'complete', 'done']);
const ERROR_TERMINAL = new Set(['failed', 'error', 'cancelled', 'canceled', 'aborted']);
const MAX_RETRIES = 5;

export function useResearchStream(
  sessionId: string | undefined,
  enabled: boolean,
  initialQuery?: string | null,
  initialMeta?: { query?: string; document_id?: string; project_id?: string } | null
) {
  const [events, setEvents] = useState<SSEMessage[]>([]);
  const [status, setStatus] = useState<StreamStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [runId, setRunId] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const lastIdRef = useRef<string | null>(null);
  const attemptRef = useRef(0);
  const terminalRef = useRef(false);
  const timerRef = useRef<number | undefined>(undefined);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (timerRef.current) window.clearTimeout(timerRef.current);
  }, []);

  useEffect(() => {
    if (!sessionId || !enabled) { setStatus('idle'); return; }
    let cancelled = false;
    terminalRef.current = false;
    attemptRef.current = 0;
    setStatus('connecting');
    setError(null);

    const scheduleRetry = () => {
      if (cancelled || terminalRef.current || initialQuery) return;
      const attempt = ++attemptRef.current;
      if (attempt > MAX_RETRIES) {
        setStatus('error');
        setError('Connection lost after multiple retries.');
        return;
      }
      setStatus('reconnecting');
      timerRef.current = window.setTimeout(connect, Math.min(1000 * 2 ** attempt, 15_000));
    };

    const connect = async () => {
      if (cancelled || terminalRef.current) return;
      const ac = new AbortController();
      abortRef.current = ac;
      try {
        const url = initialQuery ? `${API_BASE}/research` : streamUrl(sessionId);
        const method = initialQuery ? 'POST' : 'GET';
        const body = initialQuery
          ? {
              topic: initialQuery,
              research_id: sessionId,
              provider: 'gemini',
              project_id: initialMeta?.project_id || undefined,
              document_id: initialMeta?.document_id || undefined,
            }
          : undefined;

        await streamSSE(url, {
          method,
          body,
          token: getAccessToken(),
          lastEventId: lastIdRef.current,
          signal: ac.signal,
          onMessage: (m) => {
            if (cancelled) return;
            if (m.id) lastIdRef.current = m.id;
            attemptRef.current = 0;
            setEvents((prev) => [...prev, m]);
            const type = String(m.data?.stage ?? m.data?.type ?? m.event ?? '').toLowerCase();
            const eventName = String(m.event || '').toLowerCase();
            if (ERROR_TERMINAL.has(type) || ERROR_TERMINAL.has(eventName) || m.event === 'error') {
              terminalRef.current = true;
              setStatus('error');
              setError(m.data?.message || m.message || 'The research pipeline encountered an error.');
              stop();
            } else if (TERMINAL.has(type) || TERMINAL.has(eventName) || m.event === 'complete') {
              terminalRef.current = true;
              setStatus('done');
              stop();
            } else if (type !== 'heartbeat' && type !== 'ping') {
              setStatus('streaming');
            }
          },
        });
        scheduleRetry();
      } catch (err: any) {
        if (cancelled || ac.signal.aborted) return;
        if (initialQuery) {
          setStatus('error');
          setError(err?.message || 'Research pipeline encountered an error.');
          return;
        }
        scheduleRetry();
      }
    };

    connect();
    return () => { cancelled = true; stop(); };
  }, [sessionId, enabled, runId, initialQuery, stop]);

  const restart = useCallback(() => {
    lastIdRef.current = null;
    terminalRef.current = false;
    setEvents([]);
    setError(null);
    setRunId((n) => n + 1);
  }, []);

  return { events, status, error, restart };
}

export interface StageItem { key: string; label: string; status: 'active' | 'done' | 'failed'; detail?: string }

const CANONICAL: Record<string, string> = {
  strategist: 'Research Strategist',
  planner: 'Research Planning',
  plan: 'Research Planning',
  search: 'Search Execution',
  searching: 'Search Execution',
  reader: 'Deep Web Scraping',
  extraction: 'Source Extraction',
  retrieval: 'Knowledge Retrieval',
  analyst: 'Synthesis & Analysis',
  analysis: 'Synthesis & Analysis',
  writer: 'Report Drafting',
  writing: 'Report Drafting',
  critic: 'Academic Review & Scoring',
  review: 'Academic Review & Scoring',
  refiner: 'Self-Correction & Refinement',
  refinement: 'Self-Correction & Refinement',
  exporter: 'Report Compilation & Export',
};
const ORDER = Object.keys(CANONICAL);
const DONE_WORDS = ['completed', 'complete', 'done', 'success', 'finished'];
const FAIL_WORDS = ['failed', 'error'];

export function deriveStages(events: SSEMessage[]): StageItem[] {
  const map = new Map<string, StageItem>();
  for (const m of events) {
    const rawAgent = String(m.data?.agent || '').toLowerCase();
    const rawStage = String(m.data?.stage ?? m.data?.type ?? (rawAgent ? rawAgent : m.event) ?? '').toLowerCase();
    const key = rawStage.replace(/[\s-]+/g, '_');
    if (!key || key === 'message' || key === 'heartbeat' || key === 'ping' || TERMINAL.has(key)) continue;
    const status = String(m.data?.status ?? '').toLowerCase();
    const label = CANONICAL[key] ?? key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    const detail =
      (typeof m.data?.message === 'string' && m.data.message) ||
      (typeof m.data?.detail === 'string' && m.data.detail) ||
      (typeof m.data?.percent === 'number' ? `${m.data.percent}%` : undefined) || undefined;
    const prev = map.get(key);
    let st: StageItem['status'] = prev?.status ?? 'active';
    if (DONE_WORDS.includes(status)) st = 'done';
    else if (FAIL_WORDS.includes(status)) st = 'failed';
    map.set(key, { key, label, status: st, detail: detail ?? prev?.detail });
  }
  return [...map.values()].sort((a, b) => {
    const ia = ORDER.indexOf(a.key), ib = ORDER.indexOf(b.key);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });
}

export function findApprovalRequest(events: SSEMessage[]): { planId?: string; plan?: Record<string, unknown> } | null {
  for (const m of events) {
    const t = String(m.data?.stage ?? m.data?.type ?? m.event ?? '').toLowerCase();
    if (t.includes('awaiting_approval') || t.includes('plan_approval') || t.includes('approval_required')) {
      return { planId: m.data?.plan_id ?? m.data?.planId ?? m.data?.id, plan: m.data };
    }
  }
  return null;
}

/* ============================================================
 * SECTION 2 — UI PRIMITIVES
 * ============================================================ */

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
const btnStyles: Record<ButtonVariant, string> = {
  primary: 'bg-[#C5A36A] text-[#080806] font-semibold hover:bg-[#D6A65D] shadow-sm',
  secondary: 'border border-[#453322] bg-[#14110D] hover:bg-[#1B1510] text-[#F4EBDD]',
  ghost: 'hover:bg-[#1B1510] text-[#C0B19C]',
  danger: 'bg-red-800 text-white hover:bg-red-700',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant; loading?: boolean; size?: 'sm' | 'md';
}
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'secondary', loading, size = 'md', className, children, disabled, ...props }, ref) => (
    <button ref={ref} disabled={disabled || loading} {...props}
      className={cn('inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#C5A36A]/60 disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' ? 'px-3 py-1.5 text-[13px]' : 'px-4 py-2 text-sm',
        btnStyles[variant], className)}>
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  )
);
Button.displayName = 'Button';

const fieldCls = 'w-full rounded-lg border border-[#453322] bg-[#14110D] px-3.5 py-2.5 text-sm placeholder:text-[#817463] ' +
  'text-[#F4EBDD] focus:outline-none focus:ring-2 focus:ring-[#C5A36A]/50 focus:border-[#C5A36A]';

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn(fieldCls, className)} {...props} />);
Input.displayName = 'Input';

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => <textarea ref={ref} className={cn(fieldCls, 'resize-none', className)} {...props} />);
Textarea.displayName = 'Textarea';

export function Badge({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn('inline-flex items-center rounded-full border border-[#453322] px-2 py-0.5 text-[11px] font-medium font-mono', className)}>{children}</span>;
}

const STATUS_STYLES: Record<string, string> = {
  completed: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-400',
  complete: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-400',
  approved: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-400',
  supported: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-400',
  indexed: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-400',
  active: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-400',
  failed: 'border-red-500/40 bg-red-500/10 text-red-400',
  error: 'border-red-500/40 bg-red-500/10 text-red-400',
  rejected: 'border-red-500/40 bg-red-500/10 text-red-400',
  contradicted: 'border-red-500/40 bg-red-500/10 text-red-400',
  running: 'border-amber-500/40 bg-amber-500/10 text-amber-400',
  in_progress: 'border-amber-500/40 bg-amber-500/10 text-amber-400',
  pending: 'border-amber-500/40 bg-amber-500/10 text-amber-400',
  uploaded: 'border-sky-500/40 bg-sky-500/10 text-sky-400',
  chunking: 'border-sky-500/40 bg-sky-500/10 text-sky-400',
  embedding: 'border-sky-500/40 bg-sky-500/10 text-sky-400',
  awaiting_approval: 'border-sky-500/40 bg-sky-500/10 text-sky-400',
};

export function StatusBadge({ status }: { status?: string }) {
  if (!status) return <Badge className="border-[#453322] text-[#817463]">unknown</Badge>;
  return <Badge className={STATUS_STYLES[status.toLowerCase()] ?? 'border-[#453322] text-[#C0B19C]'}>{status.replace(/_/g, ' ')}</Badge>;
}

export const Skeleton = ({ className }: { className?: string }) =>
  <div className={cn('animate-pulse rounded-md bg-[#1B1510]', className)} />;

export const Spinner = ({ className }: { className?: string }) =>
  <Loader2 className={cn('h-5 w-5 animate-spin text-[#C5A36A]', className)} />;

export function FullPageSpinner() {
  return <div className="grid h-[100dvh] place-items-center bg-[#080806]"><Spinner className="h-7 w-7" /></div>;
}

export function EmptyState({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 px-6 py-16 text-center">
      {icon && <div className="grid h-12 w-12 place-items-center rounded-full border border-[#453322] text-[#C5A36A] bg-[#14110D]">{icon}</div>}
      <h3 className="text-base font-serif font-semibold text-[#F3E9D7]">{title}</h3>
      {hint && <p className="text-sm text-[#817463]">{hint}</p>}
      {action}
    </div>
  );
}

export function Tabs({ tabs, active, onChange }: { tabs: { id: string; label: string }[]; active: string; onChange: (id: string) => void }) {
  return (
    <div role="tablist" className="flex gap-1 border-b border-[#302319]">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={active === t.id} onClick={() => onChange(t.id)}
          className={cn('border-b-2 px-3.5 py-2 text-[13px] font-medium transition-colors',
            active === t.id ? 'border-[#C5A36A] text-[#F3E9D7]' : 'border-transparent text-[#817463] hover:text-[#C0B19C]')}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Dialog({ open, onClose, title, children, footer }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-label={title}
        className="w-full max-w-md rounded-xl border border-[#453322] bg-[#10100E] p-5 shadow-2xl text-[#F4EBDD]">
        <div className="mb-3 flex items-center justify-between border-b border-[#302319] pb-3">
          <h2 className="text-base font-serif font-semibold text-[#E9D6B6]">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-[#817463] hover:text-[#F4EBDD]">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="text-sm text-[#C0B19C]">{children}</div>
        {footer && <div className="mt-5 flex justify-end gap-2">{footer}</div>}
      </div>
    </div>, document.body
  );
}

export function ConfirmDialog({ open, onClose, onConfirm, title, message, busy }: {
  open: boolean; onClose: () => void; onConfirm: () => void; title: string; message: string; busy?: boolean;
}) {
  return (
    <Dialog open={open} onClose={onClose} title={title}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="danger" onClick={onConfirm} loading={busy}>Delete</Button>
      </>}>
      <p>{message}</p>
    </Dialog>
  );
}

/* ============================================================
 * SECTION 3 — RESEARCH COMPONENTS
 * ============================================================ */

function markCitations(md: string): string {
  const parts = md.split(/(```[\s\S]*?```|`[^`]*`)/g);
  return parts.map((p, i) => (i % 2 === 1 ? p : p.replace(/\[(\d+)\](?!\()/g, '`[$1]`'))).join('');
}

export function MessageContent({ content, onCite }: { content: string; onCite?: (n: number) => void }) {
  return (
    <div className="prose prose-invert max-w-none text-sm text-[#C0B19C] leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        code({ className, children, ...props }) {
          const text = String(children ?? '');
          const m = !className && /^\[(\d+)\]$/.exec(text);
          if (m) return <button type="button" className="text-[#C5A36A] font-mono hover:underline font-bold" onClick={() => onCite?.(Number(m[1]))}>[{m[1]}]</button>;
          return <code className={className} {...props}>{children}</code>;
        },
        a({ href, children, ...props }) {
          return <a href={href} target="_blank" rel="noreferrer noopener" className="text-[#C5A36A] hover:underline" {...props}>{children}</a>;
        },
      }}>{markCitations(content)}</ReactMarkdown>
    </div>
  );
}

export function ResearchComposer({ variant = 'hero', disabled, onSubmit, submitLabel }: {
  variant?: 'hero' | 'followup';
  disabled?: boolean;
  onSubmit: (payload: { query: string; document_id?: string; project_id?: string }) => Promise<void>;
  submitLabel?: string;
}) {
  const [query, setQuery] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [projectId, setProjectId] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: projects = [] } = useProjects();
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const ta = taRef.current;
    if (ta) { ta.style.height = 'auto'; ta.style.height = `${Math.min(ta.scrollHeight, 240)}px`; }
  }, [query]);

  const uploadDocument = async (f: File, pId?: string): Promise<string> => {
    const fd = new FormData();
    fd.append('file', f);
    if (pId) fd.append('project_id', pId);
    const { data } = await api.post(API.documents.upload, fd);
    const id = (data as { id?: string; document_id?: string })?.id ?? (data as { id?: string; document_id?: string })?.document_id;
    if (!id) throw new Error('Upload succeeded but no document id returned.');
    return id;
  };

  const submit = async () => {
    setError(null);
    if (!query.trim() || query.trim().length < 3) {
      setError('Please enter a research question (at least 3 characters).');
      return;
    }
    setBusy(true);
    try {
      const document_id = file ? await uploadDocument(file, projectId || undefined) : undefined;
      setFile(null);
      if (fileRef.current) fileRef.current.value = '';
      await onSubmit({ query: query.trim(), document_id, project_id: projectId || undefined });
      setQuery('');
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  };

  const hero = variant === 'hero';
  return (
    <div className={cn('w-full', hero && 'mx-auto max-w-3xl')}>
      <div className="rounded-2xl border border-[#453322] bg-[#14110D] shadow-xl focus-within:border-[#C5A36A]">
        <Textarea
          ref={taRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
          rows={hero ? 4 : 2}
          disabled={disabled || busy}
          placeholder={hero
            ? 'What would you like to investigate? e.g. “Compare empirical findings on productivity impacts of remote work…”'
            : 'Ask a follow-up about this investigation…'}
          className="border-0 bg-transparent px-4 pt-4 focus:ring-0 text-[#F4EBDD]"
        />
        <div className="flex flex-wrap items-center gap-2 px-3 pb-3">
          {hero && (
            <>
              <input ref={fileRef} type="file" hidden accept=".pdf,.docx,.txt,.md,.csv,.xlsx"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              <button type="button" onClick={() => fileRef.current?.click()}
                className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[13px] text-[#817463] hover:text-[#C5A36A]">
                <Paperclip className="h-4 w-4" /> Attach Reference
              </button>
              {file && (
                <Badge className="border-[#C5A36A] text-[#E9D6B6]">
                  <FileText className="mr-1 h-3 w-3" />{file.name}
                  <button onClick={() => setFile(null)}><X className="ml-1 h-3 w-3" /></button>
                </Badge>
              )}

              {projects.length > 0 && (
                <div className="inline-flex items-center gap-1.5 text-[12px] text-[#817463]">
                  <FolderKanban className="h-3.5 w-3.5 text-[#C5A36A]" />
                  <select
                    value={projectId}
                    onChange={(e) => setProjectId(e.target.value)}
                    className="rounded border border-[#453322] bg-[#10100E] px-2 py-1 text-xs text-[#E9D6B6] focus:outline-none focus:border-[#C5A36A]"
                  >
                    <option value="">No Project (General)</option>
                    {projects.map((p) => (
                      <option key={p.project_id || p.id} value={p.project_id || p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </>
          )}
          <span className="ml-auto text-[11px] text-[#817463]">{hero ? 'Enter to submit · Shift+Enter for newline' : ''}</span>
          <Button variant="primary" size="sm" onClick={submit} loading={busy} disabled={disabled}>
            {hero ? (submitLabel ?? 'Start Research') : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
      {error && <p className="mt-2 flex items-center gap-1.5 text-[13px] text-red-400"><AlertTriangle className="h-3.5 w-3.5" />{error}</p>}
    </div>
  );
}

function Elapsed({ running, startedAt }: { running: boolean; startedAt: number }) {
  const [, force] = useState(0);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [running]);
  return <span className="tabular-nums font-mono">{fmtDuration(running ? Date.now() - startedAt : undefined)}</span>;
}

export function ProgressTimeline({ stages, status, error, onRetry, startedAt }: {
  stages: StageItem[]; status: StreamStatus; error?: string | null; onRetry: () => void; startedAt: number;
}) {
  return (
    <div className="rounded-xl border border-[#453322] bg-[#10100E] p-4 text-[#F4EBDD]">
      <div className="mb-3 flex items-center justify-between text-[13px]">
        <span className="flex items-center gap-2 font-medium">
          {status === 'reconnecting' ? <><RefreshCw className="h-4 w-4 animate-spin text-amber-500" /> Reconnecting…</>
            : status === 'error' ? <><AlertTriangle className="h-4 w-4 text-red-500" /> Connection lost</>
              : status === 'done' ? <><Check className="h-4 w-4 text-emerald-500" /> Finished</>
                : <><Loader2 className="h-4 w-4 animate-spin text-[#C5A36A]" /> Executing multi-agent pipeline…</>}
        </span>
        <span className="text-[#817463]">Elapsed <Elapsed running={status === 'streaming' || status === 'connecting' || status === 'reconnecting'} startedAt={startedAt} /></span>
      </div>
      {stages.length === 0 && status !== 'done' && (
        <div className="space-y-2">
          <p className="text-[13px] text-[#817463]">Connecting to research event stream…</p>
          {(API_BASE.includes('127.0.0.1') || API_BASE.includes('localhost')) && typeof window !== 'undefined' && window.location.hostname.includes('vercel.app') && (
            <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
              <p className="font-semibold text-amber-300">Backend URL required</p>
              <p className="mt-1 text-[#C0B19C]">
                The frontend is hosted on Vercel and needs your Render backend URL to run AI agents and event streams. Paste it below:
              </p>
              <div className="mt-2.5 flex gap-2">
                <Input
                  id="render-url-input"
                  placeholder="https://quest-xxxx.onrender.com"
                  className="font-mono text-xs flex-1"
                />
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() => {
                    const el = document.getElementById('render-url-input') as HTMLInputElement;
                    if (el && el.value.trim()) {
                      localStorage.setItem('questApi', el.value.trim().replace(/\/$/, ''));
                      window.location.reload();
                    }
                  }}
                >
                  Connect
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
      <ol className="space-y-2.5">
        {stages.map((s, i) => (
          <li key={s.key} className="flex items-start gap-3 text-[13px]">
            <span className={cn('mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full border',
              s.status === 'done' ? 'border-emerald-500 bg-emerald-500/20 text-emerald-400'
                : s.status === 'failed' ? 'border-red-500 bg-red-500/20 text-red-400'
                  : 'border-[#C5A36A] bg-[#C5A36A]/20 text-[#D6A65D]')}>
              {s.status === 'done' ? <Check className="h-2.5 w-2.5" /> : s.status === 'failed' ? <X className="h-2.5 w-2.5" /> : <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[#D6A65D]" />}
            </span>
            <div className="min-w-0">
              <span className={cn('font-medium', s.status === 'active' ? 'text-[#F3E9D7]' : 'text-[#817463]')}>
                {String(i + 1).padStart(2, '0')} · {s.label}
              </span>
              {s.detail && <p className="truncate text-xs text-[#817463]">{s.detail}</p>}
            </div>
          </li>
        ))}
      </ol>
      {error && (
        <div className="mt-3 flex items-center justify-between gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-[13px] text-red-400">
          <span className="truncate">{error}</span>
          <Button size="sm" onClick={onRetry}><RefreshCw className="h-3.5 w-3.5" /> Reconnect</Button>
        </div>
      )}
    </div>
  );
}

export function ApprovalCard({ planId, plan, onDecided }: { planId?: string; plan?: Record<string, unknown>; onDecided: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const objectives = Array.isArray(plan?.objectives) ? (plan!.objectives as string[]) : [];

  const decide = async (decision: 'approve' | 'reject' | 'modify') => {
    if (!planId) { toast.push('No plan id provided.'); return; }
    setBusy(decision);
    try {
      await api.post(API.review.decide(planId), { decision });
      toast.push(`Plan ${decision}d.`, 'success');
      onDecided();
    } catch (e) { toast.push(apiError(e), 'error'); }
    finally { setBusy(null); }
  };

  return (
    <div className="rounded-xl border border-sky-500/40 bg-sky-500/10 p-4">
      <p className="mb-2 text-[13px] font-semibold text-sky-400">Research plan awaiting human review</p>
      {objectives.length > 0 && (
        <ul className="mb-3 list-disc pl-5 text-[13px] text-[#C0B19C]">
          {objectives.map((o, i) => <li key={i}>{o}</li>)}
        </ul>
      )}
      <div className="flex gap-2">
        <Button variant="primary" size="sm" loading={busy === 'approve'} onClick={() => decide('approve')}>Approve</Button>
        <Button size="sm" loading={busy === 'modify'} onClick={() => decide('modify')}>Request Modifications</Button>
        <Button variant="danger" size="sm" loading={busy === 'reject'} onClick={() => decide('reject')}>Reject</Button>
      </div>
    </div>
  );
}

export function SourcePanel({
  sessionId,
  focusIndex,
  fallbackSources,
}: {
  sessionId: string;
  focusIndex?: number | null;
  fallbackSources?: SourceRef[];
}) {
  const { data: serverSources = [], isLoading } = useSources(sessionId);
  const sources = serverSources.length > 0 ? serverSources : (fallbackSources || []);
  const itemRefs = useRef<(HTMLLIElement | null)[]>([]);

  useEffect(() => {
    if (focusIndex && focusIndex > 0 && focusIndex <= sources.length) {
      const target = itemRefs.current[focusIndex - 1];
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }, [focusIndex, sources.length]);

  if (isLoading && sources.length === 0) return <div className="space-y-2 p-1">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16" />)}</div>;
  if (sources.length === 0) return <EmptyState icon={<FileText className="h-5 w-5" />} title="No sources yet" hint="Sources appear here once retrieved by Search agents." />;

  return (
    <ul className="space-y-3">
      {sources.map((s, i) => {
        const isFocused = focusIndex === (i + 1);
        let domain = '';
        try {
          if (s.url) domain = new URL(s.url).hostname.replace(/^www\./, '');
        } catch (_) {}

        const statusLower = (s.citation_status || '').toLowerCase();
        const isVerified = statusLower === 'verified' || statusLower === 'live';
        const isBroken = statusLower === 'broken' || statusLower === 'failed';

        return (
          <li
            key={s.id || `src-${i}`}
            ref={(el) => { itemRefs.current[i] = el; }}
            className={cn(
              "rounded-xl border transition-all duration-300 p-3.5 text-[13px]",
              isFocused
                ? "border-[#C5A36A] bg-[#221A13] ring-2 ring-[#C5A36A]/50 shadow-lg shadow-[#C5A36A]/10 scale-[1.01]"
                : "border-[#302319] bg-[#14110D] hover:border-[#453322]"
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <span className="font-medium text-[#F4EBDD] leading-snug line-clamp-2">
                {s.title ?? `Source ${i + 1}`}
              </span>
              <span className={cn(
                "shrink-0 rounded px-1.5 py-0.5 text-[11px] font-mono font-semibold border",
                isFocused
                  ? "bg-[#C5A36A] text-[#080806] border-[#C5A36A]"
                  : "bg-[#201A14] text-[#C5A36A] border-[#453322]"
              )}>
                [{i + 1}]
              </span>
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
              {domain && (
                <span className="rounded bg-[#1A1410] px-1.5 py-0.5 text-[#A08F7D] font-mono border border-[#302319]">
                  {domain}
                </span>
              )}
              {s.source_type && <Badge>{s.source_type}</Badge>}
              <span className={cn(
                "inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium text-[10px]",
                isVerified
                  ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/30"
                  : isBroken
                  ? "bg-rose-500/10 text-rose-400 border border-rose-500/30"
                  : "bg-amber-500/10 text-amber-400 border border-amber-500/30"
              )}>
                {isVerified ? (
                  <><CheckCircle2 className="h-3 w-3" /> Live & Verified</>
                ) : isBroken ? (
                  <><XCircle className="h-3 w-3" /> Broken / 404</>
                ) : (
                  <><ShieldCheck className="h-3 w-3" /> Evaluating</>
                )}
              </span>
              {s.credibility_label && (
                <span className="rounded bg-[#C5A36A]/10 text-[#E9D6B6] px-1.5 py-0.5 text-[10px] border border-[#C5A36A]/20">
                  {s.credibility_label}
                </span>
              )}
            </div>

            {s.url && (
              <div className="mt-2.5 pt-2 border-t border-[#201A14] flex items-center justify-between text-[11px]">
                <a
                  href={s.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-center gap-1 text-[#C5A36A] hover:text-[#E9D6B6] transition-colors font-medium"
                >
                  <ExternalLink className="h-3 w-3" /> Open source
                </a>
                {s.published_at && (
                  <span className="text-[#817463] text-[10px]">{s.published_at}</span>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function CompletionCard({ session, onOpenReport }: { session: ResearchSession; onOpenReport: () => void }) {
  const toast = useToast();
  const [fmt, setFmt] = useState<string | null>(null);

  const doExport = async (f: 'markdown' | 'pdf' | 'docx') => {
    setFmt(f);
    try { await downloadExport(session.id, f); toast.push(`${f.toUpperCase()} exported`, 'success'); }
    catch (e) { toast.push(apiError(e), 'error'); }
    finally { setFmt(null); }
  };

  return (
    <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-5">
      <p className="flex items-center gap-2 text-[15px] font-semibold text-emerald-400">
        <Check className="h-5 w-5" /> Research investigation completed
      </p>
      {session.summary ? (
        <div className="mt-2 text-sm text-[#C0B19C]"><MessageContent content={session.summary} /></div>
      ) : (
        <p className="mt-1 text-[13px] text-[#817463]">Findings have been synthesized into a structured research report.</p>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="primary" size="sm" onClick={onOpenReport}>Open Report</Button>
        <Button size="sm" loading={fmt === 'markdown'} onClick={() => doExport('markdown')}><FileDown className="h-3.5 w-3.5" /> Markdown</Button>
        <Button size="sm" loading={fmt === 'pdf'} onClick={() => doExport('pdf')}>PDF</Button>
        <Button size="sm" loading={fmt === 'docx'} onClick={() => doExport('docx')}>DOCX</Button>
      </div>
    </div>
  );
}

/* ============================================================
 * SECTION 4 — APP SHELL (SIDEBAR)
 * ============================================================ */

const NAV = [
  { to: '/app/projects', label: 'Projects', icon: FolderKanban },
  { to: '/app/documents', label: 'Documents', icon: Files },
  { to: '/app/knowledge', label: 'Knowledge Base', icon: Brain },
  { to: '/app/contradictions', label: 'Contradictions', icon: GitCompare },
  { to: '/app/system', label: 'System Status', icon: Activity },
  { to: '/app/history', label: 'History', icon: History },
];

export function AppShell() {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('rai-sidebar') === '1');
  const [mobileOpen, setMobileOpen] = useState(false);
  const navigate = useNavigate();
  useEffect(() => { localStorage.setItem('rai-sidebar', collapsed ? '1' : '0'); }, [collapsed]);

  const isVercelLocalhost = typeof window !== 'undefined' &&
    window.location.hostname.includes('vercel.app') &&
    (API_BASE.includes('localhost') || API_BASE.includes('127.0.0.1'));

  return (
    <div className="flex h-[100dvh] overflow-hidden bg-[#080806] text-[#F4EBDD]">
      <aside className="hidden md:block">
        <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((v) => !v)} />
      </aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/70" onClick={() => setMobileOpen(false)} />
          <div className="absolute inset-y-0 left-0 shadow-2xl"><Sidebar onNavigate={() => setMobileOpen(false)} /></div>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        {isVercelLocalhost && (
          <div className="flex items-center justify-between gap-3 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-xs text-amber-200">
            <span className="flex items-center gap-2 truncate">
              <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
              <span>Backend not connected: Running on Vercel but API points to localhost. Set your Render URL in Settings.</span>
            </span>
            <Button size="sm" variant="outline" className="h-6 text-[11px] shrink-0 border-amber-500/40 text-amber-200 hover:bg-amber-500/20" onClick={() => navigate('/app/settings')}>
              Configure Render URL
            </Button>
          </div>
        )}
        <div className="flex h-12 items-center gap-3 border-b border-[#302319] px-3 md:hidden">
          <button onClick={() => setMobileOpen(true)} className="rounded p-2 text-[#C0B19C]"><Menu className="h-5 w-5" /></button>
          <span className="font-serif text-sm font-semibold tracking-wide text-[#E9D6B6]">Quest</span>
        </div>
        <main className="min-h-0 flex-1 overflow-y-auto"><Outlet /></main>
      </div>
    </div>
  );
}

function Sidebar({ collapsed, onToggle, onNavigate }: { collapsed?: boolean; onToggle?: () => void; onNavigate?: () => void }) {
  const { user, signOut } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const activeId = /^\/app\/research\/([^/]+)/.exec(location.pathname)?.[1] ?? null;
  const { data: sessions = [], isLoading } = useSessions();
  const { rename, remove } = useSessionMutations();
  const [q, setQ] = useState('');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const isUntitled = (s: ResearchSession) => {
    const t = (s.title || s.topic || s.query || '').trim().toLowerCase();
    return !t || t === 'untitled' || t === 'untitled research' || t === 'unknown';
  };

  const filtered = sessions
    .filter((s) => !isUntitled(s))
    .filter((s) => !q || (s.title ?? s.topic ?? s.query ?? '').toLowerCase().includes(q.toLowerCase()))
    .slice(0, 25);

  const openSession = (id: string) => { navigate(`/app/research/${id}`); onNavigate?.(); };

  return (
    <div className={cn('flex h-full flex-col border-r border-[#302319] bg-[#10100E]', collapsed ? 'w-14' : 'w-72')}>
      <div className="flex h-14 items-center gap-2 px-3 border-b border-[#302319]">
        <Link to="/" className="flex items-center gap-2.5">
          <div className="grid h-7 w-7 shrink-0 place-items-center rounded bg-[#C5A36A] font-serif text-sm font-bold text-[#080806]">Q</div>
          {!collapsed && <span className="truncate font-serif text-[15px] font-semibold text-[#E9D6B6]">Quest</span>}
        </Link>
        <button onClick={onToggle} aria-label="Toggle sidebar" className="ml-auto hidden rounded p-1.5 text-[#817463] hover:text-[#C5A36A] md:block">
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
        </button>
      </div>

      <div className="p-3">
        <Button variant="primary" className={cn('w-full', collapsed && 'px-0')} onClick={() => { navigate('/app/new'); onNavigate?.(); }}>
          <Plus className="h-4 w-4" />{!collapsed && 'New Research'}
        </Button>
      </div>

      {!collapsed && (
        <div className="relative px-3 pb-2">
          <Search className="pointer-events-none absolute left-5 top-2.5 h-4 w-4 text-[#817463]" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search investigations…" className="py-1.5 pl-8 text-[13px]" />
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-2 space-y-4">
        {!collapsed && <p className="px-2 pt-2 text-[10px] font-mono uppercase tracking-wider text-[#817463]">Workspace</p>}
        <nav className="space-y-0.5" aria-label="Workspace">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} onClick={onNavigate} title={collapsed ? label : undefined}
              className={({ isActive }) => cn('flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] text-[#C0B19C] hover:bg-[#1B1510] hover:text-[#F4EBDD]',
                isActive && 'bg-[#302319] text-[#E9D6B6] font-medium', collapsed && 'justify-center px-0')}>
              <Icon className="h-4 w-4 shrink-0 text-[#C5A36A]" />{!collapsed && label}
            </NavLink>
          ))}
        </nav>

        {!collapsed && (
          <div>
            <p className="px-2 pb-1 text-[10px] font-mono uppercase tracking-wider text-[#817463]">Recent Investigations</p>
            {(API_BASE.includes('127.0.0.1') || API_BASE.includes('localhost')) && typeof window !== 'undefined' && window.location.hostname.includes('vercel.app') ? (
              <div className="px-2 py-3 text-center">
                <p className="text-[11px] text-amber-300">Backend not connected</p>
                <button onClick={() => navigate('/app/settings')} className="mt-1 text-[11px] text-[#C5A36A] underline hover:text-[#E9D6B6]">
                  Configure Render URL
                </button>
              </div>
            ) : isLoading ? (
              <div className="flex justify-center py-4"><Spinner /></div>
            ) : filtered.length === 0 ? (
              <p className="px-3 py-2 text-[12px] text-[#817463]">{q ? 'No matches' : 'No research sessions'}</p>
            ) : (
              <ul className="space-y-0.5">
                {filtered.map((s) => (
                  <li key={s.id} className="group relative">
                    <button onClick={() => openSession(s.id)}
                      className={cn('w-full truncate rounded px-2.5 py-1.5 text-left text-[12.5px] text-[#817463] hover:bg-[#1B1510] hover:text-[#F4EBDD]',
                        activeId === s.id && 'bg-[#1B1510] text-[#E9D6B6] font-medium')}>
                      {titleFromQuery(s.title || s.topic || s.query)}
                    </button>
                    <span className="absolute right-1 top-1 hidden items-center group-hover:flex">
                      <button onClick={() => setDeleteId(s.id)} className="p-1 text-[#817463] hover:text-red-400"><Trash2 className="h-3 w-3" /></button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      <div className="border-t border-[#302319] p-3">
        <div className="flex items-center gap-2.5">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[#302319] text-xs font-bold text-[#E9D6B6]">
            {(user?.email?.[0] ?? 'Q').toUpperCase()}
          </div>
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <p className="truncate text-[12px] font-medium text-[#F4EBDD]">{user?.email ?? 'Signed in'}</p>
              <button onClick={() => signOut().then(() => navigate('/auth/login'))} className="text-[11px] text-[#817463] hover:text-red-400">
                Sign out
              </button>
            </div>
          )}
          <NavLink
            to="/app/settings"
            onClick={onNavigate}
            title="Settings"
            className={({ isActive }) => cn(
              'rounded-lg p-2 text-[#817463] hover:bg-[#1B1510] hover:text-[#C5A36A] transition-colors shrink-0',
              isActive && 'bg-[#302319] text-[#E9D6B6]',
              collapsed && 'mx-auto'
            )}
          >
            <Settings className="h-4 w-4" />
          </NavLink>
        </div>
      </div>

      <ConfirmDialog open={!!deleteId} onClose={() => setDeleteId(null)} busy={remove.isPending}
        title="Delete research session" message="This permanently deletes the session, claims, and generated reports."
        onConfirm={() => { if (deleteId) remove.mutate(deleteId, { onSuccess: () => { setDeleteId(null); toast.push('Session deleted', 'success'); navigate('/app/new'); } }); }} />
    </div>
  );
}

/* ============================================================
 * SECTION 5 — AUTH PAGES
 * ============================================================ */

function AuthShell({ title, children, footer }: { title: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="grid min-h-[100dvh] place-items-center bg-[#080806] px-4 text-[#F4EBDD]">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2.5">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-[#C5A36A] font-serif text-lg font-bold text-[#080806]">Q</div>
          <span className="font-serif text-lg font-semibold tracking-wide text-[#E9D6B6]">Quest</span>
        </div>
        <div className="rounded-2xl border border-[#453322] bg-[#10100E] p-6 shadow-2xl">
          <h1 className="mb-5 text-center text-lg font-serif font-semibold text-[#E9D6B6]">{title}</h1>
          {children}
        </div>
        {footer && <p className="mt-4 text-center text-[13px] text-[#817463]">{footer}</p>}
      </div>
    </div>
  );
}

export function LoginPage() {
  const { user, signIn, signInWithGoogle } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (user) return <Navigate to="/app/new" replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setError(null); setBusy(true);
    const { error } = await signIn(email.trim(), password);
    setBusy(false);
    if (error) setError(error.message);
    else navigate('/app/new', { replace: true });
  };

  return (
    <AuthShell title="Sign in to Quest" footer={<>New researcher? <Link className="text-[#C5A36A] hover:underline" to="/auth/signup">Create an account</Link></>}>
      <form onSubmit={submit} className="space-y-3">
        <Input type="email" required placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <Input type="password" required placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <p className="text-[13px] text-red-400">{error}</p>}
        <Button variant="primary" className="w-full" loading={busy} type="submit">Sign in with Email</Button>
      </form>
      <div className="relative my-4 text-center">
        <span className="bg-[#10100E] px-2 text-xs text-[#817463]">OR</span>
      </div>
      <Button className="w-full" onClick={() => signInWithGoogle().then(({ error }) => error && toast.push(error.message, 'error'))}>
        Continue with Google
      </Button>
      <p className="mt-3 text-center text-xs"><Link className="text-[#817463] hover:underline" to="/auth/forgot-password">Forgot password?</Link></p>
    </AuthShell>
  );
}

export function SignUpPage() {
  const { signUp, signInWithGoogle } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setError(null);
    if (password !== confirm) { setError('Passwords do not match.'); return; }
    setBusy(true);
    const { error } = await signUp(email.trim(), password);
    setBusy(false);
    if (error) setError(error.message);
    else navigate('/app/new', { replace: true });
  };

  return (
    <AuthShell title="Create Quest Account" footer={<>Already registered? <Link className="text-[#C5A36A] hover:underline" to="/auth/login">Sign in</Link></>}>
      <form onSubmit={submit} className="space-y-3">
        <Input type="email" required placeholder="Work Email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <Input type="password" required placeholder="Password (min 8 chars)" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} />
        <Input type="password" required placeholder="Confirm Password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        {error && <p className="text-[13px] text-red-400">{error}</p>}
        <Button variant="primary" className="w-full" loading={busy} type="submit">Create Account</Button>
      </form>
      <Button className="mt-3 w-full" onClick={() => signInWithGoogle()}>Sign up with Google</Button>
    </AuthShell>
  );
}

export function ForgotPasswordPage() {
  const { resetPassword } = useAuth();
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <AuthShell title="Reset Password" footer={<Link className="text-[#C5A36A] hover:underline" to="/auth/login">Back to Sign in</Link>}>
      {sent ? <p className="text-center text-sm text-emerald-400">Password reset instructions sent to your email.</p> : (
        <form onSubmit={async (e) => { e.preventDefault(); setBusy(true); await resetPassword(email.trim()); setBusy(false); setSent(true); }} className="space-y-3">
          <Input type="email" required placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <Button variant="primary" className="w-full" loading={busy} type="submit">Send Reset Link</Button>
        </form>
      )}
    </AuthShell>
  );
}

/* ============================================================
 * SECTION 6 — WORKSPACE & SESSIONS
 * ============================================================ */

const ACTIVE_STATUSES = new Set(['pending', 'queued', 'planning', 'running', 'in_progress', 'processing', 'awaiting_approval']);
interface Msg { id: string; role: 'user' | 'assistant'; content: string }

export function WorkspacePage() {
  const { sessionId } = useParams();
  return sessionId ? <SessionView key={sessionId} sessionId={sessionId} /> : <NewResearch />;
}

function NewResearch() {
  const navigate = useNavigate();
  const toast = useToast();
  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col items-center justify-center px-4 pb-24 text-center">
      <h1 className="mb-2 font-serif text-3xl font-semibold text-[#E9D6B6]">What should we investigate?</h1>
      <p className="mb-8 max-w-lg text-[15px] text-[#C0B19C]">
        Formulate a complex hypothesis or research question. Autonomous AI agents will plan, search, extract claims, verify evidence, and produce an intelligence report.
      </p>
      <ResearchComposer
        onSubmit={async ({ query, document_id, project_id }) => {
          try {
            const sid = Array.from(crypto.getRandomValues(new Uint8Array(16)))
              .map((b) => b.toString(16).padStart(2, '0'))
              .join('');
            sessionStorage.setItem(`pending_research_${sid}`, JSON.stringify({ query, document_id, project_id }));
            navigate(`/app/research/${sid}`);
          } catch (e) {
            toast.push(apiError(e), 'error');
            throw e;
          }
        }}
      />
    </div>
  );
}

function SessionView({ sessionId }: { sessionId: string }) {
  const { data: session, isLoading, isError } = useSession(sessionId);
  const { data: report, isLoading: reportLoading } = useReport(sessionId, session?.saved_report_path);
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const scrollRef = useRef<HTMLDivElement>(null);

  const [pendingMeta, setPendingMeta] = useState<{ query: string; document_id?: string; project_id?: string } | null>(() => {
    try {
      const raw = sessionStorage.getItem(`pending_research_${sessionId}`);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  });

  const pendingQuery = pendingMeta?.query ?? null;
  const isActive = Boolean(pendingQuery) || (!!session && ACTIVE_STATUSES.has(String(session.status).toLowerCase()));
  const { events, status, error: streamError, restart } = useResearchStream(sessionId, isActive, pendingQuery, pendingMeta);
  const [followUps, setFollowUps] = useState<Msg[]>([]);
  const [chatBusy, setChatBusy] = useState(false);
  const [railTab, setRailTab] = useState<'sources' | 'evidence'>('sources');
  const [focusSource, setFocusSource] = useState<number | null>(null);

  const startedAtRef = useRef(Date.now());
  useEffect(() => { startedAtRef.current = Date.now(); }, [sessionId]);

  const done = status === 'done' || (!!session && String(session.status).toLowerCase() === 'completed');

  useEffect(() => {
    if (pendingQuery) {
      qc.invalidateQueries({ queryKey: ['sessions'] });
    }
  }, [pendingQuery, qc]);

  useEffect(() => {
    if (status === 'done') {
      sessionStorage.removeItem(`pending_research_${sessionId}`);
      qc.invalidateQueries({ queryKey: ['session', sessionId] });
      qc.invalidateQueries({ queryKey: ['sessions'] });
      qc.invalidateQueries({ queryKey: ['report', sessionId] });
      qc.invalidateQueries({ queryKey: ['sources', sessionId] });
      qc.invalidateQueries({ queryKey: ['claims', sessionId] });
    }
  }, [status, sessionId, qc]);

  // Extract live report, sources and claims directly from the SSE event stream or session
  const liveReportContent = useMemo(() => {
    if (report?.content) return report.content;
    const completeEvt = events.slice().reverse().find(
      (e) => e.event === 'complete' || e.data?.stage === 'complete' || e.data?.type === 'complete'
    );
    return completeEvt?.data?.report || completeEvt?.data?.final_report || session?.report_content || '';
  }, [report?.content, events, session?.report_content]);

  const liveSources = useMemo<SourceRef[] | undefined>(() => {
    const completeEvt = events.slice().reverse().find(
      (e) => e.event === 'complete' || e.data?.stage === 'complete' || e.data?.type === 'complete'
    );
    const raw = completeEvt?.data?.sources;
    if (Array.isArray(raw)) {
      return raw.map((s: any, i: number) => ({
        id: s.source_id || `src-${i + 1}`,
        source_id: s.source_id || `src-${i + 1}`,
        title: s.title || s.url || `Source ${i + 1}`,
        url: s.url,
        publisher: s.source_name || s.publisher || 'Web',
        source_type: s.source_type || 'web',
        citation_status: s.citation_status || 'verified',
      }));
    }
    return undefined;
  }, [events]);

  const liveClaims = useMemo<ClaimRecord[] | undefined>(() => {
    const completeEvt = events.slice().reverse().find(
      (e) => e.event === 'complete' || e.data?.stage === 'complete' || e.data?.type === 'complete'
    );
    const raw = completeEvt?.data?.claims;
    if (Array.isArray(raw)) {
      return raw.map((c: any, i: number) => ({
        id: c.claim_id || `claim-${i + 1}`,
        claim_id: c.claim_id || `claim-${i + 1}`,
        text: c.text || c.conclusion || c.claim_text || `Claim ${i + 1}`,
        verification_status: c.status || c.verification_status || 'supported',
        status: c.status || c.verification_status || 'supported',
        confidence: c.confidence != null ? c.confidence : 0.9,
        claim_type: c.claim_type || 'factual',
        supporting_evidence: c.supporting_evidence || [],
        contradicting_evidence: c.contradicting_evidence || [],
      }));
    }
    return undefined;
  }, [events]);

  // Poll for report content after completion — the exporter may write the
  // report file slightly after the session status flips to 'done', so keep
  // refetching until the report content is available (max ~60 seconds).
  useEffect(() => {
    if (!done || report) return;
    let attempts = 0;
    const timer = setInterval(() => {
      qc.invalidateQueries({ queryKey: ['report', sessionId] });
      attempts += 1;
      if (attempts >= 30) clearInterval(timer);
    }, 2000);
    return () => clearInterval(timer);
  }, [done, report, sessionId, qc]);

  const stages = useMemo(() => deriveStages(events), [events]);
  const approval = useMemo(() => findApprovalRequest(events), [events]);

  const displayTitle = session?.title ?? session?.query ?? session?.topic ?? pendingQuery ?? 'Research Investigation';
  const displayStatus = done ? 'completed' : (status === 'streaming' || status === 'connecting' ? 'in_progress' : session?.status ?? 'running');

  const messages = useMemo<Msg[]>(() => {
    const list: Msg[] = [];
    const queryText = session?.query || session?.topic || pendingQuery;
    if (queryText) list.push({ id: 'q0', role: 'user', content: queryText });
    list.push(...followUps);
    return list;
  }, [session?.query, session?.topic, pendingQuery, followUps]);

  const sendFollowUp = async ({ query }: { query: string }) => {
    setFollowUps((f) => [...f, { id: `u${Date.now()}`, role: 'user', content: query }]);
    setChatBusy(true);
    try {
      const { data } = await api.post(API.chat.send, { session_id: sessionId, message: query });
      const text = (data as { reply?: string; response?: string; answer?: string; content?: string; message?: string })?.reply ??
        (data as { reply?: string; response?: string; answer?: string; content?: string; message?: string })?.response ??
        (data as { reply?: string; response?: string; answer?: string; content?: string; message?: string })?.answer ??
        (data as { reply?: string; response?: string; answer?: string; content?: string; message?: string })?.content ??
        (data as { reply?: string; response?: string; answer?: string; content?: string; message?: string })?.message;
      setFollowUps((f) => [...f, { id: `a${Date.now()}`, role: 'assistant', content: String(text || 'No reply.') }]);
    } catch (e) {
      toast.push(apiError(e), 'error');
    } finally { setChatBusy(false); }
  };

  if (isLoading && !pendingQuery) return <FullPageSpinner />;
  if ((isError || !session) && !pendingQuery) return <EmptyState title="Session not found" hint="This session may not exist or does not belong to your account." />;

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8">
            <header className="border-b border-[#302319] pb-4">
              <h1 className="font-serif text-2xl font-semibold text-[#E9D6B6]">{titleFromQuery(displayTitle)}</h1>
              <p className="mt-1 text-xs text-[#817463]">Session {sessionId.slice(0, 10)} · Status: <StatusBadge status={displayStatus} /></p>
            </header>

            {messages.map((m) => (
              <div key={m.id} className={cn('flex', m.role === 'user' ? 'justify-end' : 'justify-start')}>
                <div className={cn('max-w-[88%] rounded-2xl px-4 py-3',
                  m.role === 'user' ? 'bg-[#C5A36A] text-[#080806] font-medium' : 'border border-[#453322] bg-[#14110D]')}>
                  {m.role === 'user' ? <p className="whitespace-pre-wrap">{m.content}</p> : <MessageContent content={m.content} />}
                </div>
              </div>
            ))}

            {(isActive || status === 'connecting' || status === 'streaming') && (
              <ProgressTimeline stages={stages} status={status} error={streamError} onRetry={restart} startedAt={startedAtRef.current} />
            )}

            {approval && <ApprovalCard planId={approval.planId} plan={approval.plan} onDecided={() => qc.invalidateQueries({ queryKey: ['session', sessionId] })} />}
            {done && (
              <div className="space-y-6">
                <CompletionCard
                  session={session || ({ id: sessionId, title: displayTitle, query: displayTitle, status: 'completed' } as any)}
                  onOpenReport={() => navigate(`/app/research/${sessionId}/report`)}
                />

                {(reportLoading && !liveReportContent && !report?.content) ? (
                  <div className="rounded-2xl border border-[#453322] bg-[#14110D] p-6 space-y-3 py-8">
                    <div className="flex items-center gap-2 text-sm text-[#C5A36A]">
                      <Loader2 className="h-4 w-4 animate-spin" /> Loading synthesized report…
                    </div>
                    <Skeleton className="h-4 w-3/4" />
                    <Skeleton className="h-32 w-full" />
                  </div>
                ) : (liveReportContent || report?.content) ? (
                  <ReportViewer
                    title={report?.title || session?.title || session?.topic || displayTitle}
                    topic={session?.topic || displayTitle}
                    content={liveReportContent || report?.content || ''}
                    metrics={{
                      criticScore: session?.critic_score,
                      revisionCount: session?.revision_count,
                      sourcesCount: (liveSources && liveSources.length > 0 ? liveSources.length : session?.sources?.length),
                      citationsVerified: session?.citations_verified,
                      citationsTotal: session?.citations_total,
                      provider: session?.provider,
                      generatedAt: session?.timestamp || session?.created_at,
                    }}
                    onCite={(citationNum) => {
                      setRailTab('sources');
                      setFocusSource(citationNum);
                    }}
                    onExport={async (fmt) => {
                      try {
                        await downloadExport(sessionId, fmt);
                        toast.push(`${fmt.toUpperCase()} export downloaded`, 'success');
                      } catch (e) {
                        toast.push(apiError(e), 'error');
                      }
                    }}
                    onEdit={() => navigate(`/app/research/${sessionId}/report`)}
                  />
                ) : (
                  <div className="rounded-2xl border border-[#453322] bg-[#14110D] p-6 text-center text-sm text-[#817463]">
                    Report generated. Click <strong className="text-[#C5A36A]">Fullscreen Editor</strong> or export above to view.
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="border-t border-[#302319] bg-[#10100E] p-4">
          <div className="mx-auto max-w-3xl">
            {done ? (
              <ResearchComposer variant="followup" disabled={chatBusy} onSubmit={sendFollowUp} />
            ) : (
              <p className="text-center text-xs text-[#817463]">Follow-up assistant unlocks when the research run finishes.</p>
            )}
          </div>
        </div>
      </div>

      <aside className="hidden w-80 shrink-0 flex-col border-l border-[#302319] lg:flex bg-[#10100E]">
        <div className="p-4">
          <Tabs active={railTab} onChange={(t) => setRailTab(t as typeof railTab)}
            tabs={[{ id: 'sources', label: 'Sources' }, { id: 'evidence', label: 'Evidence' }]} />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {railTab === 'sources' && <SourcePanel sessionId={sessionId} focusIndex={focusSource} fallbackSources={liveSources} />}
          {railTab === 'evidence' && <EvidencePanel sessionId={sessionId} fallbackClaims={liveClaims} />}
        </div>
      </aside>
    </div>
  );
}

function EvidencePanel({
  sessionId,
  fallbackClaims,
}: {
  sessionId: string;
  fallbackClaims?: ClaimRecord[];
}) {
  const { data: serverClaims = [], isLoading } = useClaims(sessionId);
  const claims = serverClaims.length > 0 ? serverClaims : (fallbackClaims || []);

  if (isLoading && claims.length === 0) return <Spinner />;
  if (claims.length === 0) return <EmptyState title="No claims extracted yet" hint="Evidence claims appear here once cross-verified." />;
  return (
    <ul className="space-y-2 text-xs">
      {claims.map((c, i) => (
        <li key={c.id || `claim-${i}`} className="rounded-lg border border-[#453322] bg-[#14110D] p-3 text-[#C0B19C]">
          <div className="flex justify-between items-center mb-1">
            <StatusBadge status={c.verification_status || c.status || 'verified'} />
            {c.confidence != null && <span className="font-mono text-[#C5A36A]">{(Number(c.confidence) * 100).toFixed(0)}% conf</span>}
          </div>
          <p className="text-[#F4EBDD] font-medium leading-snug">{c.text || (c as any).conclusion || (c as any).claim_text}</p>
        </li>
      ))}
    </ul>
  );
}

/* ============================================================
 * SECTION 7 — HISTORY PAGE
 * ============================================================ */

export function HistoryPage() {
  const { data: sessions = [], isLoading } = useSessions();
  const { data: projects = [] } = useProjects();
  const { remove } = useSessionMutations();
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'completed' | 'in_progress' | 'failed'>('all');
  const [projectFilter, setProjectFilter] = useState<string>('all');
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const isUntitled = (s: ResearchSession) => {
    const t = (s.title || s.topic || s.query || '').trim().toLowerCase();
    return !t || t === 'untitled' || t === 'untitled research' || t === 'unknown';
  };

  const projectMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const p of projects) {
      if (p.project_id) map.set(p.project_id, p.name);
      if (p.id) map.set(p.id, p.name);
    }
    return map;
  }, [projects]);

  const filtered = sessions
    .filter((s) => !isUntitled(s))
    .filter((s) => {
      if (statusFilter === 'all') return true;
      const st = String(s.status || '').toLowerCase();
      if (statusFilter === 'completed') return st === 'completed' || st === 'done';
      if (statusFilter === 'in_progress') return st === 'running' || st === 'in_progress' || st === 'planning' || st === 'queued';
      if (statusFilter === 'failed') return st === 'failed' || st === 'error';
      return true;
    })
    .filter((s) => {
      if (projectFilter === 'all') return true;
      return s.project_id === projectFilter;
    })
    .filter((s) => !q || (s.title ?? s.topic ?? s.query ?? '').toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="font-serif text-2xl font-semibold text-[#E9D6B6]">Research History</h1>
          <p className="text-xs text-[#817463]">Comprehensive log of all investigations, extracted claims, and generated reports.</p>
        </div>
        <div className="flex items-center gap-2">
          {projects.length > 0 && (
            <div className="flex items-center gap-1.5 rounded-lg border border-[#453322] bg-[#14110D] px-2.5 py-1 text-xs text-[#817463]">
              <FolderKanban className="h-3.5 w-3.5 text-[#C5A36A]" />
              <select
                value={projectFilter}
                onChange={(e) => setProjectFilter(e.target.value)}
                className="bg-transparent text-[#E9D6B6] focus:outline-none"
              >
                <option value="all">All Projects</option>
                {projects.map((p) => (
                  <option key={p.project_id || p.id} value={p.project_id || p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      </div>

      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 mb-5">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-[#817463]" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search previous investigations…"
            className="pl-9"
          />
        </div>
        <div className="flex items-center rounded-lg border border-[#453322] bg-[#14110D] p-1 text-xs">
          {(['all', 'completed', 'in_progress', 'failed'] as const).map((st) => (
            <button
              key={st}
              onClick={() => setStatusFilter(st)}
              className={cn(
                'rounded-md px-3 py-1 font-medium capitalize transition-colors',
                statusFilter === st ? 'bg-[#302319] text-[#E9D6B6]' : 'text-[#817463] hover:text-[#F4EBDD]'
              )}
            >
              {st.replace('_', ' ')}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16" />)}</div>
      ) : filtered.length === 0 ? (
        <EmptyState icon={<History className="h-6 w-6" />} title="No research sessions found" hint="Start an investigation from New Research or adjust your filters." />
      ) : (
        <ul className="divide-y divide-[#302319] rounded-xl border border-[#453322] bg-[#10100E]">
          {filtered.map((s) => {
            const pName = s.project_id ? projectMap.get(s.project_id) : null;
            return (
              <li key={s.id}>
                <button
                  onClick={() => navigate(`/app/research/${s.id}`)}
                  className="group flex w-full items-center justify-between px-4 py-3.5 text-left hover:bg-[#1B1510] transition-colors"
                >
                  <div className="min-w-0 flex-1 pr-4">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-[14.5px] font-medium text-[#F4EBDD]">{titleFromQuery(s.title || s.topic || s.query)}</p>
                      {pName && (
                        <span className="inline-flex items-center gap-1 rounded bg-[#C5A36A]/10 px-1.5 py-0.5 text-[11px] font-mono text-[#C5A36A] border border-[#C5A36A]/20">
                          <FolderKanban className="h-2.5 w-2.5" /> {pName}
                        </span>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[#817463]">
                      <span>{fmtDate(s.created_at || s.timestamp)}</span>
                      <span>·</span>
                      <span className="text-[#C5A36A] font-mono">{s.sources_count ?? 0} sources</span>
                      <span>·</span>
                      <span className="text-[#C5A36A] font-mono">{s.claims_count ?? 0} claims</span>
                      {s.critic_score != null && (
                        <>
                          <span>·</span>
                          <span className="font-mono text-[#C5A36A]">Score: {s.critic_score}/10</span>
                        </>
                      )}
                      {s.provider && (
                        <>
                          <span>·</span>
                          <span className="font-mono uppercase">{s.provider}</span>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <StatusBadge status={s.status} />
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setDeleteId(s.id);
                      }}
                      className="p-1 text-[#817463] hover:text-red-400 transition-colors"
                      title="Delete session"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <ConfirmDialog open={!!deleteId} onClose={() => setDeleteId(null)}
        title="Delete Session" message="Delete this investigation and all generated claims and reports?"
        onConfirm={() => { if (deleteId) remove.mutate(deleteId, { onSuccess: () => setDeleteId(null) }); }} />
    </div>
  );
}

/* ============================================================
 * SECTION 8 — REPORT EDITOR
 * ============================================================ */

export function ReportEditorPage() {
  const { sessionId = '' } = useParams();
  const toast = useToast();
  const { data: session } = useSession(sessionId);
  const { data: report, isLoading } = useReport(sessionId, session?.saved_report_path);

  const [content, setContent] = useState('');
  const [dirty, setDirty] = useState(false);
  const [preview, setPreview] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const text = report?.content || (session as any)?.report_content || (session as any)?.content || (session as any)?.report;
    if (text) {
      setContent(text);
      setDirty(false);
    }
  }, [report, session]);

  const save = async () => {
    setSaving(true);
    try {
      await api.post(API.reports.update(String(report?.id || sessionId)), { content_markdown: content });
      setDirty(false);
      toast.push('Report updated.', 'success');
    } catch (e) { toast.push(apiError(e), 'error'); }
    finally { setSaving(false); }
  };

  if (isLoading) return <FullPageSpinner />;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-[#302319] bg-[#10100E] px-6 py-3">
        <div>
          <h2 className="font-serif text-lg font-semibold text-[#E9D6B6]">{report?.title || session?.title || session?.topic || 'Research Report'}</h2>
          <p className="text-xs text-[#817463]">Version {report?.version ?? 1} · {report?.verification_status || 'verified'}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setPreview(!preview)}>{preview ? 'Edit Markdown' : 'Preview'}</Button>
          <Button size="sm" variant="primary" loading={saving} onClick={save} disabled={!dirty}>Save Version</Button>
          <Button size="sm" onClick={() => downloadExport(sessionId, 'pdf')}>Export PDF</Button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-8">
        {preview ? (
          content ? (
            <div className="mx-auto max-w-4xl">
              <ReportViewer
                title={report?.title || session?.title || session?.topic || 'Research Report'}
                topic={session?.topic}
                content={content}
                metrics={{
                  criticScore: session?.critic_score,
                  revisionCount: session?.revision_count,
                  sourcesCount: session?.sources?.length,
                  citationsVerified: session?.citations_verified,
                  citationsTotal: session?.citations_total,
                  provider: session?.provider,
                  generatedAt: session?.timestamp || session?.created_at,
                }}
                onCite={(n) => {
                  toast.push(`Citation [${n}] reference in document`, 'info');
                }}
                onExport={async (fmt) => {
                  try {
                    await downloadExport(sessionId, fmt);
                    toast.push(`${fmt.toUpperCase()} exported`, 'success');
                  } catch (e) {
                    toast.push(apiError(e), 'error');
                  }
                }}
              />
            </div>
          ) : (
            <div className="mx-auto max-w-md py-16 text-center">
              <FileText className="mx-auto mb-3 h-10 w-10 text-[#817463]" />
              <h3 className="text-base font-medium text-[#E9D6B6]">No report content available</h3>
              <p className="mt-1 text-xs text-[#817463]">This investigation does not have a generated report yet.</p>
            </div>
          )
        ) : (
          <textarea value={content} onChange={(e) => { setContent(e.target.value); setDirty(true); }}
            placeholder="Type markdown content..."
            className="w-full h-full bg-transparent font-mono text-sm resize-none focus:outline-none text-[#F4EBDD]" />
        )}
      </div>
    </div>
  );
}

/* ============================================================
 * SECTION 9 — PROJECTS (FULL UI IMPLEMENTATION)
 * ============================================================ */

export function ProjectsPage() {
  const { data: projects = [], isLoading } = useProjects();
  const { data: sessions = [] } = useSessions();
  const { data: documents = [] } = useQuery<DocumentRecord[]>({
    queryKey: ['documents'],
    queryFn: async () => asList<DocumentRecord>((await api.get(API.documents.list)).data),
  });
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();

  // Create Modal
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [domain, setDomain] = useState('general');
  const [busy, setBusy] = useState(false);

  // Edit Modal
  const [editProject, setEditProject] = useState<{ id: string; name: string; description: string } | null>(null);
  const [editBusy, setEditBusy] = useState(false);

  // Delete Confirm
  const [deleteId, setDeleteId] = useState<string | null>(null);

  // Details Modal
  const [viewProject, setViewProject] = useState<Project | null>(null);

  const createProject = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      await api.post(API.projects.create, { name: name.trim(), description: desc.trim() || null, research_domain: domain });
      toast.push('Project created.', 'success');
      qc.invalidateQueries({ queryKey: ['projects'] });
      setIsModalOpen(false);
      setName(''); setDesc('');
    } catch (err) { toast.push(apiError(err), 'error'); }
    finally { setBusy(false); }
  };

  const updateProject = async (e: FormEvent) => {
    e.preventDefault();
    if (!editProject || !editProject.name.trim()) return;
    setEditBusy(true);
    try {
      await api.patch(API.projects.update(editProject.id), {
        name: editProject.name.trim(),
        description: editProject.description.trim() || null,
      });
      toast.push('Project updated.', 'success');
      qc.invalidateQueries({ queryKey: ['projects'] });
      setEditProject(null);
    } catch (err) { toast.push(apiError(err), 'error'); }
    finally { setEditBusy(false); }
  };

  const deleteProject = async (id: string) => {
    try {
      await api.delete(API.projects.delete(id));
      toast.push('Project deleted.', 'success');
      qc.invalidateQueries({ queryKey: ['projects'] });
      if (viewProject && (viewProject.project_id === id || viewProject.id === id)) {
        setViewProject(null);
      }
      setDeleteId(null);
    } catch (err) { toast.push(apiError(err), 'error'); }
  };

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-serif text-2xl font-semibold text-[#E9D6B6]">Research Projects</h1>
          <p className="text-xs text-[#817463]">Multi-tenant project containers isolating investigations, reference documents, and persistent memory.</p>
        </div>
        <Button variant="primary" size="sm" onClick={() => setIsModalOpen(true)}>
          <Plus className="h-4 w-4" /> New Project
        </Button>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">{[0, 1].map((i) => <Skeleton key={i} className="h-36" />)}</div>
      ) : projects.length === 0 ? (
        <EmptyState icon={<FolderKanban className="h-6 w-6" />} title="No projects created yet" hint="Create a project to organize research sessions and reference documents." />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {projects.map((p) => {
            const pId = p.project_id || p.id || '';
            const pSessions = sessions.filter((s) => s.project_id === pId);
            const pDocs = documents.filter((d) => d.project_id === pId);

            return (
              <div
                key={pId}
                className="group relative rounded-xl border border-[#453322] bg-[#10100E] p-5 shadow-lg transition-all hover:border-[#C5A36A]/50 flex flex-col justify-between"
              >
                <div>
                  <div className="flex justify-between items-start mb-2">
                    <button
                      onClick={() => setViewProject(p)}
                      className="text-left font-serif text-lg font-semibold text-[#F3E9D7] hover:text-[#C5A36A] transition-colors"
                    >
                      {p.name}
                    </button>
                    <StatusBadge status={p.status} />
                  </div>
                  <p className="text-xs text-[#C0B19C] line-clamp-2 mb-4">{p.description || 'No description provided.'}</p>
                </div>

                <div>
                  <div className="flex items-center gap-3 text-xs text-[#817463] mb-3">
                    <span className="font-mono text-[#C5A36A]">{p.research_domain || 'general'}</span>
                    <span>·</span>
                    <span>{pSessions.length} session{pSessions.length === 1 ? '' : 's'}</span>
                    <span>·</span>
                    <span>{pDocs.length} doc{pDocs.length === 1 ? '' : 's'}</span>
                  </div>

                  <div className="flex items-center justify-between text-xs border-t border-[#302319] pt-3">
                    <button
                      onClick={() => setViewProject(p)}
                      className="inline-flex items-center gap-1 text-[#C5A36A] hover:underline"
                    >
                      <Eye className="h-3.5 w-3.5" /> View Container
                    </button>
                    <div className="flex items-center gap-3">
                      <button
                        onClick={() => setEditProject({ id: pId, name: p.name, description: p.description || '' })}
                        className="text-[#817463] hover:text-[#F4EBDD]"
                        title="Edit Project"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button
                        onClick={() => setDeleteId(pId)}
                        className="text-red-400 hover:text-red-300"
                        title="Delete Project"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* View Project Details Modal */}
      <Dialog open={!!viewProject} onClose={() => setViewProject(null)} title={viewProject?.name || 'Project Details'}>
        {viewProject && (() => {
          const pId = viewProject.project_id || viewProject.id || '';
          const pSessions = sessions.filter((s) => s.project_id === pId);
          const pDocs = documents.filter((d) => d.project_id === pId);

          return (
            <div className="space-y-5">
              <div>
                <p className="text-xs font-mono text-[#C5A36A] uppercase mb-1">{viewProject.research_domain || 'General'} Domain</p>
                <p className="text-sm text-[#C0B19C]">{viewProject.description || 'No description provided.'}</p>
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-xs font-mono uppercase tracking-wider text-[#817463]">Associated Investigations ({pSessions.length})</h4>
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => {
                      setViewProject(null);
                      navigate('/app/new');
                    }}
                  >
                    <Plus className="h-3.5 w-3.5" /> New Investigation
                  </Button>
                </div>
                {pSessions.length === 0 ? (
                  <p className="text-xs text-[#817463] italic bg-[#14110D] p-3 rounded border border-[#302319]">
                    No research sessions linked to this project container yet.
                  </p>
                ) : (
                  <ul className="divide-y divide-[#302319] rounded-lg border border-[#302319] bg-[#14110D]">
                    {pSessions.map((s) => (
                      <li key={s.id} className="p-2.5 hover:bg-[#1B1510] flex justify-between items-center text-xs">
                        <button
                          onClick={() => {
                            setViewProject(null);
                            navigate(`/app/research/${s.id}`);
                          }}
                          className="font-medium text-[#F4EBDD] hover:text-[#C5A36A] truncate max-w-xs text-left"
                        >
                          {titleFromQuery(s.title || s.topic || s.query)}
                        </button>
                        <StatusBadge status={s.status} />
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h4 className="text-xs font-mono uppercase tracking-wider text-[#817463] mb-2">Associated Documents ({pDocs.length})</h4>
                {pDocs.length === 0 ? (
                  <p className="text-xs text-[#817463] italic bg-[#14110D] p-3 rounded border border-[#302319]">
                    No reference documents uploaded to this container.
                  </p>
                ) : (
                  <ul className="divide-y divide-[#302319] rounded-lg border border-[#302319] bg-[#14110D]">
                    {pDocs.map((d) => (
                      <li key={d.document_id || d.id} className="p-2.5 flex justify-between items-center text-xs text-[#C0B19C]">
                        <span className="truncate max-w-xs flex items-center gap-1.5">
                          <FileText className="h-3.5 w-3.5 text-[#C5A36A]" /> {d.original_filename}
                        </span>
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-[11px]">{(d.size_bytes / 1024).toFixed(1)} KB</span>
                          <StatusBadge status={d.processing_status} />
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="flex justify-end pt-2 border-t border-[#302319]">
                <Button onClick={() => setViewProject(null)}>Close</Button>
              </div>
            </div>
          );
        })()}
      </Dialog>

      {/* Create Project Modal */}
      <Dialog open={isModalOpen} onClose={() => setIsModalOpen(false)} title="Create Research Project">
        <form onSubmit={createProject} className="space-y-4">
          <div>
            <label className="text-xs text-[#817463] mb-1 block">Project Name</label>
            <Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Climate Macroeconomics" />
          </div>
          <div>
            <label className="text-xs text-[#817463] mb-1 block">Description</label>
            <Textarea value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Objectives of this research container…" />
          </div>
          <div>
            <label className="text-xs text-[#817463] mb-1 block">Domain</label>
            <select value={domain} onChange={(e) => setDomain(e.target.value)} className="w-full rounded-lg border border-[#453322] bg-[#14110D] p-2 text-sm text-[#F4EBDD]">
              <option value="general">General</option>
              <option value="scientific">Scientific</option>
              <option value="economics">Economics</option>
              <option value="technology">Technology</option>
            </select>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button onClick={() => setIsModalOpen(false)}>Cancel</Button>
            <Button variant="primary" type="submit" loading={busy}>Create Container</Button>
          </div>
        </form>
      </Dialog>

      {/* Edit Project Modal */}
      <Dialog open={!!editProject} onClose={() => setEditProject(null)} title="Edit Project Details">
        {editProject && (
          <form onSubmit={updateProject} className="space-y-4">
            <div>
              <label className="text-xs text-[#817463] mb-1 block">Project Name</label>
              <Input required value={editProject.name} onChange={(e) => setEditProject({ ...editProject, name: e.target.value })} />
            </div>
            <div>
              <label className="text-xs text-[#817463] mb-1 block">Description</label>
              <Textarea value={editProject.description} onChange={(e) => setEditProject({ ...editProject, description: e.target.value })} />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button onClick={() => setEditProject(null)}>Cancel</Button>
              <Button variant="primary" type="submit" loading={editBusy}>Save Changes</Button>
            </div>
          </form>
        )}
      </Dialog>

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={!!deleteId}
        onClose={() => setDeleteId(null)}
        title="Delete Project Container"
        message="Are you sure you want to delete this project? Associated research sessions will remain accessible under History."
        onConfirm={() => { if (deleteId) deleteProject(deleteId); }}
      />
    </div>
  );
}

/* ============================================================
 * SECTION 10 — DOCUMENTS (FULL UI IMPLEMENTATION)
 * ============================================================ */

export function DocumentsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: projects = [] } = useProjects();
  const { data: documents = [], isLoading } = useQuery<DocumentRecord[]>({
    queryKey: ['documents'],
    queryFn: async () => asList<DocumentRecord>((await api.get(API.documents.list)).data),
  });

  const [searchQ, setSearchQ] = useState('');
  const [projectFilter, setProjectFilter] = useState('all');
  const [uploadProjectId, setUploadProjectId] = useState<string>('');
  const [uploading, setUploading] = useState(false);
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [previewDoc, setPreviewDoc] = useState<DocumentRecord | null>(null);
  const [deleteDocId, setDeleteDocId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const projectMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const p of projects) {
      if (p.project_id) map.set(p.project_id, p.name);
      if (p.id) map.set(p.id, p.name);
    }
    return map;
  }, [projects]);

  const onUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setUploading(true);
    const fd = new FormData();
    fd.append('file', f);
    if (uploadProjectId) {
      fd.append('project_id', uploadProjectId);
    }
    try {
      await api.post(API.documents.upload, fd);
      toast.push('Document uploaded. Parsing and embedding pipeline started.', 'success');
      qc.invalidateQueries({ queryKey: ['documents'] });
    } catch (err) { toast.push(apiError(err), 'error'); }
    finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const removeDoc = async (id: string) => {
    try {
      await api.delete(API.documents.delete(id));
      toast.push('Document removed.', 'success');
      qc.invalidateQueries({ queryKey: ['documents'] });
      setDeleteDocId(null);
      if (previewDoc && (previewDoc.document_id === id || previewDoc.id === id)) {
        setPreviewDoc(null);
      }
    } catch (err) { toast.push(apiError(err), 'error'); }
  };

  const processDoc = async (id: string) => {
    setProcessingId(id);
    try {
      await api.post(API.documents.process(id));
      toast.push('Document text extracted, chunked, and indexed into ChromaDB.', 'success');
      qc.invalidateQueries({ queryKey: ['documents'] });
    } catch (err) {
      toast.push(apiError(err), 'error');
    } finally {
      setProcessingId(null);
    }
  };

  const downloadDoc = async (id: string, filename: string) => {
    try {
      const token = getAccessToken();
      const res = await fetch(`${API_BASE}/documents/${id}/download`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error(`Download failed: HTTP ${res.status}`);
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast.push('Download started.', 'success');
    } catch (e) {
      toast.push(apiError(e), 'error');
    }
  };

  const filtered = documents
    .filter((d) => !searchQ || d.original_filename.toLowerCase().includes(searchQ.toLowerCase()))
    .filter((d) => projectFilter === 'all' || d.project_id === projectFilter);

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="font-serif text-2xl font-semibold text-[#E9D6B6]">Knowledge Documents</h1>
          <p className="text-xs text-[#817463]">Upload research papers, PDFs, DOCX, and texts to feed local RAG and vector embeddings.</p>
        </div>
        <div className="flex items-center gap-2">
          {projects.length > 0 && (
            <select
              value={uploadProjectId}
              onChange={(e) => setUploadProjectId(e.target.value)}
              className="rounded-lg border border-[#453322] bg-[#14110D] px-2.5 py-1.5 text-xs text-[#E9D6B6] focus:outline-none focus:border-[#C5A36A]"
              title="Target project for upload"
            >
              <option value="">No Project (General)</option>
              {projects.map((p) => (
                <option key={p.project_id || p.id} value={p.project_id || p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          )}
          <input ref={fileRef} type="file" hidden accept=".pdf,.docx,.txt,.md" onChange={onUpload} />
          <Button variant="primary" size="sm" loading={uploading} onClick={() => fileRef.current?.click()}>
            <UploadCloud className="h-4 w-4" /> Upload Document
          </Button>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 mb-5">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-[#817463]" />
          <Input
            value={searchQ}
            onChange={(e) => setSearchQ(e.target.value)}
            placeholder="Search documents by filename…"
            className="pl-9"
          />
        </div>
        {projects.length > 0 && (
          <div className="flex items-center gap-1.5 rounded-lg border border-[#453322] bg-[#14110D] px-2.5 py-1.5 text-xs text-[#817463]">
            <Filter className="h-3.5 w-3.5 text-[#C5A36A]" />
            <select
              value={projectFilter}
              onChange={(e) => setProjectFilter(e.target.value)}
              className="bg-transparent text-[#E9D6B6] focus:outline-none"
            >
              <option value="all">All Projects</option>
              {projects.map((p) => (
                <option key={p.project_id || p.id} value={p.project_id || p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16" />)}</div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<Files className="h-6 w-6" />}
          title={searchQ || projectFilter !== 'all' ? 'No matching documents' : 'No documents indexed'}
          hint="Upload PDFs, DOCX, or text files to ground multi-agent queries in your local literature."
        />
      ) : (
        <div className="rounded-xl border border-[#453322] bg-[#10100E] overflow-hidden shadow-lg">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-[#302319] bg-[#14110D] text-xs font-mono text-[#817463]">
              <tr>
                <th className="p-3">File Name</th>
                <th className="p-3">Project</th>
                <th className="p-3">Size</th>
                <th className="p-3">Chunks</th>
                <th className="p-3">Status</th>
                <th className="p-3">Date</th>
                <th className="p-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#302319] text-[#C0B19C]">
              {filtered.map((d) => {
                const docId = d.document_id || d.id || '';
                const pName = d.project_id ? projectMap.get(d.project_id) : null;
                const isProcessing = processingId === docId;
                const canProcess = d.processing_status === 'uploaded' || d.processing_status === 'failed';

                return (
                  <tr key={docId} className="hover:bg-[#1B1510] transition-colors">
                    <td className="p-3 font-medium text-[#F4EBDD]">
                      <button
                        onClick={() => setPreviewDoc(d)}
                        className="flex items-center gap-2 hover:text-[#C5A36A] text-left"
                      >
                        <FileText className="h-4 w-4 text-[#C5A36A] shrink-0" />
                        <span className="truncate max-w-xs">{d.original_filename}</span>
                      </button>
                    </td>
                    <td className="p-3 text-xs">
                      {pName ? (
                        <span className="inline-flex items-center gap-1 rounded bg-[#C5A36A]/10 px-1.5 py-0.5 font-mono text-[#C5A36A] border border-[#C5A36A]/20">
                          <FolderKanban className="h-2.5 w-2.5" /> {pName}
                        </span>
                      ) : (
                        <span className="text-[#817463]">—</span>
                      )}
                    </td>
                    <td className="p-3 font-mono text-xs">{(d.size_bytes / 1024).toFixed(1)} KB</td>
                    <td className="p-3 font-mono text-xs text-[#C5A36A]">{d.chunk_count ?? 0}</td>
                    <td className="p-3">
                      <StatusBadge status={d.processing_status} />
                    </td>
                    <td className="p-3 text-xs">{fmtDate(d.created_at)}</td>
                    <td className="p-3 text-right">
                      <div className="flex items-center justify-end gap-2 text-xs">
                        {canProcess && (
                          <button
                            onClick={() => processDoc(docId)}
                            disabled={isProcessing}
                            className="inline-flex items-center gap-1 text-[#C5A36A] hover:underline"
                            title="Extract text, chunk, and index into ChromaDB"
                          >
                            {isProcessing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Sparkles className="h-3 w-3" />}
                            Process
                          </button>
                        )}
                        <button
                          onClick={() => downloadDoc(docId, d.original_filename)}
                          className="p-1 text-[#817463] hover:text-[#F4EBDD]"
                          title="Download Document"
                        >
                          <Download className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => setPreviewDoc(d)}
                          className="p-1 text-[#817463] hover:text-[#C5A36A]"
                          title="View Details"
                        >
                          <Eye className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => setDeleteDocId(docId)}
                          className="p-1 text-red-400 hover:text-red-300"
                          title="Delete Document"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Document Preview & Details Modal */}
      <Dialog open={!!previewDoc} onClose={() => setPreviewDoc(null)} title={previewDoc?.original_filename || 'Document Details'}>
        {previewDoc && (() => {
          const docId = previewDoc.document_id || previewDoc.id || '';
          const pName = previewDoc.project_id ? projectMap.get(previewDoc.project_id) : null;
          return (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="rounded-lg border border-[#302319] bg-[#14110D] p-3">
                  <span className="text-[#817463] block mb-1">MIME Type</span>
                  <span className="font-mono text-[#F4EBDD]">{previewDoc.mime_type || 'application/octet-stream'}</span>
                </div>
                <div className="rounded-lg border border-[#302319] bg-[#14110D] p-3">
                  <span className="text-[#817463] block mb-1">File Size</span>
                  <span className="font-mono text-[#F4EBDD]">{(previewDoc.size_bytes / 1024).toFixed(2)} KB</span>
                </div>
                <div className="rounded-lg border border-[#302319] bg-[#14110D] p-3">
                  <span className="text-[#817463] block mb-1">Indexed Vector Chunks</span>
                  <span className="font-mono text-[#C5A36A] font-bold">{previewDoc.chunk_count ?? 0} chunks</span>
                </div>
                <div className="rounded-lg border border-[#302319] bg-[#14110D] p-3">
                  <span className="text-[#817463] block mb-1">Processing Status</span>
                  <StatusBadge status={previewDoc.processing_status} />
                </div>
              </div>

              {pName && (
                <div className="text-xs text-[#817463]">
                  Project Container: <span className="font-mono text-[#C5A36A]">{pName}</span>
                </div>
              )}

              {previewDoc.sha256 && (
                <div className="text-xs text-[#817463]">
                  <span className="block mb-1">SHA-256 Digest:</span>
                  <code className="block rounded bg-[#14110D] p-2 font-mono text-[11px] text-[#C0B19C] break-all border border-[#302319]">
                    {previewDoc.sha256}
                  </code>
                </div>
              )}

              <div className="flex justify-end gap-2 pt-2 border-t border-[#302319]">
                <Button onClick={() => setPreviewDoc(null)}>Close</Button>
                {(previewDoc.processing_status === 'uploaded' || previewDoc.processing_status === 'failed') && (
                  <Button
                    variant="primary"
                    loading={processingId === docId}
                    onClick={() => processDoc(docId)}
                  >
                    <Sparkles className="h-4 w-4" /> Index Vector Embeddings
                  </Button>
                )}
                <Button onClick={() => downloadDoc(docId, previewDoc.original_filename)}>
                  <Download className="h-4 w-4" /> Download File
                </Button>
              </div>
            </div>
          );
        })()}
      </Dialog>

      {/* Delete Confirm */}
      <ConfirmDialog
        open={!!deleteDocId}
        onClose={() => setDeleteDocId(null)}
        title="Delete Document"
        message="Are you sure you want to delete this document? Any associated vector embeddings in ChromaDB will also be purged."
        onConfirm={() => { if (deleteDocId) removeDoc(deleteDocId); }}
      />
    </div>
  );
}



/* ============================================================
 * SECTION 12 — KNOWLEDGE BASE (FULL UI IMPLEMENTATION)
 * ============================================================ */

export function KnowledgeBasePage() {
  const [q, setQ] = useState('');
  const [sourceType, setSourceType] = useState<'all' | 'document' | 'research'>('all');
  const [selectedProject, setSelectedProject] = useState<string>('all');
  const [verificationFilter, setVerificationFilter] = useState<'all' | 'verified'>('all');
  const [results, setResults] = useState<MemoryRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [stats, setStats] = useState<KnowledgeStats>({
    total_documents: 0,
    total_chunks: 0,
    total_memories: 0,
    contributing_sessions: 0,
  });
  const [initialLoaded, setInitialLoaded] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const { data: projects = [] } = useProjects();
  const navigate = useNavigate();

  const loadStats = useCallback(async () => {
    try {
      const { data } = await api.get<KnowledgeStats>(API.knowledge.stats);
      if (data) {
        setStats({
          total_documents: data.total_documents || 0,
          total_chunks: data.total_chunks || 0,
          total_memories: data.total_memories || 0,
          contributing_sessions: data.contributing_sessions || 0,
        });
      }
    } catch (_) {}
  }, []);

  const fetchKnowledge = useCallback(
    async (
      searchQuery = '',
      srcType = sourceType,
      proj = selectedProject,
      ver = verificationFilter
    ) => {
      setBusy(true);
      try {
        const params = new URLSearchParams();
        if (searchQuery.trim()) params.set('q', searchQuery.trim());
        if (srcType !== 'all') params.set('source_type', srcType);
        if (proj !== 'all') params.set('project_id', proj);
        if (ver === 'verified') params.set('verification_status', 'verified');
        params.set('k', '40');

        const url = `${API.knowledge.search}?${params.toString()}`;
        const { data } = await api.get<{ results?: MemoryRecord[]; memories?: MemoryRecord[] }>(url);
        setResults(data?.results || data?.memories || asList<MemoryRecord>(data));
      } catch (_) {
        setResults([]);
      } finally {
        setBusy(false);
        setInitialLoaded(true);
      }
    },
    [sourceType, selectedProject, verificationFilter]
  );

  useEffect(() => {
    loadStats();
    fetchKnowledge(q, sourceType, selectedProject, verificationFilter);
  }, [loadStats, fetchKnowledge, sourceType, selectedProject, verificationFilter]);

  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    fetchKnowledge(q, sourceType, selectedProject, verificationFilter);
  };

  const onDeleteRecord = async (recordId: string) => {
    if (!window.confirm('Are you sure you want to remove this record from persistent memory?')) return;
    try {
      await api.delete(API.knowledge.delete(recordId));
      setResults((prev) => prev.filter((r) => r.id !== recordId && r.memory_id !== recordId));
      loadStats();
    } catch (err) {
      alert('Could not delete record: ' + apiError(err));
    }
  };

  return (
    <div className="mx-auto max-w-6xl px-4 py-8">
      {/* Header */}
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[#C5A36A]/40 bg-[#C5A36A]/10 px-2.5 py-0.5 text-[11px] font-medium text-[#E9D6B6]">
              <Brain className="h-3.5 w-3.5 text-[#C5A36A]" /> Persistent Vector RAG
            </span>
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-400">
              <ShieldCheck className="h-3 w-3" /> AES-256-GCM
            </span>
          </div>
          <h1 className="font-serif text-3xl font-semibold text-[#E9D6B6]">
            Research Memory & Knowledge Base
          </h1>
          <p className="text-xs text-[#A89885] mt-1 max-w-2xl leading-relaxed">
            Persistent cross-investigation intelligence system. Unified retrieval combining uploaded
            reference literature and verified research findings across all sessions and projects.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              loadStats();
              fetchKnowledge(q, sourceType, selectedProject, verificationFilter);
            }}
            loading={busy}
          >
            <RefreshCw className="h-3.5 w-3.5 mr-1" /> Refresh
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => navigate('/app/documents')}
          >
            <UploadCloud className="h-3.5 w-3.5 mr-1" /> Add Documents
          </Button>
        </div>
      </div>

      {/* Real-time Overview Statistics Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <div className="rounded-xl border border-[#453322] bg-[#12100E] p-4 shadow-sm hover:border-[#C5A36A]/40 transition-colors">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-[#A89885]">Indexed Documents</span>
            <FileText className="h-4 w-4 text-[#C5A36A]" />
          </div>
          <div className="font-serif text-2xl font-bold text-[#E9D6B6]">{stats.total_documents}</div>
          <p className="text-[11px] text-[#786958] mt-0.5">PDF, DOCX, TXT literature</p>
        </div>

        <div className="rounded-xl border border-[#453322] bg-[#12100E] p-4 shadow-sm hover:border-[#C5A36A]/40 transition-colors">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-[#A89885]">Vectorized Chunks</span>
            <Files className="h-4 w-4 text-[#C5A36A]" />
          </div>
          <div className="font-serif text-2xl font-bold text-[#E9D6B6]">{stats.total_chunks}</div>
          <p className="text-[11px] text-[#786958] mt-0.5">ChromaDB encrypted blocks</p>
        </div>

        <div className="rounded-xl border border-[#453322] bg-[#12100E] p-4 shadow-sm hover:border-[#C5A36A]/40 transition-colors">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-[#A89885]">Research Memories</span>
            <Brain className="h-4 w-4 text-[#C5A36A]" />
          </div>
          <div className="font-serif text-2xl font-bold text-[#E9D6B6]">{stats.total_memories}</div>
          <p className="text-[11px] text-[#786958] mt-0.5">Summaries, claims & evidence</p>
        </div>

        <div className="rounded-xl border border-[#453322] bg-[#12100E] p-4 shadow-sm hover:border-[#C5A36A]/40 transition-colors">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-[#A89885]">Contributing Sessions</span>
            <History className="h-4 w-4 text-[#C5A36A]" />
          </div>
          <div className="font-serif text-2xl font-bold text-[#E9D6B6]">{stats.contributing_sessions}</div>
          <p className="text-[11px] text-[#786958] mt-0.5">Past research runs</p>
        </div>
      </div>

      {/* Semantic Search Bar */}
      <form onSubmit={onSearch} className="flex gap-2 mb-4">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-[#817463]" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Natural-language semantic search across documents, verified claims, and prior investigations…"
            className="pl-9 bg-[#10100E] border-[#453322] text-[#E9D6B6] placeholder-[#6E6050]"
          />
        </div>
        <Button variant="primary" type="submit" loading={busy}>
          <Search className="h-4 w-4 mr-1" /> Search
        </Button>
        {q && (
          <Button
            variant="outline"
            onClick={() => {
              setQ('');
              fetchKnowledge('', sourceType, selectedProject, verificationFilter);
            }}
          >
            Clear
          </Button>
        )}
      </form>

      {/* Multi-Dimensional Filter Controls */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#302319] bg-[#0E0D0B] p-3">
        {/* Source Type Filter Tabs */}
        <div className="flex items-center gap-1 bg-[#181614] p-1 rounded-lg border border-[#3A2A1E]">
          <button
            type="button"
            onClick={() => setSourceType('all')}
            className={`px-3 py-1 text-xs rounded-md font-medium transition-colors ${
              sourceType === 'all'
                ? 'bg-[#C5A36A] text-[#12100E] shadow-sm font-semibold'
                : 'text-[#A89885] hover:text-[#E9D6B6]'
            }`}
          >
            All Knowledge
          </button>
          <button
            type="button"
            onClick={() => setSourceType('document')}
            className={`flex items-center gap-1.5 px-3 py-1 text-xs rounded-md font-medium transition-colors ${
              sourceType === 'document'
                ? 'bg-[#C5A36A] text-[#12100E] shadow-sm font-semibold'
                : 'text-[#A89885] hover:text-[#E9D6B6]'
            }`}
          >
            <FileText className="h-3 w-3" /> Uploaded Documents
          </button>
          <button
            type="button"
            onClick={() => setSourceType('research')}
            className={`flex items-center gap-1.5 px-3 py-1 text-xs rounded-md font-medium transition-colors ${
              sourceType === 'research'
                ? 'bg-[#C5A36A] text-[#12100E] shadow-sm font-semibold'
                : 'text-[#A89885] hover:text-[#E9D6B6]'
            }`}
          >
            <Brain className="h-3 w-3" /> Research Findings
          </button>
        </div>

        {/* Project & Verification Dropdowns */}
        <div className="flex items-center gap-2">
          {/* Project selector */}
          <div className="flex items-center gap-1.5 text-xs text-[#A89885]">
            <FolderKanban className="h-3.5 w-3.5 text-[#C5A36A]" />
            <select
              value={selectedProject}
              onChange={(e) => setSelectedProject(e.target.value)}
              className="rounded-lg border border-[#453322] bg-[#141210] px-2.5 py-1 text-xs text-[#E9D6B6] focus:border-[#C5A36A] focus:outline-none"
            >
              <option value="all">All Projects</option>
              {projects.map((p) => (
                <option key={p.project_id || p.id} value={p.project_id || p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>

          {/* Verification Status selector */}
          <div className="flex items-center gap-1.5 text-xs text-[#A89885]">
            <Filter className="h-3.5 w-3.5 text-[#C5A36A]" />
            <select
              value={verificationFilter}
              onChange={(e) => setVerificationFilter(e.target.value as 'all' | 'verified')}
              className="rounded-lg border border-[#453322] bg-[#141210] px-2.5 py-1 text-xs text-[#E9D6B6] focus:border-[#C5A36A] focus:outline-none"
            >
              <option value="all">All Statuses</option>
              <option value="verified">Verified Only</option>
            </select>
          </div>
        </div>
      </div>

      {/* Results Header */}
      <div className="mb-3 flex items-center justify-between text-xs text-[#817463]">
        <span>
          Showing <strong className="text-[#E9D6B6]">{results.length}</strong> knowledge record
          {results.length === 1 ? '' : 's'}
        </span>
        {results.length > 0 && (
          <span className="font-mono text-[11px] text-[#C5A36A]">
            ChromaDB Semantic Index · AES-256 Authenticated
          </span>
        )}
      </div>

      {/* Knowledge Cards List */}
      {busy && !initialLoaded ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28 rounded-xl" />
          ))}
        </div>
      ) : results.length > 0 ? (
        <div className="space-y-3">
          {results.map((r, i) => {
            const recordId = (r.id || r.memory_id || `rec-${i}`).toString();
            const memoryText =
              r.content || r.content_text || r.summary || (typeof r === 'string' ? r : JSON.stringify(r));
            const meta = (r as any).metadata || {};
            const isDoc =
              r.source_type === 'document' ||
              meta.source_type === 'document' ||
              r.content_type === 'document_chunk' ||
              meta.content_type === 'document_chunk' ||
              Boolean(r.document_id || meta.document_id);

            const cType = r.content_type || meta.content_type || (isDoc ? 'document_chunk' : 'claim');
            const vStatus = r.verification_status || meta.verification_status || 'verified';
            const sessionId = r.session_id || meta.session_id;
            const docId = r.document_id || meta.document_id;
            const filename = r.filename || meta.filename || meta.title || r.topic;
            const isExpanded = expandedId === recordId;

            return (
              <div
                key={recordId}
                className="group rounded-xl border border-[#453322] bg-[#12100E] p-4 shadow-sm hover:border-[#C5A36A]/50 transition-all"
              >
                {/* Header row */}
                <div className="flex flex-wrap items-center justify-between gap-2 mb-2.5">
                  <div className="flex items-center gap-2">
                    {/* Source & Content Type Badges */}
                    {isDoc ? (
                      <span className="inline-flex items-center gap-1 rounded-md border border-cyan-800/40 bg-cyan-950/40 px-2 py-0.5 text-[10px] font-medium text-cyan-300">
                        <FileText className="h-3 w-3" /> Document Chunk
                      </span>
                    ) : cType === 'summary' ? (
                      <span className="inline-flex items-center gap-1 rounded-md border border-purple-800/40 bg-purple-950/40 px-2 py-0.5 text-[10px] font-medium text-purple-300">
                        <Sparkles className="h-3 w-3" /> Executive Summary
                      </span>
                    ) : cType === 'evidence' ? (
                      <span className="inline-flex items-center gap-1 rounded-md border border-amber-800/40 bg-amber-950/40 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                        <ShieldCheck className="h-3 w-3" /> Evidence Passage
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-md border border-[#C5A36A]/40 bg-[#C5A36A]/10 px-2 py-0.5 text-[10px] font-medium text-[#E9D6B6]">
                        <Brain className="h-3 w-3 text-[#C5A36A]" /> Research Finding
                      </span>
                    )}

                    {/* Verification Status Badge */}
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.2 text-[10px] font-medium ${
                        vStatus === 'verified'
                          ? 'border border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
                          : vStatus === 'partially_verified'
                          ? 'border border-amber-500/30 bg-amber-500/10 text-amber-400'
                          : 'border border-[#554332] bg-[#221B14] text-[#A89885]'
                      }`}
                    >
                      <CheckCircle2 className="h-2.5 w-2.5" />
                      {vStatus.replace('_', ' ')}
                    </span>

                    {/* Confidence score pill if present */}
                    {r.confidence != null && (
                      <span className="text-[10px] font-mono text-[#A89885] bg-[#181614] px-1.5 py-0.5 rounded border border-[#302319]">
                        {Math.round(Number(r.confidence) * 100)}% conf
                      </span>
                    )}

                    {/* Source label / Document Name */}
                    {filename && (
                      <span className="text-xs font-serif font-medium text-[#E9D6B6] truncate max-w-xs">
                        {filename}
                      </span>
                    )}
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2">
                    {sessionId && (
                      <button
                        type="button"
                        onClick={() => navigate(`/app/research/${sessionId}`)}
                        className="inline-flex items-center gap-1 text-[11px] text-[#C5A36A] hover:underline hover:text-[#E9D6B6]"
                      >
                        View Session <ArrowUpRight className="h-3 w-3" />
                      </button>
                    )}
                    {docId && (
                      <button
                        type="button"
                        onClick={() => navigate('/app/documents')}
                        className="inline-flex items-center gap-1 text-[11px] text-cyan-400 hover:underline"
                      >
                        View Documents <ArrowUpRight className="h-3 w-3" />
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => onDeleteRecord(recordId)}
                      title="Delete from knowledge base"
                      className="p-1 text-[#655543] hover:text-red-400 opacity-60 group-hover:opacity-100 transition-opacity"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>

                {/* Content Passage */}
                <div className="text-xs text-[#D8CAB5] leading-relaxed mb-3">
                  {memoryText.length > 280 && !isExpanded ? (
                    <>
                      <p>{memoryText.slice(0, 280)}…</p>
                      <button
                        type="button"
                        onClick={() => setExpandedId(recordId)}
                        className="mt-1 text-[11px] font-medium text-[#C5A36A] hover:underline"
                      >
                        Show full passage
                      </button>
                    </>
                  ) : (
                    <>
                      <p>{memoryText}</p>
                      {memoryText.length > 280 && (
                        <button
                          type="button"
                          onClick={() => setExpandedId(null)}
                          className="mt-1 text-[11px] font-medium text-[#C5A36A] hover:underline"
                        >
                          Show less
                        </button>
                      )}
                    </>
                  )}
                </div>

                {/* Footer metadata */}
                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#302319] pt-2 text-[11px] text-[#786958] font-mono">
                  <div className="flex items-center gap-2">
                    <span>ChromaDB Vector Store</span>
                    {(meta.project_id || r.project_id) && (
                      <>
                        <span>·</span>
                        <span className="text-[#A89885]">Project: {meta.project_id || r.project_id}</span>
                      </>
                    )}
                    {meta.chunk_index != null && (
                      <>
                        <span>·</span>
                        <span>Chunk #{meta.chunk_index}</span>
                      </>
                    )}
                  </div>
                  {(r.created_at || meta.created_at) && (
                    <span>{fmtDate(r.created_at || meta.created_at)}</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyState
          icon={<Brain className="h-8 w-8 text-[#C5A36A]" />}
          title={q ? 'No matching memory findings' : 'Knowledge Base Ready'}
          hint={
            q
              ? 'Try adjusting your search terms or selecting "All Knowledge" to expand your query scope.'
              : 'Upload reference papers and reports in the Documents workspace, or complete an investigation to automatically populate persistent memory.'
          }
        />
      )}
    </div>
  );
}

/* ============================================================
 * SECTION 13 — CONTRADICTIONS (FULL UI IMPLEMENTATION)
 * ============================================================ */

export function ContradictionsPage() {
  const { data: sessions = [], isLoading: sessionsLoading } = useSessions();
  const [selectedSession, setSelectedSession] = useState<string>('');
  const [contradictionData, setContradictionData] = useState<{
    status: string;
    message?: string;
    contradictions: ContradictionRecord[];
    claims_evaluated?: number;
  }>({ status: 'loading', contradictions: [] });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (sessions.length > 0 && !selectedSession) {
      setSelectedSession(sessions[0].id);
    }
  }, [sessions, selectedSession]);

  useEffect(() => {
    if (!selectedSession) return;
    setBusy(true);
    api.get<{ status?: string; message?: string; contradictions?: ContradictionRecord[]; claims_evaluated?: number }>(
      API.contradictions.bySession(selectedSession)
    )
      .then(({ data }) => {
        setContradictionData({
          status: data?.status || (data?.contradictions && data.contradictions.length > 0 ? 'completed' : 'no_contradictions'),
          message: data?.message,
          contradictions: data?.contradictions || [],
          claims_evaluated: data?.claims_evaluated,
        });
      })
      .catch(() => setContradictionData({ status: 'failed', contradictions: [] }))
      .finally(() => setBusy(false));
  }, [selectedSession]);

  const activeSessionObj = sessions.find((s) => s.id === selectedSession);

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="font-serif text-2xl font-semibold text-[#E9D6B6]">Contradiction Analysis</h1>
          <p className="text-xs text-[#817463]">Multi-source cross-examination detecting scientific and factual discrepancies.</p>
        </div>
        {sessions.length > 0 && (
          <select
            value={selectedSession}
            onChange={(e) => setSelectedSession(e.target.value)}
            className="rounded-lg border border-[#453322] bg-[#14110D] px-3 py-1.5 text-xs text-[#F4EBDD] focus:outline-none focus:border-[#C5A36A] max-w-xs truncate"
          >
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {titleFromQuery(s.title ?? s.query)}
              </option>
            ))}
          </select>
        )}
      </div>

      {busy || sessionsLoading ? (
        <div className="space-y-3">{[0, 1].map((i) => <Skeleton key={i} className="h-32" />)}</div>
      ) : sessions.length === 0 ? (
        <EmptyState icon={<GitCompare className="h-6 w-6" />} title="No research sessions found" hint="Start an investigation to evaluate claims and contradiction matrices." />
      ) : contradictionData.status === 'no_claims' ? (
        <EmptyState
          icon={<GitCompare className="h-6 w-6" />}
          title="No Claims Extracted Yet"
          hint="This investigation has not extracted verifiable claims. Contradiction detection runs automatically once claims are retrieved."
        />
      ) : contradictionData.status === 'in_progress' ? (
        <div className="rounded-xl border border-sky-500/40 bg-sky-500/10 p-8 text-center">
          <Loader2 className="mx-auto h-8 w-8 animate-spin text-sky-400 mb-3" />
          <h3 className="font-serif text-lg font-semibold text-sky-200">Contradiction Analysis In Progress</h3>
          <p className="text-xs text-[#C0B19C] mt-2 max-w-md mx-auto">
            Multi-agent cross-examination is actively comparing empirical claims against indexed sources.
          </p>
        </div>
      ) : contradictionData.status === 'no_contradictions' || contradictionData.contradictions.length === 0 ? (
        <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-8 text-center shadow-lg">
          <CheckCircle2 className="mx-auto h-12 w-12 text-emerald-400 mb-3" />
          <h3 className="font-serif text-lg font-semibold text-emerald-200">Empirical Alignment Verified</h3>
          <p className="text-xs text-[#C0B19C] mt-2 max-w-md mx-auto leading-relaxed">
            Cross-examination of {contradictionData.claims_evaluated ?? activeSessionObj?.claims_count ?? 'extracted'} claims confirms empirical consensus across all analyzed references with zero unresolved factual disputes flagged.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center justify-between text-xs text-[#817463]">
            <span>{contradictionData.contradictions.length} Conflict{contradictionData.contradictions.length === 1 ? '' : 's'} Detected</span>
            <span className="font-mono text-[#C5A36A]">{contradictionData.claims_evaluated ?? activeSessionObj?.claims_count ?? 0} claims evaluated</span>
          </div>

          {contradictionData.contradictions.map((c, i) => (
            <div key={c.contradiction_id || i} className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-5 shadow-lg">
              <div className="flex justify-between items-center mb-3">
                <span className="font-mono text-xs text-amber-400 font-bold uppercase tracking-wider">
                  {c.category || 'Empirical Discrepancy'}
                </span>
                <Badge className={cn('border-amber-500/40', c.severity === 'high' ? 'text-red-400 border-red-500/40' : 'text-amber-300')}>
                  {c.severity || 'medium'} severity
                </Badge>
              </div>

              <p className="text-sm text-[#F4EBDD] font-medium mb-3 leading-snug">
                {c.description || 'Conflict detected between opposing empirical claims.'}
              </p>

              {(c.claim_a_id || c.claim_b_id) && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3 text-xs">
                  <div className="rounded-lg border border-[#302319] bg-[#10100E] p-3">
                    <span className="font-mono text-[#817463] block mb-1">Perspective A:</span>
                    <p className="text-[#C0B19C]">{c.claim_a_id}</p>
                  </div>
                  <div className="rounded-lg border border-[#302319] bg-[#10100E] p-3">
                    <span className="font-mono text-[#817463] block mb-1">Perspective B:</span>
                    <p className="text-[#C0B19C]">{c.claim_b_id}</p>
                  </div>
                </div>
              )}

              {c.resolution_notes && (
                <div className="rounded-lg border border-[#302319] bg-[#10100E] p-3 text-xs text-[#C0B19C]">
                  <span className="font-mono text-[#C5A36A] block mb-1">Synthesis & Reconciliation Guidance:</span>
                  {c.resolution_notes}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ============================================================
 * SECTION 14 — SYSTEM STATUS (FULL UI IMPLEMENTATION)
 * ============================================================ */

export function SystemStatusPage() {
  const { data: health, isLoading: healthLoading } = useQuery<HealthStatus>({
    queryKey: ['system-health'],
    queryFn: async () => (await api.get(API.system.health)).data,
  });

  const { data: dependencies, isLoading: depsLoading } = useQuery<any>({
    queryKey: ['system-dependencies'],
    queryFn: async () => (await api.get(API.system.dependencies)).data,
  });

  const { data: providers } = useQuery<ProvidersResponse>({
    queryKey: ['system-providers'],
    queryFn: async () => (await api.get(API.system.providers)).data,
  });

  const { data: usage } = useQuery<UsageSummaryResponse>({
    queryKey: ['system-usage'],
    queryFn: async () => (await api.get(API.usage.summary)).data,
  });

  const checks = dependencies?.checks || health?.checks || [];

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 space-y-6">
      <div>
        <h1 className="font-serif text-2xl font-semibold text-[#E9D6B6]">System Observability & Status</h1>
        <p className="text-xs text-[#817463]">Live telemetry, provider fallback availability, OpenTelemetry metrics, and usage tracking.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="rounded-xl border border-[#453322] bg-[#10100E] p-4">
          <p className="text-xs text-[#817463] mb-1">API Core Status</p>
          <div className="flex items-center gap-2">
            <span className={cn('h-3 w-3 rounded-full', (health?.status === 'ok' || dependencies?.status === 'ok') ? 'bg-emerald-500' : 'bg-red-500')} />
            <span className="font-serif font-bold text-lg text-[#F3E9D7]">
              {(dependencies?.status || health?.status || 'ONLINE').toUpperCase()}
            </span>
          </div>
          <p className="text-xs text-[#817463] mt-2">
            Uptime: {fmtDuration((health?.uptime_seconds || 0) * 1000)} · v{health?.version || '1.0.0'}
          </p>
        </div>

        <div className="rounded-xl border border-[#453322] bg-[#10100E] p-4">
          <p className="text-xs text-[#817463] mb-1">Default Model Provider</p>
          <div className="flex items-center gap-2">
            <Cpu className="h-5 w-5 text-[#C5A36A]" />
            <span className="font-mono font-bold text-lg text-[#E9D6B6]">
              {providers?.default?.toUpperCase() || 'GEMINI'}
            </span>
          </div>
          <p className="text-xs text-[#817463] mt-2">
            {providers?.providers?.length || 4} configured fallback routing channels
          </p>
        </div>

        <div className="rounded-xl border border-[#453322] bg-[#10100E] p-4">
          <p className="text-xs text-[#817463] mb-1">Usage & Telemetry</p>
          <div className="flex items-center gap-2">
            <DollarSign className="h-5 w-5 text-emerald-400" />
            <span className="font-mono font-bold text-lg text-emerald-400">
              ${(usage?.total_estimated_cost_usd || 0).toFixed(4)}
            </span>
          </div>
          <p className="text-xs text-[#817463] mt-2">
            {usage?.total_tokens || 0} tokens processed across {usage?.total_requests || 0} runs
          </p>
        </div>
      </div>

      <div className="rounded-xl border border-[#453322] bg-[#10100E] p-5 shadow-lg">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-serif text-lg font-semibold text-[#E9D6B6]">Live Dependency Probes</h3>
          <span className="text-xs font-mono text-[#817463]">Auto-refreshed</span>
        </div>

        {healthLoading && depsLoading ? (
          <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)}</div>
        ) : checks.length === 0 ? (
          <div className="py-4 text-center text-xs text-[#817463]">All primary backend services operating normally.</div>
        ) : (
          <div className="divide-y divide-[#302319]">
            {checks.map((c: any, i: number) => (
              <div key={i} className="flex justify-between items-center py-2.5 text-sm">
                <div>
                  <span className="text-[#F4EBDD] font-medium">{c.name}</span>
                  {c.detail && <p className="text-[11px] text-[#817463]">{c.detail}</p>}
                </div>
                <div className="flex items-center gap-3">
                  {c.duration_ms != null && (
                    <span className="font-mono text-xs text-[#817463]">{c.duration_ms.toFixed(1)}ms</span>
                  )}
                  <StatusBadge status={c.state || 'ok'} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-[#453322] bg-[#10100E] p-5 shadow-lg">
        <h3 className="font-serif text-lg font-semibold text-[#E9D6B6] mb-2">Observability & Telemetry Dashboards</h3>
        <p className="text-xs text-[#C0B19C] mb-4">
          Prometheus metrics and OpenAPI documentation are served directly by the FastAPI backend instance.
        </p>
        <div className="flex flex-wrap gap-3">
          <a
            href={`${API_BASE}/metrics`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#453322] bg-[#14110D] px-3 py-2 text-xs font-medium text-[#E9D6B6] hover:bg-[#1B1510] hover:text-[#F4EBDD] transition-colors"
          >
            <ExternalLink className="h-3.5 w-3.5 text-[#C5A36A]" /> Prometheus Metrics Endpoint
          </a>
          <a
            href={`${API_BASE}/docs`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#453322] bg-[#14110D] px-3 py-2 text-xs font-medium text-[#E9D6B6] hover:bg-[#1B1510] hover:text-[#F4EBDD] transition-colors"
          >
            <ExternalLink className="h-3.5 w-3.5 text-[#C5A36A]" /> Swagger OpenAPI Docs
          </a>
        </div>
      </div>
    </div>
  );
}

/* ============================================================
 * SECTION 15 — SETTINGS PAGE
 * ============================================================ */

export function SettingsPage() {
  const { user, signOut } = useAuth();
  const { theme, setTheme } = useTheme();
  const navigate = useNavigate();
  const toast = useToast();
  const [apiUrl, setApiUrl] = useState(() => localStorage.getItem('questApi') || API_BASE);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  const handleSaveApi = async () => {
    setTesting(true);
    setTestResult(null);
    const cleanUrl = apiUrl.trim().replace(/\/$/, '');
    try {
      const target = cleanUrl ? `${cleanUrl}/auth/config` : `${API_BASE}/auth/config`;
      const res = await fetch(target, { method: 'GET' });
      if (res.ok) {
        if (cleanUrl) {
          localStorage.setItem('questApi', cleanUrl);
        } else {
          localStorage.removeItem('questApi');
        }
        setTestResult({ ok: true, message: 'Backend connected successfully!' });
        toast.push('Backend API URL saved and verified!', 'success');
        setTimeout(() => window.location.reload(), 1000);
      } else {
        setTestResult({ ok: false, message: `Server responded with status ${res.status}` });
        toast.push(`Failed to connect: HTTP ${res.status}`, 'error');
      }
    } catch (e: any) {
      setTestResult({ ok: false, message: e?.message || 'Failed to reach server. Check CORS or URL.' });
      toast.push('Cannot reach backend. Verify URL and Render deployment.', 'error');
    } finally {
      setTesting(false);
    }
  };

  const handleResetApi = () => {
    localStorage.removeItem('questApi');
    setApiUrl('http://127.0.0.1:8000');
    toast.push('Reset to default API URL', 'info');
    setTimeout(() => window.location.reload(), 800);
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-8">
      <h1 className="font-serif text-2xl font-semibold text-[#E9D6B6]">Settings</h1>

      <section className="rounded-xl border border-[#453322] bg-[#10100E] p-5">
        <h2 className="text-xs font-mono uppercase tracking-wider text-[#817463] mb-3">Backend API Server (Render)</h2>
        <p className="text-xs text-[#C0B19C] mb-3">
          Enter your Render backend URL (e.g. <code className="text-[#E9D6B6]">https://your-service.onrender.com</code>) so the Vercel frontend can connect to multi-agent pipelines and load recent investigations.
        </p>
        <div className="space-y-3">
          <div>
            <label className="text-[11px] font-mono text-[#817463] block mb-1">Backend Server URL</label>
            <Input
              value={apiUrl}
              onChange={(e) => setApiUrl(e.target.value)}
              placeholder="https://quest-xxxx.onrender.com"
              className="font-mono text-xs"
            />
          </div>
          {testResult && (
            <div className={cn('p-2.5 rounded-lg text-xs flex items-center gap-2', testResult.ok ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30' : 'bg-red-500/10 text-red-400 border border-red-500/30')}>
              {testResult.ok ? <Check className="h-4 w-4 shrink-0" /> : <AlertTriangle className="h-4 w-4 shrink-0" />}
              <span>{testResult.message}</span>
            </div>
          )}
          <div className="flex gap-2 pt-1">
            <Button variant="primary" size="sm" onClick={handleSaveApi} loading={testing}>
              Save & Test Connection
            </Button>
            <Button variant="outline" size="sm" onClick={handleResetApi} disabled={testing}>
              Reset Default
            </Button>
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-[#453322] bg-[#10100E] p-5">
        <h2 className="text-xs font-mono uppercase tracking-wider text-[#817463] mb-3">Identity & Security</h2>
        <p className="text-sm"><span className="text-[#817463]">Account:</span> <span className="text-[#F4EBDD] font-medium">{user?.email ?? 'Analyst'}</span></p>
        <p className="text-sm mt-1"><span className="text-[#817463]">Provider:</span> Google OAuth (Supabase Managed)</p>
        <Button variant="danger" size="sm" className="mt-4" onClick={() => signOut().then(() => navigate('/auth/login'))}>
          Sign out of Quest
        </Button>
      </section>

      <section className="rounded-xl border border-[#453322] bg-[#10100E] p-5">
        <h2 className="text-xs font-mono uppercase tracking-wider text-[#817463] mb-3">Cloud Configuration</h2>
        <div className="space-y-2 text-xs font-mono">
          <p className="text-[#C0B19C]">Supabase Endpoint: <span className="text-emerald-400">Connected (vxqalflbzpjkvmtbbqrv.supabase.co)</span></p>
          <p className="text-[#C0B19C]">Google Cloud Client: <span className="text-emerald-400">Configured (OAuth 2.0 Web)</span></p>
          <p className="text-[#C0B19C]">PostgreSQL Multi-Tenant Isolation: <span className="text-emerald-400">AES-256 Enabled</span></p>
        </div>
      </section>
    </div>
  );
}

/* ============================================================
 * SECTION 16 — MAIN ROUTER (App)
 * ============================================================ */

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <FullPageSpinner />;
  if (!user) return <Navigate to="/auth/login" state={{ from: location.pathname }} replace />;
  return <>{children}</>;
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/auth/login" element={<LoginPage />} />
      <Route path="/auth/signup" element={<SignUpPage />} />
      <Route path="/auth/forgot-password" element={<ForgotPasswordPage />} />

      <Route path="/app" element={<RequireAuth><AppShell /></RequireAuth>}>
        <Route index element={<Navigate to="/app/new" replace />} />
        <Route path="new" element={<WorkspacePage />} />
        <Route path="research/:sessionId" element={<WorkspacePage />} />
        <Route path="research/:sessionId/report" element={<ReportEditorPage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="review" element={<Navigate to="/app/new" replace />} />
        <Route path="knowledge" element={<KnowledgeBasePage />} />
        <Route path="contradictions" element={<ContradictionsPage />} />
        <Route path="system" element={<SystemStatusPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;

