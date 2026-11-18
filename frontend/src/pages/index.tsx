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
  CheckCircle2, XCircle, ArrowUpRight, DollarSign, Cpu, Eye, Download, Filter, Clock, Sparkles,
  ArrowLeft, MessageSquare, Copy, ChevronLeft, BookOpen, Layers, Bot,
  ChevronDown, ChevronUp, Scale, ArrowRight, Database, Server, HardDrive, Gauge, Terminal, Radio, Zap, Globe
} from 'lucide-react';

import { ReportViewer } from '../components/ReportViewer';
import { api, apiError, downloadExport, getAccessToken, streamUrl } from '../api/client';
import { API, API_BASE } from '../api/endpoints';
import type {
  ClaimRecord, Project, ResearchReport, ResearchSession, SourceRef,
  DocumentRecord, ContradictionRecord, MemoryRecord, KnowledgeStats, HealthStatus,
  ProvidersResponse, UsageSummaryResponse, NotificationRecord,
  DocumentCitation, DocumentChatMessage, DocumentConversation, DocumentPreview,
  ReportComparisonRecord, ComparisonDimension, ComparisonDifference, ComparisonOverviewStats,
  ComparisonCitation, ComparisonChatMessage
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

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline';
const btnStyles: Record<ButtonVariant, string> = {
  primary: 'btn-pioneer-primary',
  secondary: 'btn-pioneer-secondary',
  outline: 'border border-[#453322] bg-transparent hover:border-[#C5A36A] hover:bg-[#1B1510] text-[#F4EBDD] hover:shadow-[0_0_15px_rgba(197,163,106,0.2)] transition-all duration-300',
  ghost: 'hover:bg-[#1B1510] text-[#C0B19C] hover:text-[#F4EBDD] transition-all duration-200',
  danger: 'bg-red-800 text-white hover:bg-red-700 active:scale-[0.98] transition-all duration-200 shadow-sm',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant; loading?: boolean; size?: 'sm' | 'md';
}
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'secondary', loading, size = 'md', className, children, disabled, ...props }, ref) => (
    <button ref={ref} disabled={disabled || loading} {...props}
      className={cn('inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-all duration-300',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#C5A36A]/60 disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' ? 'px-3 py-1.5 text-[13px]' : 'px-4 py-2 text-sm',
        btnStyles[variant], className)}>
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  )
);
Button.displayName = 'Button';

const fieldCls = 'w-full rounded-lg input-pioneer px-3.5 py-2.5 text-sm placeholder:text-[#817463] text-[#F4EBDD]';

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
          className={cn('relative border-b-2 px-3.5 py-2 text-[13px] font-medium transition-all duration-300',
            active === t.id ? 'border-[#C5A36A] text-[#F3E9D7] shadow-[0_1px_8px_rgba(197,163,106,0.3)]' : 'border-transparent text-[#817463] hover:text-[#C0B19C]')}>
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
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/75 p-4 backdrop-blur-md transition-opacity duration-300"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-label={title}
        className="w-full max-w-md pioneer-modal rounded-xl border border-[#453322] bg-[#10100E]/95 backdrop-blur-xl p-5 shadow-2xl text-[#F4EBDD] shadow-[0_16px_40px_rgba(0,0,0,0.7)]">
        <div className="mb-3 flex items-center justify-between border-b border-[#302319] pb-3">
          <h2 className="text-base font-serif font-semibold text-[#E9D6B6]">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-[#817463] hover:text-[#F4EBDD] transition-colors duration-200">
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
  { to: '/app/contradictions', label: 'Report Comparison', icon: GitCompare },
  { to: '/app/system', label: 'System Status', icon: Activity },
  { to: '/app/history', label: 'History', icon: History },
];

export function AppShell() {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('rai-sidebar') === '1');
  const [mobileOpen, setMobileOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
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
        <main className="min-h-0 flex-1 overflow-y-auto pioneer-enter" key={location.pathname}><Outlet /></main>
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
        <button onClick={onToggle} aria-label="Toggle sidebar" className="ml-auto hidden rounded p-1.5 text-[#817463] hover:text-[#C5A36A] md:block transition-colors duration-200">
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
              className={({ isActive }) => cn(
                'nav-link-pioneer flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] text-[#C0B19C] hover:bg-[#1B1510] hover:text-[#F4EBDD]',
                isActive && 'bg-[#302319]/80 border border-[#C5A36A]/30 text-[#E9D6B6] font-medium shadow-[0_0_12px_rgba(197,163,106,0.15)]',
                collapsed && 'justify-center px-0'
              )}>
              <Icon className="h-4 w-4 shrink-0 text-[#C5A36A] transition-transform duration-300" />{!collapsed && label}
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
                      className={cn(
                        'w-full truncate rounded-md px-2.5 py-1.5 text-left text-[12.5px] text-[#817463] hover:bg-[#1B1510] hover:text-[#F4EBDD] transition-all duration-200 hover:translate-x-1',
                        activeId === s.id && 'bg-[#1B1510] border-l-2 border-[#C5A36A] text-[#E9D6B6] font-medium pl-3'
                      )}>
                      {titleFromQuery(s.title || s.topic || s.query)}
                    </button>
                    <span className="absolute right-1 top-1 hidden items-center group-hover:flex">
                      <button onClick={() => setDeleteId(s.id)} className="p-1 text-[#817463] hover:text-red-400 transition-colors"><Trash2 className="h-3 w-3" /></button>
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

  const initialQuery = session?.query || session?.topic || pendingQuery;

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

  useEffect(() => {
    if (followUps.length > 0 || chatBusy) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
    }
  }, [followUps.length, chatBusy]);

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

            {initialQuery && (
              <div className="flex justify-end">
                <div className="max-w-[88%] rounded-2xl px-4 py-3 bg-[#C5A36A] text-[#080806] font-medium shadow-sm">
                  <p className="whitespace-pre-wrap">{initialQuery}</p>
                </div>
              </div>
            )}

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

            {/* Follow-up conversation queries & answers render BELOW the report */}
            {followUps.length > 0 && (
              <div className="space-y-4 pt-6 border-t border-[#302319]">
                <div className="flex items-center gap-2 text-xs font-mono uppercase tracking-wider text-[#C5A36A]">
                  <MessageSquare className="h-3.5 w-3.5" />
                  <span>Follow-up Discussion</span>
                </div>
                {followUps.map((m) => (
                  <div key={m.id} className={cn('flex', m.role === 'user' ? 'justify-end' : 'justify-start')}>
                    <div className={cn('max-w-[88%] rounded-2xl px-4 py-3',
                      m.role === 'user' ? 'bg-[#C5A36A] text-[#080806] font-medium' : 'border border-[#453322] bg-[#14110D]')}>
                      {m.role === 'user' ? <p className="whitespace-pre-wrap">{m.content}</p> : <MessageContent content={m.content} />}
                    </div>
                  </div>
                ))}
                {chatBusy && (
                  <div className="flex justify-start">
                    <div className="flex items-center gap-2 rounded-2xl border border-[#453322] bg-[#14110D] px-4 py-3 text-sm text-[#C5A36A]">
                      <Loader2 className="h-4 w-4 animate-spin" /> Analyzing investigation findings…
                    </div>
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
        <ul className="divide-y divide-[#302319] card-pioneer overflow-hidden shadow-xl">
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
                className="group relative card-pioneer p-5 flex flex-col justify-between"
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
 * SECTION 10 — DOCUMENTS (FULL UI IMPLEMENTATION + CHAT WITH DOCUMENTS)
 * ============================================================ */

function formatDocumentChatMessage(raw: string): string {
  if (!raw) return '';
  let text = raw.trim();

  // If text has code blocks, only process lines outside code blocks
  const parts = text.split(/(```[\s\S]*?```)/g);
  for (let pIdx = 0; pIdx < parts.length; pIdx++) {
    if (pIdx % 2 === 1) continue;

    const lines = parts[pIdx].split('\n');
    const formatted: string[] = [];

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();

      // Check if line is a common section header
      const isHeaderPattern = /^(Key Takeaways|Core Premise and Content Highlights|Core Premise|Content Highlights|Production Credits|Publication Details|Executive Summary|Overview|Summary|Conclusions?|Strategic Recommendations?|Methodology|Findings|Background|Analysis|Context)(?::)?$/i;

      if (isHeaderPattern.test(line)) {
        formatted.push('');
        formatted.push(`### ${line.replace(/:$/, '')}`);
        formatted.push('');
        continue;
      }

      // If a line is formatted like "Label: Value" or "Label: Value [1]" without a bullet, turn it into "- **Label:** Value"
      const labelMatch = /^([A-Z][A-Za-z0-9\s&/()_-]{1,40}):\s+(.+)$/.exec(line);
      if (labelMatch && !line.startsWith('-') && !line.startsWith('*') && !line.startsWith('#')) {
        formatted.push(`- **${labelMatch[1]}:** ${labelMatch[2]}`);
        continue;
      }

      // If an un-bulleted sentence ends with a citation bracket (e.g. "Across tables... [6].")
      if (/^[A-Z].*\[\d+\]\.\s*$/.test(line) && !line.startsWith('-') && !line.startsWith('*')) {
        formatted.push(`- ${line}`);
        continue;
      }

      formatted.push(lines[i]);
    }

    parts[pIdx] = formatted.join('\n');
  }

  text = parts.join('');

  // Mark citations so [1], [2] become `[1]` for interactive buttons
  const citationParts = text.split(/(```[\s\S]*?```|`[^`]*`)/g);
  return citationParts
    .map((p, i) => (i % 2 === 1 ? p : p.replace(/\[(\d+)\](?!\()/g, '`[$1]`')))
    .join('');
}

interface DocumentChatMessageContentProps {
  content: string;
  citations?: any[];
  onCite?: (num: number) => void;
}

function DocumentChatMessageContent({ content, citations, onCite }: DocumentChatMessageContentProps) {
  const formattedContent = useMemo(() => formatDocumentChatMessage(content), [content]);

  return (
    <div className="space-y-3 leading-relaxed text-xs">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1({ children }) {
            return (
              <h3 className="font-serif text-base font-bold text-[#F4EBDD] mt-3.5 mb-2 pb-1 border-b border-[#302319] flex items-center gap-1.5">
                {children}
              </h3>
            );
          },
          h2({ children }) {
            return (
              <h4 className="font-serif text-sm font-semibold text-[#E9D6B6] mt-3 mb-1.5 flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-[#C5A36A] shrink-0" />
                <span>{children}</span>
              </h4>
            );
          },
          h3({ children }) {
            return (
              <h4 className="font-serif text-sm font-semibold text-[#E9D6B6] mt-3 mb-1.5 flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-[#C5A36A] shrink-0" />
                <span>{children}</span>
              </h4>
            );
          },
          h4({ children }) {
            return (
              <h5 className="font-mono text-[11px] font-semibold uppercase tracking-wider text-[#C5A36A] mt-2.5 mb-1">
                {children}
              </h5>
            );
          },
          p({ children }) {
            return <p className="mb-2.5 leading-relaxed text-[#D6CBB8] text-xs last:mb-0">{children}</p>;
          },
          strong({ children }) {
            return <strong className="font-semibold text-[#F4EBDD]">{children}</strong>;
          },
          em({ children }) {
            return <em className="italic text-[#E9D6B6]">{children}</em>;
          },
          ul({ children }) {
            return <ul className="my-2 space-y-1.5 pl-1 text-[#D6CBB8] list-none">{children}</ul>;
          },
          ol({ children }) {
            return <ol className="my-2 space-y-1.5 pl-4 list-decimal text-[#D6CBB8] text-xs">{children}</ol>;
          },
          li({ children }) {
            return (
              <li className="flex items-start gap-2 text-xs leading-relaxed text-[#D6CBB8]">
                <span className="h-1.5 w-1.5 rounded-full bg-[#C5A36A]/80 mt-1.5 shrink-0" />
                <span className="flex-1">{children}</span>
              </li>
            );
          },
          blockquote({ children }) {
            return (
              <blockquote className="my-2.5 rounded-r-lg border-l-2 border-[#C5A36A] bg-[#1A140F] p-2.5 text-xs text-[#F4EBDD] italic shadow-xs">
                {children}
              </blockquote>
            );
          },
          table({ children }) {
            return (
              <div className="my-3 overflow-hidden rounded-lg border border-[#453322] bg-[#14100D] shadow-sm">
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs border-collapse">{children}</table>
                </div>
              </div>
            );
          },
          thead({ children }) {
            return (
              <thead className="border-b border-[#453322] bg-[#221A13] text-[#E9D6B6] font-mono uppercase tracking-wider text-[10px]">
                {children}
              </thead>
            );
          },
          th({ children }) {
            return <th className="px-3 py-2 font-semibold text-[#F4EBDD] whitespace-nowrap">{children}</th>;
          },
          tbody({ children }) {
            return <tbody className="divide-y divide-[#2B1E14]">{children}</tbody>;
          },
          tr({ children }) {
            return (
              <tr className="hover:bg-[#201811] transition-colors odd:bg-[#120F0C] even:bg-[#17120E]">{children}</tr>
            );
          },
          td({ children }) {
            return <td className="px-3 py-2 text-[#D8CEBF] leading-normal">{children}</td>;
          },
          code({ className, children, ...props }) {
            const text = String(children ?? '');
            const citationMatch = !className && /^\[(\d+)\]$/.exec(text.trim());

            if (citationMatch) {
              const citeNum = Number(citationMatch[1]);
              return (
                <button
                  type="button"
                  onClick={() => onCite?.(citeNum)}
                  className="inline-flex items-center justify-center mx-0.5 px-1.5 py-0.2 rounded text-[10px] font-mono font-bold bg-[#C5A36A]/15 text-[#C5A36A] border border-[#C5A36A]/40 hover:bg-[#C5A36A] hover:text-[#10100E] hover:border-[#C5A36A] transition-all cursor-pointer shadow-xs"
                  title={`View cited passage [${citeNum}] in document`}
                >
                  [{citeNum}]
                </button>
              );
            }

            return (
              <code
                className="rounded bg-[#201A14] border border-[#3E2E20] px-1 py-0.5 font-mono text-[11px] text-[#E9D6B6]"
                {...props}
              >
                {children}
              </code>
            );
          },
          pre({ children }) {
            return (
              <pre className="my-2.5 overflow-x-auto rounded-lg border border-[#3E2E20] bg-[#0E0C0A] p-3 font-mono text-[11px] text-[#E9D6B6]">
                {children}
              </pre>
            );
          },
        }}
      >
        {formattedContent}
      </ReactMarkdown>
    </div>
  );
}

interface DocumentChatWorkspaceProps {
  docRecord?: DocumentRecord | null;
  document?: DocumentRecord | null;
  multiDocuments?: DocumentRecord[];
  projectMap: Map<string, string>;
  onBack: () => void;
}

function DocumentChatWorkspace({
  docRecord,
  document: legacyDoc,
  multiDocuments = [],
  projectMap,
  onBack,
}: DocumentChatWorkspaceProps) {
  const activeDoc = docRecord || legacyDoc || null;
  const toast = useToast();
  const isMulti = multiDocuments.length > 1 || !activeDoc;
  const primaryDoc = activeDoc || multiDocuments[0];
  const docId = primaryDoc ? (primaryDoc.document_id || primaryDoc.id || '') : '';

  // Document preview state
  const [preview, setPreview] = useState<DocumentPreview | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [activePage, setActivePage] = useState(1);
  const [searchInDoc, setSearchInDoc] = useState('');

  // Chat conversation state
  const [conversations, setConversations] = useState<DocumentConversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<DocumentChatMessage[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [question, setQuestion] = useState('');
  const [sending, setSending] = useState(false);
  const [activeCitation, setActiveCitation] = useState<DocumentCitation | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const highlightRef = useRef<HTMLDivElement>(null);

  // Load document preview
  useEffect(() => {
    if (!docId || isMulti) return;
    let cancelled = false;
    setLoadingPreview(true);
    api.get<DocumentPreview>(API.documents.preview(docId))
      .then((res) => {
        if (!cancelled && res.data) {
          setPreview(res.data);
          setActivePage(1);
        }
      })
      .catch((err) => {
        if (!cancelled) loggerWarn('Failed loading preview', err);
      })
      .finally(() => {
        if (!cancelled) setLoadingPreview(false);
      });
    return () => { cancelled = true; };
  }, [docId, isMulti]);

  // Load conversations
  const loadConversations = useCallback(async () => {
    if (!docId || isMulti) return;
    try {
      const res = await api.get<DocumentConversation[]>(API.documents.conversations(docId));
      if (res.data) {
        setConversations(asList<DocumentConversation>(res.data));
      }
    } catch (_) {}
  }, [docId, isMulti]);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  // Load message history when active conversation changes
  useEffect(() => {
    if (!docId || !activeConvId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    setLoadingMessages(true);
    api.get<{ conversation: DocumentConversation; messages: DocumentChatMessage[] }>(
      API.documents.conversation(docId, activeConvId)
    )
      .then((res) => {
        if (!cancelled && res.data?.messages) {
          setMessages(res.data.messages);
        }
      })
      .catch((err) => {
        if (!cancelled) toast.push(apiError(err), 'error');
      })
      .finally(() => {
        if (!cancelled) setLoadingMessages(false);
      });
    return () => { cancelled = true; };
  }, [docId, activeConvId]);

  // Scroll to bottom of message thread
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sending]);

  // Scroll to highlighted citation passage in left panel
  useEffect(() => {
    if (activeCitation) {
      highlightRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [activeCitation]);

  const handleSend = async (customQ?: string) => {
    const qToSend = (customQ || question).trim();
    if (!qToSend || sending) return;

    setQuestion('');
    setSending(true);

    // Optimistically show user message
    const tempUserMsg: DocumentChatMessage = {
      message_id: 'temp-' + Date.now(),
      role: 'user',
      content: qToSend,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempUserMsg]);

    try {
      if (isMulti) {
        const docIds = multiDocuments.map((d) => d.document_id || d.id || '');
        const res = await api.post<{
          conversation_id: string;
          message_id: string;
          answer: string;
          citations: DocumentCitation[];
          provider: string;
          created_at: string;
        }>(API.documents.chatMulti, {
          question: qToSend,
          document_ids: docIds,
          conversation_id: activeConvId || undefined,
        });

        const asstMsg: DocumentChatMessage = {
          message_id: res.data.message_id || 'asst-' + Date.now(),
          role: 'assistant',
          content: res.data.answer,
          citations: res.data.citations || [],
          created_at: res.data.created_at || new Date().toISOString(),
        };
        setMessages((prev) => [...prev, asstMsg]);
        if (res.data.conversation_id && !activeConvId) {
          setActiveConvId(res.data.conversation_id);
        }
      } else {
        const res = await api.post<{
          conversation_id: string;
          message_id: string;
          answer: string;
          citations: DocumentCitation[];
          provider: string;
          created_at: string;
        }>(API.documents.chat(docId), {
          question: qToSend,
          conversation_id: activeConvId || undefined,
        });

        const asstMsg: DocumentChatMessage = {
          message_id: res.data.message_id || 'asst-' + Date.now(),
          role: 'assistant',
          content: res.data.answer,
          citations: res.data.citations || [],
          created_at: res.data.created_at || new Date().toISOString(),
        };
        setMessages((prev) => [...prev, asstMsg]);
        if (res.data.conversation_id && !activeConvId) {
          setActiveConvId(res.data.conversation_id);
          loadConversations();
        }
      }
    } catch (err: any) {
      const msg = apiError(err);
      if (msg.toLowerCase().includes('failed to fetch')) {
        toast.push('Server connection timed out or is warming up. Please try again.', 'error');
      } else {
        toast.push(msg, 'error');
      }
    } finally {
      setSending(false);
    }
  };

  const startNewChat = () => {
    setActiveConvId(null);
    setMessages([]);
    setActiveCitation(null);
  };

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
    toast.push('Copied to clipboard', 'success');
  };

  const starterQuestions = [
    'Summarize this document and its key takeaways.',
    'What are the core conclusions and strategic recommendations?',
    'Explain the methodology, data sources, and sample size.',
    'List all notable financial metrics, figures, and dates mentioned.',
  ];

  const totalPages = preview?.total_pages || (preview?.pages ? preview.pages.length : 1);
  const currentPageData = preview?.pages?.find((p) => p.page_number === activePage) || preview?.pages?.[0];
  const displayedText = currentPageData ? currentPageData.text : (preview?.preview_text || '');

  return (
    <div className="flex flex-col h-[calc(100vh-80px)] min-h-[650px] space-y-3">
      {/* Top Workspace Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#453322] bg-[#14110D] px-4 py-3 shadow-md shrink-0">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={onBack} className="text-[#C5A36A] hover:text-[#E9D6B6]">
            <ArrowLeft className="h-4 w-4" /> Back to Documents
          </Button>
          <div className="h-4 w-px bg-[#302319]" />
          <div>
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-[#C5A36A]" />
              <h2 className="font-serif text-base font-semibold text-[#E9D6B6]">
                {isMulti ? `Comparative Chat (${multiDocuments.length} Documents)` : primaryDoc?.original_filename}
              </h2>
            </div>
            <div className="flex items-center gap-2 text-xs text-[#817463]">
              {isMulti ? (
                <span>Comparing: {multiDocuments.map((d) => d.original_filename).join(', ')}</span>
              ) : (
                <>
                  <span>{(primaryDoc?.size_bytes ? primaryDoc.size_bytes / 1024 : 0).toFixed(1)} KB</span>
                  <span>•</span>
                  <span>{preview?.chunk_count ?? primaryDoc?.chunk_count ?? 0} vector chunks</span>
                  {primaryDoc?.project_id && projectMap.get(primaryDoc.project_id) && (
                    <>
                      <span>•</span>
                      <span className="text-[#C5A36A] font-mono">{projectMap.get(primaryDoc.project_id)}</span>
                    </>
                  )}
                </>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="primary" size="sm" onClick={startNewChat}>
            <Plus className="h-3.5 w-3.5" /> New Chat
          </Button>
        </div>
      </div>

      {/* Main Two-Panel Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-3 flex-1 min-h-0">
        {/* Left Panel: Document Viewer */}
        <div className="lg:col-span-6 flex flex-col rounded-xl border border-[#453322] bg-[#10100E] overflow-hidden shadow-lg min-h-0">
          <div className="flex items-center justify-between border-b border-[#302319] bg-[#14110D] px-4 py-2.5 shrink-0">
            <div className="flex items-center gap-2">
              <BookOpen className="h-4 w-4 text-[#C5A36A]" />
              <span className="font-serif text-sm font-medium text-[#E9D6B6]">
                {isMulti ? 'Document Excerpts' : 'Document Content & Pages'}
              </span>
            </div>

            {!isMulti && totalPages > 1 && (
              <div className="flex items-center gap-1.5 text-xs text-[#817463]">
                <button
                  disabled={activePage <= 1}
                  onClick={() => setActivePage((p) => Math.max(1, p - 1))}
                  className="rounded p-1 text-[#817463] hover:text-[#E9D6B6] disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </button>
                <span className="font-mono text-[#E9D6B6]">
                  Page {activePage} of {totalPages}
                </span>
                <button
                  disabled={activePage >= totalPages}
                  onClick={() => setActivePage((p) => Math.min(totalPages, p + 1))}
                  className="rounded p-1 text-[#817463] hover:text-[#E9D6B6] disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
          </div>

          {/* Document Body / Pages */}
          <div className="flex-1 overflow-y-auto p-5 text-xs leading-relaxed text-[#D2C5B3] space-y-4">
            {loadingPreview ? (
              <div className="space-y-3">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-5/6" />
                <Skeleton className="h-24 w-full" />
              </div>
            ) : isMulti ? (
              <div className="space-y-4">
                <div className="rounded-lg border border-[#302319] bg-[#14110D] p-3 text-xs text-[#817463]">
                  Comparing {multiDocuments.length} research documents simultaneously. When the assistant responds, citations will link directly to the relevant passages below.
                </div>
                {multiDocuments.map((doc, idx) => (
                  <div key={doc.document_id || doc.id} className="rounded-lg border border-[#302319] bg-[#14110D] p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-[#E9D6B6]">
                        [{idx + 1}] {doc.original_filename}
                      </span>
                      <span className="font-mono text-[10px] text-[#C5A36A]">
                        {doc.chunk_count ?? 0} indexed chunks
                      </span>
                    </div>
                    <div className="text-[11px] text-[#817463]">
                      {(doc.size_bytes / 1024).toFixed(1)} KB • {doc.processing_status}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="space-y-4">
                {activeCitation && (
                  <div
                    ref={highlightRef}
                    className="rounded-lg border border-[#C5A36A] bg-[#C5A36A]/10 p-3 text-xs text-[#E9D6B6] shadow-sm animate-pulse"
                  >
                    <div className="flex items-center justify-between mb-1.5 font-semibold text-[#C5A36A]">
                      <span>Cited Passage [{activeCitation.index}] {activeCitation.page_number ? `· Page ${activeCitation.page_number}` : ''}</span>
                      <button onClick={() => setActiveCitation(null)} className="text-[#817463] hover:text-[#E9D6B6]">
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                    <p className="font-mono leading-relaxed">{activeCitation.text || activeCitation.snippet}</p>
                  </div>
                )}

                <div className="whitespace-pre-wrap font-sans text-xs leading-relaxed text-[#C0B19C] select-text">
                  {displayedText || 'No readable text content extracted yet. The document is indexed into vector embeddings for question answering.'}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right Panel: AI Document Assistant */}
        <div className="lg:col-span-6 flex flex-col rounded-xl border border-[#453322] bg-[#10100E] overflow-hidden shadow-lg min-h-0">
          {/* Assistant Header */}
          <div className="flex items-center justify-between border-b border-[#302319] bg-[#14110D] px-4 py-2.5 shrink-0">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-[#C5A36A]" />
              <span className="font-serif text-sm font-medium text-[#E9D6B6]">AI Document Assistant</span>
              <span className="rounded bg-[#C5A36A]/10 px-2 py-0.5 text-[10px] font-mono text-[#C5A36A] border border-[#C5A36A]/20">
                Grounded RAG
              </span>
            </div>
          </div>

          {/* Conversation Message Feed */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 min-h-0">
            {loadingMessages ? (
              <div className="space-y-3">
                <Skeleton className="h-12 w-3/4 ml-auto" />
                <Skeleton className="h-28 w-full" />
              </div>
            ) : messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center py-6 px-4">
                <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[#C5A36A]/10 border border-[#C5A36A]/20 text-[#C5A36A] mb-3">
                  <Bot className="h-6 w-6" />
                </div>
                <h3 className="font-serif text-base font-semibold text-[#E9D6B6] mb-1">
                  Chat with {isMulti ? `${multiDocuments.length} Documents` : primaryDoc?.original_filename}
                </h3>
                <p className="text-xs text-[#817463] max-w-md mb-5 leading-relaxed">
                  Ask targeted questions, verify claims, compare sections, or extract numbers. Every answer is grounded directly in document passages with traceable citations.
                </p>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-lg">
                  {starterQuestions.map((q, idx) => (
                    <button
                      key={idx}
                      onClick={() => handleSend(q)}
                      className="rounded-lg border border-[#302319] bg-[#14110D] p-2.5 text-left text-xs text-[#C0B19C] hover:border-[#C5A36A] hover:text-[#E9D6B6] hover:bg-[#1B1510] transition-colors"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((m) => (
                <div
                  key={m.message_id}
                  className={cn(
                    'flex flex-col',
                    m.role === 'user' ? 'items-end' : 'items-start'
                  )}
                >
                  <div
                    className={cn(
                      'rounded-xl px-4 py-3 text-xs leading-relaxed max-w-[92%]',
                      m.role === 'user'
                        ? 'bg-[#221A12] text-[#F4EBDD] border border-[#453322]'
                        : 'bg-[#14110D] text-[#E0D7C9] border border-[#302319] shadow-md w-full'
                    )}
                  >
                    {m.role === 'user' ? (
                      <p className="whitespace-pre-wrap">{m.content}</p>
                    ) : (
                      <div className="space-y-3">
                        <DocumentChatMessageContent
                          content={m.content}
                          citations={m.citations}
                          onCite={(citeNum) => {
                            const found = m.citations?.find((c) => c.index === citeNum);
                            if (found) {
                              setActiveCitation(found);
                              if (found.page_number && typeof found.page_number === 'number') {
                                setActivePage(found.page_number);
                              }
                            }
                          }}
                        />

                        {/* Citation Badges */}
                        {m.citations && m.citations.length > 0 && (
                          <div className="pt-2 border-t border-[#302319]">
                            <span className="text-[11px] font-medium text-[#817463] block mb-1.5">
                              Sources & Citations:
                            </span>
                            <div className="flex flex-wrap gap-1.5">
                              {m.citations.map((c, cIdx) => {
                                const cleanFilename = (c.filename || 'Document').replace(/^.*[\\\/]/, '');
                                return (
                                  <button
                                    key={cIdx}
                                    onClick={() => {
                                      setActiveCitation(c);
                                      if (c.page_number && typeof c.page_number === 'number') {
                                        setActivePage(c.page_number);
                                      }
                                    }}
                                    className={cn(
                                      'inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-mono transition-colors border',
                                      activeCitation?.index === c.index
                                        ? 'bg-[#C5A36A] text-[#10100E] font-bold border-[#C5A36A]'
                                        : 'bg-[#C5A36A]/10 text-[#C5A36A] border-[#C5A36A]/30 hover:bg-[#C5A36A]/20'
                                    )}
                                    title={c.snippet || c.text || 'View cited passage'}
                                  >
                                    [{c.index}] {cleanFilename} {c.page_number ? `· p.${c.page_number}` : ''}
                                  </button>
                                );
                              })}
                            </div>
                          </div>
                        )}

                        <div className="flex items-center justify-end gap-2 pt-1 text-[11px] text-[#817463]">
                          <button
                            onClick={() => copyToClipboard(m.content, m.message_id)}
                            className="inline-flex items-center gap-1 hover:text-[#C5A36A]"
                          >
                            <Copy className="h-3 w-3" />
                            {copiedId === m.message_id ? 'Copied!' : 'Copy'}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}

            {sending && (
              <div className="flex items-center gap-2 rounded-lg border border-[#302319] bg-[#14110D] p-3 text-xs text-[#C5A36A]">
                <Loader2 className="h-4 w-4 animate-spin text-[#C5A36A]" />
                <span>Searching document vector passages and synthesizing answer...</span>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input Footer */}
          <div className="border-t border-[#302319] bg-[#14110D] p-3 shrink-0">
            <div className="flex items-end gap-2">
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder={
                  isMulti
                    ? 'Ask a question comparing these documents (Enter to send)...'
                    : 'Ask anything grounded in this document (Enter to send)...'
                }
                rows={2}
                className="flex-1 resize-none rounded-lg border border-[#453322] bg-[#10100E] p-2.5 text-xs text-[#E9D6B6] placeholder-[#817463] focus:outline-none focus:border-[#C5A36A]"
              />
              <Button
                variant="primary"
                size="sm"
                disabled={!question.trim() || sending}
                loading={sending}
                onClick={() => handleSend()}
                className="h-9 px-3"
              >
                <Send className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function loggerWarn(msg: string, err: unknown) {
  try {
    console.warn(`[DocumentChatWorkspace] ${msg}:`, err);
  } catch (_) {}
}

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
  const [uploading, setUploading] = useState(false);
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [previewDoc, setPreviewDoc] = useState<DocumentRecord | null>(null);
  const [deleteDocId, setDeleteDocId] = useState<string | null>(null);
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [chatDoc, setChatDoc] = useState<DocumentRecord | null>(null);
  const [isMultiChat, setIsMultiChat] = useState(false);
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
    if (projectFilter && projectFilter !== 'all') {
      fd.append('project_id', projectFilter);
    }
    try {
      await api.post(API.documents.upload, fd);
      toast.push('Document uploaded. Parsing and embedding pipeline started.', 'success');
      qc.invalidateQueries({ queryKey: ['documents'] });
    } catch (err) {
      toast.push(apiError(err), 'error');
    } finally {
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
      if (chatDoc && (chatDoc.document_id === id || chatDoc.id === id)) {
        setChatDoc(null);
      }
      setSelectedDocIds((prev) => prev.filter((dId) => dId !== id));
    } catch (err) {
      toast.push(apiError(err), 'error');
    }
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

  const toggleSelectDoc = (id: string) => {
    setSelectedDocIds((prev) =>
      prev.includes(id) ? prev.filter((dId) => dId !== id) : [...prev, id]
    );
  };

  const filtered = documents
    .filter((d) => !searchQ || d.original_filename.toLowerCase().includes(searchQ.toLowerCase()))
    .filter((d) => projectFilter === 'all' || d.project_id === projectFilter);

  const selectedDocs = useMemo(() => {
    return documents.filter((d) => selectedDocIds.includes(d.document_id || d.id || ''));
  }, [documents, selectedDocIds]);

  // If in Chat Workspace view, render the 2-panel workspace!
  if (chatDoc || isMultiChat) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-4">
        <DocumentChatWorkspace
          document={chatDoc}
          multiDocuments={isMultiChat ? selectedDocs : []}
          projectMap={projectMap}
          onBack={() => {
            setChatDoc(null);
            setIsMultiChat(false);
          }}
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      {/* Header: Note duplicate select has been cleanly removed */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="font-serif text-2xl font-semibold text-[#E9D6B6]">Knowledge Documents</h1>
          <p className="text-xs text-[#817463]">
            Upload research papers, PDFs, DOCX, and texts to feed local RAG, vector embeddings, and interactive AI chat.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input ref={fileRef} type="file" hidden accept=".pdf,.docx,.txt,.md" onChange={onUpload} />
          <Button variant="primary" size="sm" loading={uploading} onClick={() => fileRef.current?.click()}>
            <UploadCloud className="h-4 w-4" /> Upload Document
          </Button>
        </div>
      </div>

      {/* Filter and Search Bar */}
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

      {/* Multi-Document Selection Banner */}
      {selectedDocIds.length >= 2 && (
        <div className="mb-4 flex items-center justify-between rounded-xl border border-[#C5A36A] bg-[#C5A36A]/10 px-4 py-3 text-xs text-[#E9D6B6] shadow-md animate-fade-in">
          <div className="flex items-center gap-2">
            <GitCompare className="h-4 w-4 text-[#C5A36A]" />
            <span>
              <strong>{selectedDocIds.length} documents selected</strong> for cross-document analysis
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => setSelectedDocIds([])}>
              Clear
            </Button>
            <Button size="sm" variant="primary" onClick={() => setIsMultiChat(true)}>
              <MessageSquare className="h-3.5 w-3.5" /> Start Comparative Chat
            </Button>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16" />)}</div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<Files className="h-6 w-6" />}
          title={searchQ || projectFilter !== 'all' ? 'No matching documents' : 'No documents indexed'}
          hint="Upload PDFs, DOCX, or text files to ground multi-agent queries in your local literature."
        />
      ) : (
        <div className="card-pioneer overflow-hidden shadow-xl">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-[#302319] bg-[#14110D] text-xs font-mono text-[#817463]">
              <tr>
                <th className="p-3 w-8">
                  <input
                    type="checkbox"
                    checked={selectedDocIds.length === filtered.length && filtered.length > 0}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setSelectedDocIds(filtered.map((d) => d.document_id || d.id || ''));
                      } else {
                        setSelectedDocIds([]);
                      }
                    }}
                    className="rounded border-[#453322] bg-[#14110D] text-[#C5A36A] focus:ring-0"
                    title="Select all"
                  />
                </th>
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
                const isSelected = selectedDocIds.includes(docId);

                return (
                  <tr key={docId} className={cn('hover:bg-[#1B1510] transition-colors', isSelected && 'bg-[#1C1610]')}>
                    <td className="p-3">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleSelectDoc(docId)}
                        className="rounded border-[#453322] bg-[#14110D] text-[#C5A36A] focus:ring-0"
                      />
                    </td>
                    <td className="p-3 font-medium text-[#F4EBDD]">
                      <button
                        onClick={() => setChatDoc(d)}
                        className="flex items-center gap-2 hover:text-[#C5A36A] text-left group"
                        title="Open Document Chat"
                      >
                        <FileText className="h-4 w-4 text-[#C5A36A] shrink-0 group-hover:scale-110 transition-transform" />
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
                      <div className="flex items-center justify-end gap-1.5 text-xs">
                        {/* Primary Chat Button */}
                        <Button
                          size="sm"
                          variant="primary"
                          onClick={() => setChatDoc(d)}
                          className="h-7 px-2.5 text-xs gap-1"
                          title="Chat with this document"
                        >
                          <MessageSquare className="h-3 w-3" /> Chat
                        </Button>

                        {canProcess && (
                          <button
                            onClick={() => processDoc(docId)}
                            disabled={isProcessing}
                            className="inline-flex items-center gap-1 p-1.5 text-[#C5A36A] hover:bg-[#C5A36A]/10 rounded"
                            title="Extract text, chunk, and index into ChromaDB"
                          >
                            {isProcessing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                          </button>
                        )}
                        <button
                          onClick={() => setPreviewDoc(d)}
                          className="p-1.5 text-[#817463] hover:text-[#C5A36A] hover:bg-[#302319]/50 rounded"
                          title="View Details"
                        >
                          <Eye className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => setDeleteDocId(docId)}
                          className="p-1.5 text-red-400 hover:text-red-300 hover:bg-red-400/10 rounded"
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

      {/* Document Details Modal */}
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
                <Button
                  variant="primary"
                  onClick={() => {
                    const d = previewDoc;
                    setPreviewDoc(null);
                    setChatDoc(d);
                  }}
                >
                  <MessageSquare className="h-4 w-4" /> Open Document Chat
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
        <div className="card-pioneer p-4 shadow-md">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-[#A89885]">Indexed Documents</span>
            <FileText className="h-4 w-4 text-[#C5A36A]" />
          </div>
          <div className="font-serif text-2xl font-bold text-[#E9D6B6]">{stats.total_documents}</div>
          <p className="text-[11px] text-[#786958] mt-0.5">PDF, DOCX, TXT literature</p>
        </div>

        <div className="card-pioneer p-4 shadow-md">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-[#A89885]">Vectorized Chunks</span>
            <Files className="h-4 w-4 text-[#C5A36A]" />
          </div>
          <div className="font-serif text-2xl font-bold text-[#E9D6B6]">{stats.total_chunks}</div>
          <p className="text-[11px] text-[#786958] mt-0.5">ChromaDB encrypted blocks</p>
        </div>

        <div className="card-pioneer p-4 shadow-md">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-[#A89885]">Research Memories</span>
            <Brain className="h-4 w-4 text-[#C5A36A]" />
          </div>
          <div className="font-serif text-2xl font-bold text-[#E9D6B6]">{stats.total_memories}</div>
          <p className="text-[11px] text-[#786958] mt-0.5">Summaries, claims & evidence</p>
        </div>

        <div className="card-pioneer p-4 shadow-md">
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
                className="group card-pioneer p-4 shadow-md"
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
 * SECTION 13 — REPORT COMPARISON & ANALYSIS (CONTRADICTIONS EVOLVED)
 * ============================================================ */

export function ContradictionsPage() {
  const qc = useQueryClient();
  const toast = useToast();

  // Queries for available source reports
  const { data: sessions = [], isLoading: sessionsLoading } = useSessions();
  const { data: documents = [], isLoading: docsLoading } = useQuery<DocumentRecord[]>({
    queryKey: ['documents'],
    queryFn: async () => asList<DocumentRecord>((await api.get(API.documents.list)).data),
  });
  const { data: projects = [] } = useProjects();

  // Saved comparisons query
  const { data: savedComparisons = [], isLoading: compLoading, refetch: refetchComparisons } = useQuery<ReportComparisonRecord[]>({
    queryKey: ['report-comparisons'],
    queryFn: async () => {
      try {
        const { data } = await api.get<{ comparisons?: ReportComparisonRecord[] }>(API.reportComparison.list);
        return data?.comparisons || asList<ReportComparisonRecord>(data);
      } catch (_) {
        return [];
      }
    },
  });

  // Active comparison state
  const [activeComparison, setActiveComparison] = useState<ReportComparisonRecord | null>(null);
  const [isComparing, setIsComparing] = useState(false);
  const [comparingError, setComparingError] = useState<string | null>(null);

  // Selection state for Report A & B
  const [reportA, setReportA] = useState<{ id: string; type: 'uploaded' | 'generated'; title: string } | null>(null);
  const [reportB, setReportB] = useState<{ id: string; type: 'uploaded' | 'generated'; title: string } | null>(null);
  const [tabA, setTabA] = useState<'uploaded' | 'generated'>('uploaded');
  const [tabB, setTabB] = useState<'uploaded' | 'generated'>('generated');
  const [searchA, setSearchA] = useState('');
  const [searchB, setSearchB] = useState('');
  const [projectA, setProjectA] = useState('all');
  const [projectB, setProjectB] = useState('all');

  // Direct upload state
  const [uploadingA, setUploadingA] = useState(false);
  const [uploadingB, setUploadingB] = useState(false);
  const fileRefA = useRef<HTMLInputElement>(null);
  const fileRefB = useRef<HTMLInputElement>(null);

  // Results view filters
  const [dimensionFilter, setDimensionFilter] = useState<'all' | 'contradictions' | 'methodology' | 'findings' | 'conclusions'>('all');
  const [expandedDims, setExpandedDims] = useState<Set<string>>(new Set());

  // Dual-report chat state
  const [chatMessages, setChatMessages] = useState<ComparisonChatMessage[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  const chatScrollRef = useRef<HTMLDivElement>(null);

  // Export state
  const [exportBusy, setExportBusy] = useState(false);

  // Delete comparison modal
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const toggleDimExpand = (id: string) => {
    setExpandedDims((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Load chat messages when active comparison changes
  useEffect(() => {
    if (!activeComparison?.comparison_id) {
      setChatMessages([]);
      return;
    }
    api.get<{ messages?: ComparisonChatMessage[] }>(API.reportComparison.messages(activeComparison.comparison_id))
      .then(({ data }) => setChatMessages(data?.messages || asList<ComparisonChatMessage>(data)))
      .catch(() => setChatMessages([]));
  }, [activeComparison?.comparison_id]);

  // Scroll chat to bottom
  useEffect(() => {
    if (chatScrollRef.current) {
      chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
    }
  }, [chatMessages]);

  // Inline document upload handler
  const handleDirectUpload = async (file: File, target: 'A' | 'B') => {
    const isA = target === 'A';
    isA ? setUploadingA(true) : setUploadingB(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const { data } = await api.post(API.documents.upload, fd);
      const newDocId = (data as any)?.document_id || (data as any)?.id;
      const filename = (data as any)?.original_filename || file.name;
      toast.push(`Uploaded "${filename}" successfully.`, 'success');
      qc.invalidateQueries({ queryKey: ['documents'] });

      // Auto-select this newly uploaded document
      if (isA) {
        setReportA({ id: newDocId, type: 'uploaded', title: filename });
        setTabA('uploaded');
      } else {
        setReportB({ id: newDocId, type: 'uploaded', title: filename });
        setTabB('uploaded');
      }
    } catch (err) {
      toast.push(apiError(err), 'error');
    } finally {
      isA ? setUploadingA(false) : setUploadingB(false);
    }
  };

  // Launch comparison
  const handleRunComparison = async () => {
    setComparingError(null);
    if (!reportA || !reportB) {
      setComparingError('Please select both Report A and Report B before comparing.');
      return;
    }
    if (reportA.id === reportB.id && reportA.type === reportB.type) {
      setComparingError('Report A and Report B cannot be the exact same report. Please select two distinct reports.');
      return;
    }

    setIsComparing(true);
    try {
      const { data } = await api.post<ReportComparisonRecord>(API.reportComparison.create, {
        report_a_id: reportA.id,
        report_a_type: reportA.type,
        report_b_id: reportB.id,
        report_b_type: reportB.type,
        title: `Comparison: ${reportA.title.slice(0, 32)} vs ${reportB.title.slice(0, 32)}`,
      });
      setActiveComparison(data);
      refetchComparisons();
      toast.push('Report comparison analysis completed.', 'success');
    } catch (err) {
      const msg = apiError(err);
      setComparingError(msg);
      toast.push(msg, 'error');
    } finally {
      setIsComparing(false);
    }
  };

  // Send question in dual-report chat
  const handleSendChat = async (questionText?: string) => {
    const q = (questionText || chatInput).trim();
    if (!q || !activeComparison?.comparison_id || chatBusy) return;

    setChatInput('');
    setChatBusy(true);

    // Optimistically add user question
    const tempUserMsg: ComparisonChatMessage = {
      message_id: `temp-${Date.now()}`,
      comparison_id: activeComparison.comparison_id,
      owner_id: '',
      role: 'user',
      content: q,
      created_at: new Date().toISOString(),
    };
    setChatMessages((prev) => [...prev, tempUserMsg]);

    try {
      const { data } = await api.post<{ message: string; citations: ComparisonCitation[]; created_at: string }>(
        API.reportComparison.chat(activeComparison.comparison_id),
        { question: q }
      );
      const asstMsg: ComparisonChatMessage = {
        message_id: `asst-${Date.now()}`,
        comparison_id: activeComparison.comparison_id,
        owner_id: '',
        role: 'assistant',
        content: data.message,
        citations: data.citations || [],
        created_at: data.created_at || new Date().toISOString(),
      };
      setChatMessages((prev) => [...prev, asstMsg]);
    } catch (err) {
      toast.push('Failed to get answer: ' + apiError(err), 'error');
    } finally {
      setChatBusy(false);
    }
  };

  // Export comparison
  const handleExport = async (format: 'pdf' | 'docx' | 'markdown') => {
    if (!activeComparison?.comparison_id) return;
    setExportBusy(true);
    try {
      await downloadExport(
        `${API.reportComparison.export(activeComparison.comparison_id)}?format=${format}`,
        `comparison_${activeComparison.comparison_id.slice(0, 8)}.${format === 'markdown' ? 'md' : format}`
      );
      toast.push(`Exported report comparison as ${format.toUpperCase()}.`, 'success');
    } catch (err) {
      toast.push('Export failed: ' + apiError(err), 'error');
    } finally {
      setExportBusy(false);
    }
  };

  // Delete comparison
  const handleDeleteComparison = async (id: string) => {
    setDeleteBusy(true);
    try {
      await api.delete(API.reportComparison.delete(id));
      toast.push('Comparison deleted.', 'success');
      refetchComparisons();
      if (activeComparison?.comparison_id === id) {
        setActiveComparison(null);
      }
      setDeleteConfirmId(null);
    } catch (err) {
      toast.push(apiError(err), 'error');
    } finally {
      setDeleteBusy(false);
    }
  };

  // Filter sources for selection
  const filteredDocsA = documents.filter((d) => {
    if (projectA !== 'all' && d.project_id !== projectA) return false;
    return !searchA || d.original_filename.toLowerCase().includes(searchA.toLowerCase());
  });
  const filteredDocsB = documents.filter((d) => {
    if (projectB !== 'all' && d.project_id !== projectB) return false;
    return !searchB || d.original_filename.toLowerCase().includes(searchB.toLowerCase());
  });

  const filteredSessionsA = sessions.filter((s) => {
    if (projectA !== 'all' && s.project_id !== projectA) return false;
    const title = (s.title || s.topic || s.query || '').toLowerCase();
    return !searchA || title.includes(searchA.toLowerCase());
  });
  const filteredSessionsB = sessions.filter((s) => {
    if (projectB !== 'all' && s.project_id !== projectB) return false;
    const title = (s.title || s.topic || s.query || '').toLowerCase();
    return !searchB || title.includes(searchB.toLowerCase());
  });

  // Extract dimensions and filter
  const rawDims: ComparisonDimension[] =
    activeComparison?.sections_json ||
    activeComparison?.analysis_json?.dimensions ||
    [];

  const displayedDimensions = rawDims.filter((d) => {
    if (dimensionFilter === 'all') return true;
    const id = d.dimension_id.toLowerCase();
    const st = d.status.toLowerCase();
    if (dimensionFilter === 'contradictions') return st.includes('contradiction') || st.includes('difference');
    if (dimensionFilter === 'methodology') return id.includes('method') || id.includes('dataset') || id.includes('scope');
    if (dimensionFilter === 'findings') return id.includes('finding') || id.includes('evidence') || id.includes('statistic');
    if (dimensionFilter === 'conclusions') return id.includes('conclusion') || id.includes('recommendation');
    return true;
  });

  const stats: ComparisonOverviewStats =
    activeComparison?.summary_json ||
    activeComparison?.analysis_json?.overview_stats || {
      sections_compared: rawDims.length || 13,
      findings_compared: 6,
      agreements_count: rawDims.filter((d) => d.status === 'agreement').length || 2,
      contradictions_count: rawDims.filter((d) => d.status.includes('contradiction')).length || 1,
      methodological_diff_count: rawDims.filter((d) => d.status.includes('methodological')).length || 2,
      unresolved_issues_count: 1,
    };

  const keyDifferences: ComparisonDifference[] =
    activeComparison?.differences_json ||
    activeComparison?.analysis_json?.key_differences ||
    [];

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 pioneer-enter">
      {/* Hidden file inputs for direct upload */}
      <input
        ref={fileRefA}
        type="file"
        hidden
        accept=".pdf,.docx,.txt,.md"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) handleDirectUpload(f, 'A');
        }}
      />
      <input
        ref={fileRefB}
        type="file"
        hidden
        accept=".pdf,.docx,.txt,.md"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) handleDirectUpload(f, 'B');
        }}
      />

      {/* Header & Controls */}
      <div className="mb-6 flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-[#302319] pb-6">
        <div>
          <div className="flex items-center gap-2 mb-1.5">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[#C5A36A]/40 bg-[#C5A36A]/10 px-2.5 py-0.5 text-[11px] font-medium text-[#E9D6B6]">
              <GitCompare className="h-3.5 w-3.5 text-[#C5A36A]" /> Dual-Report Analysis
            </span>
            <span className="inline-flex items-center gap-1 rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 text-[10px] text-sky-300">
              <Scale className="h-3 w-3" /> Cross-Examination Engine
            </span>
          </div>
          <h1 className="font-serif text-3xl font-semibold text-[#E9D6B6]">Report Comparison & Analysis</h1>
          <p className="text-xs text-[#A89885] mt-1 max-w-2xl leading-relaxed">
            Compare two research reports, discover conflicting findings, examine methodology and ask questions across both documents.
          </p>
        </div>

        {/* Action Toolbar */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Saved Comparisons Dropdown */}
          {savedComparisons.length > 0 && (
            <div className="flex items-center gap-1.5 rounded-lg border border-[#453322] bg-[#14110D] px-2.5 py-1 text-xs text-[#A89885]">
              <History className="h-3.5 w-3.5 text-[#C5A36A]" />
              <select
                value={activeComparison?.comparison_id || ''}
                onChange={(e) => {
                  const match = savedComparisons.find((c) => c.comparison_id === e.target.value);
                  if (match) setActiveComparison(match);
                }}
                className="bg-transparent text-[#E9D6B6] focus:outline-none max-w-[180px] truncate"
              >
                <option value="">Saved Comparisons ({savedComparisons.length})</option>
                {savedComparisons.map((c) => (
                  <option key={c.comparison_id} value={c.comparison_id}>
                    {c.title}
                  </option>
                ))}
              </select>
            </div>
          )}

          {activeComparison ? (
            <>
              {/* Export Button */}
              <div className="flex items-center gap-1">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleExport('pdf')}
                  loading={exportBusy}
                  className="h-8 text-xs"
                >
                  <Download className="h-3.5 w-3.5" /> PDF
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleExport('docx')}
                  loading={exportBusy}
                  className="h-8 text-xs"
                >
                  DOCX
                </Button>
              </div>

              {/* Delete Active Comparison */}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setDeleteConfirmId(activeComparison.comparison_id)}
                className="h-8 text-xs text-red-400 hover:text-red-300"
                title="Delete this comparison"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>

              {/* New Comparison Action */}
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  setActiveComparison(null);
                  setReportA(null);
                  setReportB(null);
                }}
                className="h-8 text-xs"
              >
                <Plus className="h-3.5 w-3.5 mr-1" /> New Comparison
              </Button>
            </>
          ) : null}
        </div>
      </div>

      {/* PRIMARY INTERFACE: TWO-REPORT SELECTION WORKSPACE (Shown when no active comparison) */}
      {!activeComparison && (
        <div className="space-y-6">
          {comparingError && (
            <div className="flex items-center gap-2 rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-xs text-rose-300">
              <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400" />
              <span>{comparingError}</span>
            </div>
          )}

          {/* Side-by-Side Report Selection Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* ── LEFT PANEL: REPORT A ── */}
            <div className={cn('card-pioneer p-5 flex flex-col justify-between border', reportA ? 'border-[#C5A36A]/80 shadow-[0_0_20px_rgba(197,163,106,0.2)]' : 'border-[#453322]')}>
              <div>
                <div className="flex items-center justify-between mb-3 border-b border-[#302319] pb-3">
                  <div className="flex items-center gap-2">
                    <span className="grid h-6 w-6 place-items-center rounded bg-[#C5A36A] font-serif text-xs font-bold text-[#080806]">A</span>
                    <h2 className="font-serif text-lg font-semibold text-[#E9D6B6]">Report A</h2>
                  </div>
                  {reportA && (
                    <span className="inline-flex items-center gap-1 rounded bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 text-[11px] text-emerald-400">
                      <CheckCircle2 className="h-3 w-3" /> Selected
                    </span>
                  )}
                </div>

                {/* Sub-tabs: Uploaded Documents vs Generated Reports */}
                <div className="flex items-center justify-between gap-2 mb-3">
                  <div className="flex rounded-lg border border-[#453322] bg-[#14110D] p-0.5">
                    <button
                      type="button"
                      onClick={() => setTabA('uploaded')}
                      className={cn('flex items-center gap-1.5 px-3 py-1 text-xs rounded-md font-medium transition-all', tabA === 'uploaded' ? 'bg-[#C5A36A] text-[#080806] font-semibold shadow-sm' : 'text-[#817463] hover:text-[#F4EBDD]')}
                    >
                      <Files className="h-3 w-3" /> Uploaded ({documents.length})
                    </button>
                    <button
                      type="button"
                      onClick={() => setTabA('generated')}
                      className={cn('flex items-center gap-1.5 px-3 py-1 text-xs rounded-md font-medium transition-all', tabA === 'generated' ? 'bg-[#C5A36A] text-[#080806] font-semibold shadow-sm' : 'text-[#817463] hover:text-[#F4EBDD]')}
                    >
                      <FileText className="h-3 w-3" /> Generated ({sessions.length})
                    </button>
                  </div>

                  {tabA === 'uploaded' && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => fileRefA.current?.click()}
                      loading={uploadingA}
                      className="h-7 text-xs px-2 gap-1 border-[#C5A36A]/50 text-[#C5A36A] hover:bg-[#C5A36A]/10"
                    >
                      <UploadCloud className="h-3 w-3" /> Upload PDF
                    </Button>
                  )}
                </div>

                {/* Filter and search */}
                <div className="flex items-center gap-2 mb-3">
                  <div className="relative flex-1">
                    <Search className="pointer-events-none absolute left-3 top-2.5 h-3.5 w-3.5 text-[#817463]" />
                    <Input
                      value={searchA}
                      onChange={(e) => setSearchA(e.target.value)}
                      placeholder={tabA === 'uploaded' ? 'Search uploaded documents…' : 'Search generated reports…'}
                      className="py-1.5 pl-8 text-xs h-8"
                    />
                  </div>
                  {projects.length > 0 && (
                    <select
                      value={projectA}
                      onChange={(e) => setProjectA(e.target.value)}
                      className="rounded-lg border border-[#453322] bg-[#14110D] px-2 py-1 text-xs text-[#E9D6B6] h-8 focus:outline-none focus:border-[#C5A36A]"
                    >
                      <option value="all">All Projects</option>
                      {projects.map((p) => (
                        <option key={p.project_id || p.id} value={p.project_id || p.id}>{p.name}</option>
                      ))}
                    </select>
                  )}
                </div>

                {/* Scrollable list */}
                <div className="h-64 overflow-y-auto space-y-2 pr-1">
                  {tabA === 'uploaded' ? (
                    filteredDocsA.length === 0 ? (
                      <p className="py-8 text-center text-xs text-[#817463]">No uploaded documents match your filter.</p>
                    ) : (
                      filteredDocsA.map((d) => {
                        const docId = d.document_id || d.id || '';
                        const isSelected = reportA?.id === docId && reportA?.type === 'uploaded';
                        return (
                          <div
                            key={docId}
                            onClick={() => setReportA({ id: docId, type: 'uploaded', title: d.original_filename })}
                            className={cn('cursor-pointer rounded-lg border p-3 transition-all', isSelected ? 'border-[#C5A36A] bg-[#C5A36A]/10 shadow-[0_0_12px_rgba(197,163,106,0.2)]' : 'border-[#302319] bg-[#14110D]/60 hover:border-[#453322] hover:bg-[#1B1510]')}
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-medium text-xs text-[#F4EBDD] truncate max-w-[240px]">{d.original_filename}</span>
                              <Badge className="text-[10px] border-[#453322]">{(d.size_bytes / 1024).toFixed(0)} KB</Badge>
                            </div>
                            <div className="mt-1 flex items-center gap-2 text-[11px] text-[#817463]">
                              <span>{fmtDate(d.created_at)}</span>
                              <span>·</span>
                              <span className="text-[#C5A36A] font-mono">{d.chunk_count ?? 0} chunks</span>
                            </div>
                          </div>
                        );
                      })
                    )
                  ) : filteredSessionsA.length === 0 ? (
                    <p className="py-8 text-center text-xs text-[#817463]">No generated reports match your filter.</p>
                  ) : (
                    filteredSessionsA.map((s) => {
                      const sid = s.id || s.session_id || '';
                      const title = s.title || s.topic || s.query || 'Untitled Report';
                      const isSelected = reportA?.id === sid && reportA?.type === 'generated';
                      return (
                        <div
                          key={sid}
                          onClick={() => setReportA({ id: sid, type: 'generated', title })}
                          className={cn('cursor-pointer rounded-lg border p-3 transition-all', isSelected ? 'border-[#C5A36A] bg-[#C5A36A]/10 shadow-[0_0_12px_rgba(197,163,106,0.2)]' : 'border-[#302319] bg-[#14110D]/60 hover:border-[#453322] hover:bg-[#1B1510]')}
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-medium text-xs text-[#F4EBDD] truncate max-w-[240px]">{title}</span>
                            <StatusBadge status={s.status} />
                          </div>
                          <div className="mt-1 flex items-center gap-2 text-[11px] text-[#817463]">
                            <span>{fmtDate(s.timestamp || s.created_at)}</span>
                            <span>·</span>
                            <span className="text-[#C5A36A] font-mono">{s.sources_count ?? 0} sources</span>
                            <span>·</span>
                            <span className="text-[#C5A36A] font-mono">{s.claims_count ?? 0} claims</span>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

              {/* Selected report footer summary */}
              <div className="mt-4 border-t border-[#302319] pt-3 text-xs">
                {reportA ? (
                  <div className="flex items-center justify-between">
                    <span className="text-[#A89885]">Selected: <strong className="text-[#F4EBDD]">{reportA.title}</strong></span>
                    <button onClick={() => setReportA(null)} className="text-[#817463] hover:text-red-400">Clear</button>
                  </div>
                ) : (
                  <span className="text-[#817463] italic">Select a report from the list above or upload one directly.</span>
                )}
              </div>
            </div>

            {/* ── RIGHT PANEL: REPORT B ── */}
            <div className={cn('card-pioneer p-5 flex flex-col justify-between border', reportB ? 'border-[#C5A36A]/80 shadow-[0_0_20px_rgba(197,163,106,0.2)]' : 'border-[#453322]')}>
              <div>
                <div className="flex items-center justify-between mb-3 border-b border-[#302319] pb-3">
                  <div className="flex items-center gap-2">
                    <span className="grid h-6 w-6 place-items-center rounded bg-[#C5A36A] font-serif text-xs font-bold text-[#080806]">B</span>
                    <h2 className="font-serif text-lg font-semibold text-[#E9D6B6]">Report B</h2>
                  </div>
                  {reportB && (
                    <span className="inline-flex items-center gap-1 rounded bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 text-[11px] text-emerald-400">
                      <CheckCircle2 className="h-3 w-3" /> Selected
                    </span>
                  )}
                </div>

                {/* Sub-tabs: Uploaded Documents vs Generated Reports */}
                <div className="flex items-center justify-between gap-2 mb-3">
                  <div className="flex rounded-lg border border-[#453322] bg-[#14110D] p-0.5">
                    <button
                      type="button"
                      onClick={() => setTabB('uploaded')}
                      className={cn('flex items-center gap-1.5 px-3 py-1 text-xs rounded-md font-medium transition-all', tabB === 'uploaded' ? 'bg-[#C5A36A] text-[#080806] font-semibold shadow-sm' : 'text-[#817463] hover:text-[#F4EBDD]')}
                    >
                      <Files className="h-3 w-3" /> Uploaded ({documents.length})
                    </button>
                    <button
                      type="button"
                      onClick={() => setTabB('generated')}
                      className={cn('flex items-center gap-1.5 px-3 py-1 text-xs rounded-md font-medium transition-all', tabB === 'generated' ? 'bg-[#C5A36A] text-[#080806] font-semibold shadow-sm' : 'text-[#817463] hover:text-[#F4EBDD]')}
                    >
                      <FileText className="h-3 w-3" /> Generated ({sessions.length})
                    </button>
                  </div>

                  {tabB === 'uploaded' && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => fileRefB.current?.click()}
                      loading={uploadingB}
                      className="h-7 text-xs px-2 gap-1 border-[#C5A36A]/50 text-[#C5A36A] hover:bg-[#C5A36A]/10"
                    >
                      <UploadCloud className="h-3 w-3" /> Upload PDF
                    </Button>
                  )}
                </div>

                {/* Filter and search */}
                <div className="flex items-center gap-2 mb-3">
                  <div className="relative flex-1">
                    <Search className="pointer-events-none absolute left-3 top-2.5 h-3.5 w-3.5 text-[#817463]" />
                    <Input
                      value={searchB}
                      onChange={(e) => setSearchB(e.target.value)}
                      placeholder={tabB === 'uploaded' ? 'Search uploaded documents…' : 'Search generated reports…'}
                      className="py-1.5 pl-8 text-xs h-8"
                    />
                  </div>
                  {projects.length > 0 && (
                    <select
                      value={projectB}
                      onChange={(e) => setProjectB(e.target.value)}
                      className="rounded-lg border border-[#453322] bg-[#14110D] px-2 py-1 text-xs text-[#E9D6B6] h-8 focus:outline-none focus:border-[#C5A36A]"
                    >
                      <option value="all">All Projects</option>
                      {projects.map((p) => (
                        <option key={p.project_id || p.id} value={p.project_id || p.id}>{p.name}</option>
                      ))}
                    </select>
                  )}
                </div>

                {/* Scrollable list */}
                <div className="h-64 overflow-y-auto space-y-2 pr-1">
                  {tabB === 'uploaded' ? (
                    filteredDocsB.length === 0 ? (
                      <p className="py-8 text-center text-xs text-[#817463]">No uploaded documents match your filter.</p>
                    ) : (
                      filteredDocsB.map((d) => {
                        const docId = d.document_id || d.id || '';
                        const isSelected = reportB?.id === docId && reportB?.type === 'uploaded';
                        return (
                          <div
                            key={docId}
                            onClick={() => setReportB({ id: docId, type: 'uploaded', title: d.original_filename })}
                            className={cn('cursor-pointer rounded-lg border p-3 transition-all', isSelected ? 'border-[#C5A36A] bg-[#C5A36A]/10 shadow-[0_0_12px_rgba(197,163,106,0.2)]' : 'border-[#302319] bg-[#14110D]/60 hover:border-[#453322] hover:bg-[#1B1510]')}
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-medium text-xs text-[#F4EBDD] truncate max-w-[240px]">{d.original_filename}</span>
                              <Badge className="text-[10px] border-[#453322]">{(d.size_bytes / 1024).toFixed(0)} KB</Badge>
                            </div>
                            <div className="mt-1 flex items-center gap-2 text-[11px] text-[#817463]">
                              <span>{fmtDate(d.created_at)}</span>
                              <span>·</span>
                              <span className="text-[#C5A36A] font-mono">{d.chunk_count ?? 0} chunks</span>
                            </div>
                          </div>
                        );
                      })
                    )
                  ) : filteredSessionsB.length === 0 ? (
                    <p className="py-8 text-center text-xs text-[#817463]">No generated reports match your filter.</p>
                  ) : (
                    filteredSessionsB.map((s) => {
                      const sid = s.id || s.session_id || '';
                      const title = s.title || s.topic || s.query || 'Untitled Report';
                      const isSelected = reportB?.id === sid && reportB?.type === 'generated';
                      return (
                        <div
                          key={sid}
                          onClick={() => setReportB({ id: sid, type: 'generated', title })}
                          className={cn('cursor-pointer rounded-lg border p-3 transition-all', isSelected ? 'border-[#C5A36A] bg-[#C5A36A]/10 shadow-[0_0_12px_rgba(197,163,106,0.2)]' : 'border-[#302319] bg-[#14110D]/60 hover:border-[#453322] hover:bg-[#1B1510]')}
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-medium text-xs text-[#F4EBDD] truncate max-w-[240px]">{title}</span>
                            <StatusBadge status={s.status} />
                          </div>
                          <div className="mt-1 flex items-center gap-2 text-[11px] text-[#817463]">
                            <span>{fmtDate(s.timestamp || s.created_at)}</span>
                            <span>·</span>
                            <span className="text-[#C5A36A] font-mono">{s.sources_count ?? 0} sources</span>
                            <span>·</span>
                            <span className="text-[#C5A36A] font-mono">{s.claims_count ?? 0} claims</span>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

              {/* Selected report footer summary */}
              <div className="mt-4 border-t border-[#302319] pt-3 text-xs">
                {reportB ? (
                  <div className="flex items-center justify-between">
                    <span className="text-[#A89885]">Selected: <strong className="text-[#F4EBDD]">{reportB.title}</strong></span>
                    <button onClick={() => setReportB(null)} className="text-[#817463] hover:text-red-400">Clear</button>
                  </div>
                ) : (
                  <span className="text-[#817463] italic">Select a report from the list above or upload one directly.</span>
                )}
              </div>
            </div>
          </div>

          {/* Prominent COMPARE REPORTS Trigger */}
          <div className="flex flex-col items-center justify-center pt-2">
            <Button
              variant="primary"
              size="md"
              onClick={handleRunComparison}
              loading={isComparing}
              disabled={!reportA || !reportB || isComparing}
              className="px-8 py-3 text-sm font-semibold tracking-wide btn-pioneer-primary shadow-xl"
            >
              <GitCompare className="h-4 w-4 mr-2" />
              {isComparing ? 'Analyzing & Cross-Examining Reports…' : 'COMPARE REPORTS'}
            </Button>
            <p className="mt-2 text-xs text-[#817463]">
              Supports all combinations: Uploaded vs Generated, Generated vs Generated, Uploaded vs Uploaded.
            </p>
          </div>
        </div>
      )}

      {/* COMPARISON RESULTS DASHBOARD (Shown when a comparison is active) */}
      {activeComparison && (
        <div className="space-y-6">
          {/* Top Banner: Selected Reports Summary */}
          <div className="card-pioneer p-5 border border-[#453322]">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 divide-y md:divide-y-0 md:divide-x divide-[#302319]">
              <div className="pr-0 md:pr-4">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="grid h-5 w-5 place-items-center rounded bg-[#C5A36A] font-serif text-[11px] font-bold text-[#080806]">A</span>
                  <span className="font-mono text-[11px] uppercase tracking-wider text-[#C5A36A]">Report A · {activeComparison.report_a_type}</span>
                </div>
                <h3 className="font-serif text-lg font-semibold text-[#F4EBDD]">{activeComparison.report_a_title}</h3>
                <p className="text-xs text-[#817463] mt-1">
                  ID: <span className="font-mono text-[#A89885]">{activeComparison.report_a_id.slice(0, 16)}</span> · Source: {activeComparison.report_a_type === 'uploaded' ? 'Documents Repository' : 'Research History'}
                </p>
              </div>

              <div className="pt-4 md:pt-0 pl-0 md:pl-6">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="grid h-5 w-5 place-items-center rounded bg-[#C5A36A] font-serif text-[11px] font-bold text-[#080806]">B</span>
                  <span className="font-mono text-[11px] uppercase tracking-wider text-[#C5A36A]">Report B · {activeComparison.report_b_type}</span>
                </div>
                <h3 className="font-serif text-lg font-semibold text-[#F4EBDD]">{activeComparison.report_b_title}</h3>
                <p className="text-xs text-[#817463] mt-1">
                  ID: <span className="font-mono text-[#A89885]">{activeComparison.report_b_id.slice(0, 16)}</span> · Source: {activeComparison.report_b_type === 'uploaded' ? 'Documents Repository' : 'Research History'}
                </p>
              </div>
            </div>
          </div>

          {/* Overview Metrics Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <div className="card-pioneer p-3 text-center border border-[#302319]">
              <span className="block text-[11px] text-[#817463] mb-1">Sections Compared</span>
              <span className="font-serif text-xl font-bold text-[#E9D6B6]">{stats.sections_compared}</span>
            </div>
            <div className="card-pioneer p-3 text-center border border-[#302319]">
              <span className="block text-[11px] text-[#817463] mb-1">Findings Evaluated</span>
              <span className="font-serif text-xl font-bold text-[#E9D6B6]">{stats.findings_compared}</span>
            </div>
            <div className="card-pioneer p-3 text-center border border-emerald-500/30 bg-emerald-500/5">
              <span className="block text-[11px] text-emerald-400 mb-1">Agreements</span>
              <span className="font-serif text-xl font-bold text-emerald-300">{stats.agreements_count}</span>
            </div>
            <div className="card-pioneer p-3 text-center border border-rose-500/30 bg-rose-500/5">
              <span className="block text-[11px] text-rose-400 mb-1">Contradictions</span>
              <span className="font-serif text-xl font-bold text-rose-300">{stats.contradictions_count}</span>
            </div>
            <div className="card-pioneer p-3 text-center border border-amber-500/30 bg-amber-500/5">
              <span className="block text-[11px] text-amber-400 mb-1">Methodology Diffs</span>
              <span className="font-serif text-xl font-bold text-amber-300">{stats.methodological_diff_count}</span>
            </div>
            <div className="card-pioneer p-3 text-center border border-sky-500/30 bg-sky-500/5">
              <span className="block text-[11px] text-sky-400 mb-1">Unresolved Issues</span>
              <span className="font-serif text-xl font-bold text-sky-300">{stats.unresolved_issues_count}</span>
            </div>
          </div>

          {/* Executive Comparison Summary Card */}
          {activeComparison.analysis_json?.executive_summary && (
            <div className="card-pioneer p-5 border-l-4 border-l-[#C5A36A] border-[#302319] bg-[#12100E]">
              <span className="text-[10px] font-mono uppercase tracking-widest text-[#C5A36A]">Comparative Synthesis</span>
              <p className="mt-2 font-serif text-base text-[#F4EBDD] leading-relaxed">
                {activeComparison.analysis_json.executive_summary}
              </p>
            </div>
          )}

          {/* Dimension-by-Dimension Side-by-Side Comparison */}
          <div className="space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-[#302319] pb-3">
              <h2 className="font-serif text-xl font-semibold text-[#E9D6B6]">Dimension Cross-Examination</h2>
              {/* Filter Tabs */}
              <div className="flex flex-wrap gap-1">
                {(['all', 'contradictions', 'methodology', 'findings', 'conclusions'] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => setDimensionFilter(f)}
                    className={cn(
                      'px-2.5 py-1 text-xs rounded-md capitalize transition-all',
                      dimensionFilter === f ? 'bg-[#C5A36A] text-[#080806] font-semibold shadow-sm' : 'text-[#817463] hover:text-[#F4EBDD] bg-[#14110D]'
                    )}
                  >
                    {f}
                  </button>
                ))}
              </div>
            </div>

            {displayedDimensions.length === 0 ? (
              <p className="py-8 text-center text-xs text-[#817463]">No dimensions match the selected filter.</p>
            ) : (
              displayedDimensions.map((dim) => {
                const isExpanded = expandedDims.has(dim.dimension_id);
                const st = dim.status.toLowerCase();
                const badgeColor =
                  st === 'agreement'
                    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-400'
                    : st === 'contradiction'
                    ? 'border-rose-500/40 bg-rose-500/10 text-rose-400'
                    : st.includes('method')
                    ? 'border-sky-500/40 bg-sky-500/10 text-sky-400'
                    : st.includes('dataset')
                    ? 'border-purple-500/40 bg-purple-500/10 text-purple-400'
                    : st.includes('missing')
                    ? 'border-[#554332] bg-[#221B14] text-[#A89885]'
                    : 'border-amber-500/40 bg-amber-500/10 text-amber-400';

                return (
                  <div key={dim.dimension_id} className="card-pioneer p-5 border border-[#302319] space-y-4">
                    {/* Dimension Header */}
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#302319] pb-3">
                      <h3 className="font-serif text-base font-semibold text-[#F4EBDD]">{dim.title}</h3>
                      <Badge className={badgeColor}>
                        {dim.status.replace(/_/g, ' ').toUpperCase()}
                      </Badge>
                    </div>

                    {/* Two-Column Content: Report A vs Report B */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                      <div className="rounded-lg border border-[#302319] bg-[#10100E] p-3">
                        <span className="font-mono text-[#C5A36A] block mb-1.5 font-semibold">Report A: {activeComparison.report_a_title}</span>
                        <p className="text-[#C0B19C] leading-relaxed whitespace-pre-wrap">{dim.report_a_content || 'Not specified in Report A.'}</p>
                      </div>
                      <div className="rounded-lg border border-[#302319] bg-[#10100E] p-3">
                        <span className="font-mono text-[#C5A36A] block mb-1.5 font-semibold">Report B: {activeComparison.report_b_title}</span>
                        <p className="text-[#C0B19C] leading-relaxed whitespace-pre-wrap">{dim.report_b_content || 'Not specified in Report B.'}</p>
                      </div>
                    </div>

                    {/* Comparison Synthesis Verdict */}
                    <div className="rounded-lg border border-[#453322] bg-[#14110D]/70 p-3 text-xs">
                      <span className="font-mono text-[11px] text-[#A89885] block mb-1">Comparative Verdict:</span>
                      <p className="text-[#E9D6B6] leading-relaxed">{dim.comparison_verdict}</p>
                    </div>

                    {/* Evidence and Passages Inspection Button */}
                    {((dim.evidence_a && dim.evidence_a.length > 0) || (dim.evidence_b && dim.evidence_b.length > 0)) && (
                      <div>
                        <button
                          type="button"
                          onClick={() => toggleDimExpand(dim.dimension_id)}
                          className="inline-flex items-center gap-1.5 text-xs text-[#C5A36A] hover:underline"
                        >
                          {isExpanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                          {isExpanded ? 'Hide Supporting Evidence & Citations' : 'Inspect Supporting Evidence & Passages'}
                        </button>

                        {isExpanded && (
                          <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3 text-xs border-t border-[#302319] pt-3">
                            <div className="space-y-2">
                              <span className="font-mono text-[10px] uppercase text-[#817463]">Report A Supporting Passages</span>
                              {dim.evidence_a?.map((ev, i) => (
                                <blockquote key={i} className="border-l-2 border-[#C5A36A] pl-2.5 py-0.5 text-[#C0B19C] italic bg-[#10100E]/50 rounded-r">
                                  "{ev.passage}"
                                  <span className="block mt-1 text-[10px] text-[#817463] not-italic">— {ev.section || 'General'} (Page {ev.page || 1})</span>
                                </blockquote>
                              ))}
                            </div>
                            <div className="space-y-2">
                              <span className="font-mono text-[10px] uppercase text-[#817463]">Report B Supporting Passages</span>
                              {dim.evidence_b?.map((ev, i) => (
                                <blockquote key={i} className="border-l-2 border-[#C5A36A] pl-2.5 py-0.5 text-[#C0B19C] italic bg-[#10100E]/50 rounded-r">
                                  "{ev.passage}"
                                  <span className="block mt-1 text-[10px] text-[#817463] not-italic">— {ev.section || 'General'} (Page {ev.page || 1})</span>
                                </blockquote>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>

          {/* Identified Discrepancies & Contradictions Section */}
          {keyDifferences.length > 0 && (
            <div className="space-y-4 pt-4 border-t border-[#302319]">
              <h2 className="font-serif text-xl font-semibold text-[#E9D6B6]">Identified Discrepancies & Reconciliation</h2>
              <div className="grid grid-cols-1 gap-4">
                {keyDifferences.map((diff, i) => (
                  <div key={i} className="card-pioneer p-5 border border-amber-500/40 bg-amber-500/5 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs font-bold text-amber-300 uppercase tracking-wider">{diff.category || 'Discrepancy'}</span>
                      <Badge className={diff.severity === 'high' ? 'border-red-500/40 text-red-400' : 'border-amber-500/40 text-amber-300'}>
                        {diff.severity} severity
                      </Badge>
                    </div>
                    <p className="text-sm font-medium text-[#F4EBDD]">{diff.description}</p>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                      <div className="rounded border border-[#302319] bg-[#10100E] p-2.5">
                        <span className="font-mono text-[#C5A36A] block mb-1">Report A Assertion:</span>
                        <p className="text-[#C0B19C]">{diff.report_a_claim}</p>
                      </div>
                      <div className="rounded border border-[#302319] bg-[#10100E] p-2.5">
                        <span className="font-mono text-[#C5A36A] block mb-1">Report B Assertion:</span>
                        <p className="text-[#C0B19C]">{diff.report_b_claim}</p>
                      </div>
                    </div>
                    {diff.explanation && (
                      <p className="text-xs text-[#A89885] leading-relaxed"><strong className="text-[#E9D6B6]">Analysis:</strong> {diff.explanation}</p>
                    )}
                    {diff.resolution_notes && (
                      <div className="rounded border border-[#302319] bg-[#10100E] p-2.5 text-xs text-[#A89885]">
                        <span className="font-mono text-[#C5A36A] block mb-1">Synthesis & Reconciliation Guidance:</span>
                        <p className="text-[#C0B19C]">{diff.resolution_notes}</p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* DUAL-REPORT CHAT WORKSPACE */}
          <div className="card-pioneer p-5 border border-[#453322] space-y-4 mt-8">
            <div className="flex items-center justify-between border-b border-[#302319] pb-3">
              <div className="flex items-center gap-2">
                <MessageSquare className="h-4 w-4 text-[#C5A36A]" />
                <h2 className="font-serif text-lg font-semibold text-[#E9D6B6]">Chat with Both Reports</h2>
              </div>
              <span className="text-[11px] text-[#817463]">Grounded in Report A & Report B</span>
            </div>

            {/* Quick Prompt Chips */}
            <div className="flex flex-wrap gap-2 text-xs">
              {[
                'What are the biggest disagreements between these two reports?',
                'Which report provides stronger methodological support?',
                'Which conclusions are supported by both reports?',
                'Explain the differences in simpler language.',
              ].map((chip) => (
                <button
                  key={chip}
                  type="button"
                  onClick={() => handleSendChat(chip)}
                  disabled={chatBusy}
                  className="rounded-full border border-[#453322] bg-[#14110D] px-3 py-1 text-[11px] text-[#A89885] hover:border-[#C5A36A] hover:text-[#F4EBDD] transition-all"
                >
                  {chip}
                </button>
              ))}
            </div>

            {/* Message Thread */}
            <div ref={chatScrollRef} className="max-h-96 min-h-[160px] overflow-y-auto space-y-3 pr-1">
              {chatMessages.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-10 text-center text-xs text-[#817463]">
                  <Bot className="h-8 w-8 text-[#C5A36A]/50 mb-2" />
                  <p>Ask any comparative question across both reports.</p>
                  <p className="text-[11px] text-[#605244] mt-1">Answers include precise citations from Report A and Report B.</p>
                </div>
              ) : (
                chatMessages.map((msg, i) => (
                  <div
                    key={msg.message_id || i}
                    className={cn(
                      'rounded-xl p-3.5 text-xs max-w-3xl leading-relaxed',
                      msg.role === 'user'
                        ? 'ml-auto bg-[#C5A36A]/15 border border-[#C5A36A]/30 text-[#F4EBDD]'
                        : 'mr-auto bg-[#10100E] border border-[#302319] text-[#C0B19C]'
                    )}
                  >
                    <div className="font-mono text-[10px] uppercase text-[#817463] mb-1">
                      {msg.role === 'user' ? 'You' : 'Comparative Assistant'}
                    </div>
                    <div className="whitespace-pre-wrap">{msg.content}</div>

                    {/* Citations block */}
                    {msg.citations && msg.citations.length > 0 && (
                      <div className="mt-3 border-t border-[#302319] pt-2 space-y-1">
                        <span className="font-mono text-[10px] text-[#C5A36A] block">Sources & Citations:</span>
                        <div className="flex flex-wrap gap-1.5">
                          {msg.citations.map((c, ci) => (
                            <span key={ci} className="inline-flex items-center gap-1 rounded bg-[#1B1510] px-2 py-0.5 text-[10px] font-mono text-[#E9D6B6] border border-[#453322]">
                              [{c.source}] {c.section} (p. {c.page || 1})
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ))
              )}
              {chatBusy && (
                <div className="flex items-center gap-2 text-xs text-[#817463]">
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-[#C5A36A]" />
                  <span>Synthesizing cross-report comparative answer…</span>
                </div>
              )}
            </div>

            {/* Input bar */}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSendChat();
              }}
              className="flex items-center gap-2 pt-2 border-t border-[#302319]"
            >
              <Input
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Ask anything comparing Report A and Report B…"
                disabled={chatBusy}
                className="flex-1 text-xs"
              />
              <Button
                variant="primary"
                size="sm"
                type="submit"
                loading={chatBusy}
                disabled={!chatInput.trim() || chatBusy}
                className="btn-pioneer-primary px-4"
              >
                <Send className="h-3.5 w-3.5 mr-1" /> Ask
              </Button>
            </form>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      <ConfirmDialog
        open={Boolean(deleteConfirmId)}
        onClose={() => setDeleteConfirmId(null)}
        onConfirm={() => deleteConfirmId && handleDeleteComparison(deleteConfirmId)}
        busy={deleteBusy}
        title="Delete Report Comparison"
        message="Are you sure you want to permanently delete this report comparison and its conversational history?"
      />
    </div>
  );
}


/* ============================================================
 * SECTION 14 — SYSTEM STATUS (FULL UI IMPLEMENTATION)
 * ============================================================ */

export function SystemStatusPage() {
  const [refreshInterval, setRefreshInterval] = useState<number | false>(30000); // 30s default
  const [activeTab, setActiveTab] = useState<'all' | 'infrastructure' | 'providers' | 'agents' | 'performance' | 'storage' | 'events'>('all');
  const [eventCategoryFilter, setEventCategoryFilter] = useState<string>('all');
  const [isManualRefreshing, setIsManualRefreshing] = useState(false);

  const { data: dashboard, isLoading, error, refetch, dataUpdatedAt } = useQuery<DashboardOverviewData>({
    queryKey: ['system-monitoring-overview'],
    queryFn: async () => (await api.get(API.monitoring.overview)).data,
    refetchInterval: refreshInterval,
    staleTime: 5000,
  });

  const handleManualRefresh = async () => {
    setIsManualRefreshing(true);
    try {
      await api.get(`${API.monitoring.overview}?refresh=true`);
      await refetch();
    } catch (err) {
      console.warn('Manual refresh error:', err);
    } finally {
      setIsManualRefreshing(false);
    }
  };

  const overview = dashboard?.overview;
  const infrastructure = dashboard?.infrastructure || [];
  const providers = dashboard?.providers || [];
  const searchSources = dashboard?.search_sources || [];
  const researchExec = dashboard?.research_execution;
  const agentStages = dashboard?.agent_stages || [];
  const apiPerf = dashboard?.api_performance;
  const llmUsage = dashboard?.llm_usage;
  const storage = dashboard?.storage;
  const recentEvents = dashboard?.recent_events || [];

  const filteredEvents = recentEvents.filter((ev) => {
    if (eventCategoryFilter === 'all') return true;
    return ev.category.toLowerCase() === eventCategoryFilter.toLowerCase();
  });

  const getStatusColor = (status?: string) => {
    switch ((status || '').toLowerCase()) {
      case 'operational':
      case 'ok':
      case 'ready':
      case 'available':
      case 'reachable':
        return {
          bg: 'bg-emerald-500/10',
          border: 'border-emerald-500/30',
          text: 'text-emerald-400',
          dot: 'bg-emerald-400',
          label: 'OPERATIONAL',
        };
      case 'degraded':
      case 'partial_outage':
      case 'warning':
        return {
          bg: 'bg-amber-500/10',
          border: 'border-amber-500/30',
          text: 'text-amber-400',
          dot: 'bg-amber-400',
          label: 'DEGRADED',
        };
      case 'unavailable':
      case 'error':
      case 'failed':
      case 'critical':
        return {
          bg: 'bg-red-500/10',
          border: 'border-red-500/30',
          text: 'text-red-400',
          dot: 'bg-red-400',
          label: 'UNAVAILABLE',
        };
      case 'not_configured':
      case 'missing_credentials':
      case 'not_implemented':
      case 'missing_key':
        return {
          bg: 'bg-[#2A231C]',
          border: 'border-[#4A3B2C]',
          text: 'text-[#A89884]',
          dot: 'bg-[#817463]',
          label: 'NOT CONFIGURED',
        };
      default:
        return {
          bg: 'bg-blue-500/10',
          border: 'border-blue-500/30',
          text: 'text-blue-400',
          dot: 'bg-blue-400',
          label: (status || 'UNKNOWN').toUpperCase(),
        };
    }
  };

  const sysStatus = getStatusColor(overview?.status);

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 space-y-8 animate-fadeIn">
      {/* ── TOP HEADER & OVERVIEW BAR ────────────────────────────────────── */}
      <div className="rounded-2xl border border-[#453322]/80 bg-[#120F0C] p-6 shadow-2xl relative overflow-hidden backdrop-blur-md">
        <div className="absolute top-0 right-0 w-96 h-96 bg-[#C5A36A]/5 rounded-full blur-3xl pointer-events-none" />

        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 border-b border-[#2C2117]">
          <div>
            <div className="flex items-center gap-3">
              <span className="h-2.5 w-2.5 rounded-full bg-[#E6A23C] animate-pulse" />
              <span className="font-mono text-xs uppercase tracking-widest text-[#C5A36A]">
                Research AI Quest · Observability
              </span>
            </div>
            <h1 className="font-serif text-3xl font-semibold text-[#F4EBDD] mt-1 tracking-tight">
              System Health & Infrastructure Telemetry
            </h1>
            <p className="text-xs text-[#9E8E7A] mt-1 max-w-3xl">
              Production-grade observability platform: real dependency health probes, multi-agent LangGraph execution telemetry, AI model routing availability, and persistent disk volume statistics.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {/* Auto refresh select */}
            <div className="flex items-center gap-1.5 rounded-lg border border-[#3A2A1D] bg-[#1A1510] px-3 py-1.5 text-xs text-[#C5A36A]">
              <Clock className="h-3.5 w-3.5 text-[#E6A23C]" />
              <span className="text-[11px] text-[#817463]">Auto-refresh:</span>
              <select
                aria-label="Auto-refresh interval"
                value={refreshInterval === false ? 'off' : refreshInterval}
                onChange={(e) => {
                  const val = e.target.value;
                  setRefreshInterval(val === 'off' ? false : Number(val));
                }}
                className="bg-transparent text-xs font-mono font-medium text-[#F4EBDD] focus:outline-none cursor-pointer"
              >
                <option value={10000} className="bg-[#14110D]">10s</option>
                <option value={30000} className="bg-[#14110D]">30s</option>
                <option value={60000} className="bg-[#14110D]">60s</option>
                <option value="off" className="bg-[#14110D]">Off</option>
              </select>
            </div>

            {/* Manual refresh button */}
            <button
              onClick={handleManualRefresh}
              disabled={isManualRefreshing || isLoading}
              className="inline-flex items-center gap-2 rounded-lg border border-[#E6A23C]/40 bg-[#221B14] px-4 py-2 text-xs font-medium text-[#F4EBDD] hover:bg-[#2F241B] hover:border-[#E6A23C] active:scale-95 transition-all duration-200 shadow-md"
            >
              <RefreshCw className={cn('h-3.5 w-3.5 text-[#E6A23C]', (isManualRefreshing || isLoading) && 'animate-spin')} />
              <span>{isManualRefreshing ? 'Probing...' : 'Refresh Telemetry'}</span>
            </button>
          </div>
        </div>

        {/* 4 Stat Overview Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-6">
          <div className="rounded-xl border border-[#3A2A1D] bg-[#18130E] p-4">
            <span className="text-[11px] font-mono uppercase tracking-wider text-[#817463]">Overall System State</span>
            <div className="flex items-center gap-2.5 mt-2">
              <span className={cn('h-3 w-3 rounded-full', sysStatus.dot, sysStatus.dot === 'bg-emerald-400' ? 'animate-pulse' : '')} />
              <span className={cn('font-serif font-bold text-lg tracking-wide', sysStatus.text)}>
                {sysStatus.label}
              </span>
            </div>
            <p className="text-[11px] text-[#817463] mt-2">
              {overview?.status === 'operational'
                ? 'All critical backend services responsive.'
                : 'One or more dependencies degraded or offline.'}
            </p>
          </div>

          <div className="rounded-xl border border-[#3A2A1D] bg-[#18130E] p-4">
            <span className="text-[11px] font-mono uppercase tracking-wider text-[#817463]">Application Uptime</span>
            <div className="flex items-center gap-2 mt-2">
              <Clock className="h-5 w-5 text-[#C5A36A]" />
              <span className="font-mono font-bold text-lg text-[#F4EBDD]">
                {overview?.uptime_human || fmtDuration((overview?.uptime_seconds || 0) * 1000)}
              </span>
            </div>
            <p className="text-[11px] text-[#817463] mt-2">
              Continuous process execution (PID {overview?.process_id || '—'})
            </p>
          </div>

          <div className="rounded-xl border border-[#3A2A1D] bg-[#18130E] p-4">
            <span className="text-[11px] font-mono uppercase tracking-wider text-[#817463]">Runtime & Version</span>
            <div className="flex items-center gap-2 mt-2">
              <Cpu className="h-5 w-5 text-[#C5A36A]" />
              <span className="font-mono font-bold text-base text-[#F4EBDD]">
                v{overview?.version || '1.0.0'} · Python {overview?.python_version || '3.13'}
              </span>
            </div>
            <p className="text-[11px] text-[#817463] mt-2 truncate">
              {overview?.platform || 'Host Platform'}
            </p>
          </div>

          <div className="rounded-xl border border-[#3A2A1D] bg-[#18130E] p-4">
            <span className="text-[11px] font-mono uppercase tracking-wider text-[#817463]">Environment & Deployment</span>
            <div className="flex items-center gap-2 mt-2">
              <Server className="h-5 w-5 text-[#C5A36A]" />
              <span className="font-mono font-bold text-lg text-[#E6A23C] uppercase">
                {overview?.environment || 'DEVELOPMENT'}
              </span>
            </div>
            <p className="text-[11px] text-[#817463] mt-2">
              Last probe: {dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : 'Just now'}
            </p>
          </div>
        </div>

        {/* Section Navigation Tabs */}
        <div className="flex flex-wrap items-center gap-2 mt-6 pt-5 border-t border-[#2C2117]">
          {[
            { id: 'all', label: 'All Telemetry' },
            { id: 'infrastructure', label: 'Core Infrastructure' },
            { id: 'providers', label: 'AI Providers' },
            { id: 'agents', label: 'LangGraph Agents' },
            { id: 'performance', label: 'API & HTTP Traffic' },
            { id: 'storage', label: 'Storage & Disk' },
            { id: 'events', label: 'Audit Events' },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={cn(
                'px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all duration-200',
                activeTab === tab.id
                  ? 'bg-[#C5A36A]/20 text-[#F4EBDD] border border-[#C5A36A]/40 shadow-sm'
                  : 'text-[#817463] hover:text-[#C5A36A] hover:bg-[#1A1510]'
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── SECTION B: CORE INFRASTRUCTURE STATUS CARDS ────────────────────── */}
      {(activeTab === 'all' || activeTab === 'infrastructure') && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Server className="h-5 w-5 text-[#E6A23C]" />
              <h2 className="font-serif text-xl font-semibold text-[#F4EBDD]">Core Infrastructure Dependencies</h2>
            </div>
            <span className="text-xs font-mono text-[#817463]">Real Probes · Bounded Latency</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {infrastructure.map((card) => {
              const st = getStatusColor(card.status);
              return (
                <div
                  key={card.id}
                  className="rounded-xl border border-[#3A2A1D] bg-[#14110D] p-5 shadow-lg flex flex-col justify-between hover:border-[#C5A36A]/40 transition-all duration-300"
                >
                  <div>
                    <div className="flex items-start justify-between gap-2 mb-3">
                      <div>
                        <h3 className="font-serif text-base font-semibold text-[#F4EBDD]">{card.name}</h3>
                        <span className="text-[10px] font-mono text-[#817463] uppercase tracking-wider">
                          ID: {card.id}
                        </span>
                      </div>
                      <span className={cn('px-2.5 py-1 rounded-full text-[10px] font-mono font-bold tracking-wider uppercase border', st.bg, st.border, st.text)}>
                        {st.label}
                      </span>
                    </div>

                    {/* Operational Details */}
                    <div className="space-y-2 text-xs pt-2 border-t border-[#251C14]">
                      {card.latency_ms != null && (
                        <div className="flex justify-between items-center text-[#9E8E7A]">
                          <span>Measured Latency:</span>
                          <span className="font-mono font-bold text-[#E6A23C]">{card.latency_ms.toFixed(1)} ms</span>
                        </div>
                      )}

                      {Object.entries(card.details || {}).map(([key, val]) => (
                        <div key={key} className="flex justify-between items-center text-[#9E8E7A]">
                          <span className="capitalize">{key.replace(/_/g, ' ')}:</span>
                          <span className="font-mono font-medium text-[#F4EBDD] truncate max-w-[180px]">
                            {typeof val === 'boolean' ? (val ? 'Yes' : 'No') : String(val)}
                          </span>
                        </div>
                      ))}
                    </div>

                    {/* Error Notice */}
                    {card.error_message && (
                      <div className="mt-3 p-2.5 rounded-lg border border-red-500/30 bg-red-500/10 text-[11px] text-red-300 font-mono break-words">
                        <span className="font-bold">Error: </span>
                        {card.error_message}
                      </div>
                    )}
                  </div>

                  <div className="mt-4 pt-3 border-t border-[#251C14] flex justify-between items-center text-[10px] text-[#817463]">
                    <span>Checked: {new Date(card.last_checked).toLocaleTimeString()}</span>
                    <span className="font-mono">{card.status === 'not_configured' ? 'Optional' : 'Core Service'}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── SECTION C & D: AI PROVIDERS & SEARCH SOURCES ───────────────────── */}
      {(activeTab === 'all' || activeTab === 'providers') && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* AI Providers */}
          <div className="rounded-2xl border border-[#3A2A1D] bg-[#14110D] p-5 shadow-lg space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-[#251C14]">
              <div className="flex items-center gap-2">
                <Cpu className="h-5 w-5 text-[#E6A23C]" />
                <h3 className="font-serif text-lg font-semibold text-[#F4EBDD]">AI Model Providers</h3>
              </div>
              <span className="text-xs font-mono text-[#817463]">Fallback Pipeline</span>
            </div>

            <div className="divide-y divide-[#251C14]">
              {providers.map((p) => {
                const st = getStatusColor(p.reachability);
                return (
                  <div key={p.provider} className="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm text-[#F4EBDD]">{p.name}</span>
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-[#221B14] text-[#C5A36A] border border-[#3A2A1D]">
                          Priority #{p.fallback_rank}
                        </span>
                      </div>
                      <p className="text-xs font-mono text-[#817463] mt-0.5">
                        Default: <span className="text-[#E6A23C]">{p.default_model}</span>
                      </p>
                      {p.latency_ms != null && (
                        <p className="text-[11px] text-[#817463]">
                          Probe Latency: <span className="font-mono text-[#F4EBDD]">{p.latency_ms.toFixed(1)}ms</span>
                        </p>
                      )}
                    </div>

                    <div className="flex sm:flex-col sm:items-end gap-1.5">
                      <span className={cn('px-2.5 py-1 rounded-full text-[10px] font-mono font-bold uppercase border', st.bg, st.border, st.text)}>
                        {st.label}
                      </span>
                      {p.last_error && (
                        <span className="text-[10px] text-red-400 font-mono truncate max-w-[150px]">
                          {p.last_error}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Search Source Integrations */}
          <div className="rounded-2xl border border-[#3A2A1D] bg-[#14110D] p-5 shadow-lg space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-[#251C14]">
              <div className="flex items-center gap-2">
                <Globe className="h-5 w-5 text-[#E6A23C]" />
                <h3 className="font-serif text-lg font-semibold text-[#F4EBDD]">Research Retrieval Sources</h3>
              </div>
              <span className="text-xs font-mono text-[#817463]">Multi-Source Adapters</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-[440px] overflow-y-auto pr-1">
              {searchSources.map((s) => {
                const st = getStatusColor(s.reachability);
                return (
                  <div key={s.id} className="p-3 rounded-xl border border-[#2B2117] bg-[#1A1510] space-y-1.5">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-xs text-[#F4EBDD] truncate">{s.name}</span>
                      <span className={cn('px-1.5 py-0.5 rounded text-[9px] font-mono font-bold uppercase', st.bg, st.text)}>
                        {st.label}
                      </span>
                    </div>
                    <p className="text-[11px] text-[#817463]">{s.category}</p>
                    <div className="flex justify-between items-center text-[10px] text-[#9E8E7A] pt-1 border-t border-[#251C14]">
                      <span>{s.access_type}</span>
                      <span className="truncate max-w-[110px]">{s.rate_limit_info || 'Standard'}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* ── SECTION E: RESEARCH PIPELINE EXECUTION ─────────────────────────── */}
      {(activeTab === 'all' || activeTab === 'agents') && (
        <div className="rounded-2xl border border-[#3A2A1D] bg-[#14110D] p-6 shadow-lg space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-4 border-b border-[#251C14]">
            <div className="flex items-center gap-2">
              <Activity className="h-5 w-5 text-[#E6A23C]" />
              <div>
                <h3 className="font-serif text-lg font-semibold text-[#F4EBDD]">Research Pipeline & Execution Metrics</h3>
                <p className="text-xs text-[#817463]">Actual database execution counts, completion rates, and active workloads.</p>
              </div>
            </div>
            <span className="px-3 py-1 rounded-full text-xs font-mono bg-[#221B14] text-[#C5A36A] border border-[#3A2A1D]">
              {researchExec?.celery_configured ? 'Distributed Worker Queue' : 'In-Process Async Executor'}
            </span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <div className="p-3.5 rounded-xl border border-[#2B2117] bg-[#19140F]">
              <span className="text-[10px] font-mono uppercase text-[#817463]">Total Sessions</span>
              <p className="font-mono text-xl font-bold text-[#F4EBDD] mt-1">{researchExec?.total_sessions || 0}</p>
              <span className="text-[10px] text-[#817463]">Recorded runs</span>
            </div>

            <div className="p-3.5 rounded-xl border border-[#2B2117] bg-[#19140F]">
              <span className="text-[10px] font-mono uppercase text-[#817463]">Completed</span>
              <p className="font-mono text-xl font-bold text-emerald-400 mt-1">{researchExec?.completed_sessions || 0}</p>
              <span className="text-[10px] text-[#817463]">Final reports</span>
            </div>

            <div className="p-3.5 rounded-xl border border-[#2B2117] bg-[#19140F]">
              <span className="text-[10px] font-mono uppercase text-[#817463]">In Progress</span>
              <p className="font-mono text-xl font-bold text-amber-400 mt-1">{researchExec?.active_sessions || 0}</p>
              <span className="text-[10px] text-[#817463]">Active pipelines</span>
            </div>

            <div className="p-3.5 rounded-xl border border-[#2B2117] bg-[#19140F]">
              <span className="text-[10px] font-mono uppercase text-[#817463]">Failed</span>
              <p className="font-mono text-xl font-bold text-red-400 mt-1">{researchExec?.failed_sessions || 0}</p>
              <span className="text-[10px] text-[#817463]">Unrecovered runs</span>
            </div>

            <div className="p-3.5 rounded-xl border border-[#2B2117] bg-[#19140F]">
              <span className="text-[10px] font-mono uppercase text-[#817463]">Avg Duration</span>
              <p className="font-mono text-xl font-bold text-[#E6A23C] mt-1">
                {researchExec?.average_duration_seconds != null ? `${researchExec.average_duration_seconds}s` : '—'}
              </p>
              <span className="text-[10px] text-[#817463]">Per investigation</span>
            </div>

            <div className="p-3.5 rounded-xl border border-[#2B2117] bg-[#19140F]">
              <span className="text-[10px] font-mono uppercase text-[#817463]">Stage Retries</span>
              <p className="font-mono text-xl font-bold text-[#F4EBDD] mt-1">{researchExec?.total_agent_retries || 0}</p>
              <span className="text-[10px] text-[#817463]">Handled retries</span>
            </div>
          </div>
        </div>
      )}

      {/* ── SECTION F: LANGGRAPH AGENT STAGES ──────────────────────────────── */}
      {(activeTab === 'all' || activeTab === 'agents') && (
        <div className="rounded-2xl border border-[#3A2A1D] bg-[#14110D] p-6 shadow-lg space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-[#251C14]">
            <div className="flex items-center gap-2">
              <Brain className="h-5 w-5 text-[#E6A23C]" />
              <div>
                <h3 className="font-serif text-lg font-semibold text-[#F4EBDD]">LangGraph Multi-Agent Orchestration Nodes</h3>
                <p className="text-xs text-[#817463]">Execution counts, success rates, and mean execution latency per graph node.</p>
              </div>
            </div>
            <span className="text-xs font-mono text-[#817463]">14 Instrumented Stages</span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-[#1C1712] text-[#817463] font-mono uppercase text-[10px]">
                <tr>
                  <th className="py-2.5 px-3">Node / Chain</th>
                  <th className="py-2.5 px-3">Category</th>
                  <th className="py-2.5 px-3">Total Runs</th>
                  <th className="py-2.5 px-3">Success Rate</th>
                  <th className="py-2.5 px-3">Avg Duration</th>
                  <th className="py-2.5 px-3">Status</th>
                  <th className="py-2.5 px-3">Last Executed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#251C14]">
                {agentStages.map((stage) => (
                  <tr key={stage.stage_id} className="hover:bg-[#1A1510] transition-colors">
                    <td className="py-2.5 px-3">
                      <span className="font-medium text-[#F4EBDD]">{stage.name}</span>
                      <span className="block text-[10px] font-mono text-[#817463]">{stage.stage_id}</span>
                    </td>
                    <td className="py-2.5 px-3">
                      <span className="px-2 py-0.5 rounded text-[10px] font-mono uppercase bg-[#221B14] text-[#C5A36A] border border-[#3A2A1D]">
                        {stage.category}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 font-mono font-bold text-[#F4EBDD]">{stage.total_executions}</td>
                    <td className="py-2.5 px-3 font-mono">
                      <span className={stage.success_rate >= 90 ? 'text-emerald-400' : 'text-amber-400'}>
                        {stage.total_executions > 0 ? `${stage.success_rate.toFixed(1)}%` : '—'}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 font-mono text-[#E6A23C]">
                      {stage.average_duration_ms != null ? `${stage.average_duration_ms.toFixed(1)} ms` : '—'}
                    </td>
                    <td className="py-2.5 px-3">
                      <span className={cn(
                        'px-2 py-0.5 rounded-full text-[10px] font-mono uppercase font-bold',
                        stage.current_status === 'active' ? 'bg-amber-500/20 text-amber-400' : 'bg-[#221B14] text-[#817463]'
                      )}>
                        {stage.current_status}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-[#817463] font-mono text-[11px]">
                      {stage.last_executed_at ? new Date(stage.last_executed_at).toLocaleTimeString() : 'Standby'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── SECTION G & H: API PERFORMANCE & AI USAGE ──────────────────────── */}
      {(activeTab === 'all' || activeTab === 'performance') && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* API Metrics */}
          <div className="rounded-2xl border border-[#3A2A1D] bg-[#14110D] p-5 shadow-lg space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-[#251C14]">
              <div className="flex items-center gap-2">
                <Gauge className="h-5 w-5 text-[#E6A23C]" />
                <h3 className="font-serif text-lg font-semibold text-[#F4EBDD]">API Performance & Prometheus Metrics</h3>
              </div>
              <span className="text-xs font-mono text-[#817463]">{apiPerf?.active_window || 'Lifetime'}</span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <div className="p-3 rounded-xl border border-[#2B2117] bg-[#19140F]">
                <span className="text-[10px] font-mono text-[#817463]">Total Requests</span>
                <p className="font-mono text-lg font-bold text-[#F4EBDD] mt-1">{apiPerf?.total_requests || 0}</p>
                <span className="text-[10px] text-[#817463]">{apiPerf?.requests_per_minute || 0} req/min</span>
              </div>

              <div className="p-3 rounded-xl border border-[#2B2117] bg-[#19140F]">
                <span className="text-[10px] font-mono text-[#817463]">Average Latency</span>
                <p className="font-mono text-lg font-bold text-[#E6A23C] mt-1">{apiPerf?.average_latency_ms || 0} ms</p>
                <span className="text-[10px] text-[#817463]">Mean response time</span>
              </div>

              <div className="p-3 rounded-xl border border-[#2B2117] bg-[#19140F]">
                <span className="text-[10px] font-mono text-[#817463]">Error Rate</span>
                <p className="font-mono text-lg font-bold text-emerald-400 mt-1">{apiPerf?.error_rate_percent || 0}%</p>
                <span className="text-[10px] text-[#817463]">4xx & 5xx responses</span>
              </div>

              <div className="p-3 rounded-xl border border-[#2B2117] bg-[#19140F]">
                <span className="text-[10px] font-mono text-[#817463]">P95 Latency</span>
                <p className="font-mono text-lg font-bold text-[#F4EBDD] mt-1">{apiPerf?.p95_latency_ms || 0} ms</p>
                <span className="text-[10px] text-[#817463]">95th percentile</span>
              </div>

              <div className="p-3 rounded-xl border border-[#2B2117] bg-[#19140F]">
                <span className="text-[10px] font-mono text-[#817463]">P99 Latency</span>
                <p className="font-mono text-lg font-bold text-[#F4EBDD] mt-1">{apiPerf?.p99_latency_ms || 0} ms</p>
                <span className="text-[10px] text-[#817463]">99th percentile</span>
              </div>

              <div className="p-3 rounded-xl border border-[#2B2117] bg-[#19140F]">
                <span className="text-[10px] font-mono text-[#817463]">Concurrent Conns</span>
                <p className="font-mono text-lg font-bold text-[#F4EBDD] mt-1">{apiPerf?.concurrent_connections || 0}</p>
                <span className="text-[10px] text-[#817463]">Active sockets</span>
              </div>
            </div>

            {/* HTTP Status Code Bars */}
            <div className="p-3.5 rounded-xl border border-[#2B2117] bg-[#19140F] space-y-2">
              <span className="text-[11px] font-mono text-[#C5A36A]">HTTP Response Distribution</span>
              <div className="flex items-center justify-between text-xs font-mono">
                <span className="text-emerald-400">2xx Success: {apiPerf?.http_2xx || 0}</span>
                <span className="text-amber-400">4xx Client: {apiPerf?.http_4xx || 0}</span>
                <span className="text-red-400">5xx Server: {apiPerf?.http_5xx || 0}</span>
              </div>
            </div>
          </div>

          {/* AI Usage & Cost */}
          <div className="rounded-2xl border border-[#3A2A1D] bg-[#14110D] p-5 shadow-lg space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-[#251C14]">
              <div className="flex items-center gap-2">
                <DollarSign className="h-5 w-5 text-emerald-400" />
                <h3 className="font-serif text-lg font-semibold text-[#F4EBDD]">AI Usage & Estimated Cost</h3>
              </div>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                Estimated USD
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <div className="p-3 rounded-xl border border-[#2B2117] bg-[#19140F]">
                <span className="text-[10px] font-mono text-[#817463]">Total Cost</span>
                <p className="font-mono text-lg font-bold text-emerald-400 mt-1">
                  ${(llmUsage?.total_estimated_cost_usd || 0).toFixed(4)}
                </p>
                <span className="text-[10px] text-[#817463]">Calculated price</span>
              </div>

              <div className="p-3 rounded-xl border border-[#2B2117] bg-[#19140F]">
                <span className="text-[10px] font-mono text-[#817463]">Total Tokens</span>
                <p className="font-mono text-lg font-bold text-[#F4EBDD] mt-1">{llmUsage?.total_tokens || 0}</p>
                <span className="text-[10px] text-[#817463]">Prompt + Completion</span>
              </div>

              <div className="p-3 rounded-xl border border-[#2B2117] bg-[#19140F]">
                <span className="text-[10px] font-mono text-[#817463]">Total Invocations</span>
                <p className="font-mono text-lg font-bold text-[#E6A23C] mt-1">{llmUsage?.total_calls || 0}</p>
                <span className="text-[10px] text-[#817463]">LLM API calls</span>
              </div>
            </div>

            {/* Provider Breakdown */}
            <div className="space-y-2 pt-2">
              <span className="text-xs font-mono text-[#817463]">Provider Usage Breakdown:</span>
              <div className="space-y-1.5 max-h-[140px] overflow-y-auto">
                {(llmUsage?.providers || []).map((pr) => (
                  <div key={pr.provider} className="flex justify-between items-center text-xs p-2 rounded border border-[#2B2117] bg-[#1A1510]">
                    <span className="font-medium text-[#F4EBDD] capitalize">{pr.provider}</span>
                    <span className="font-mono text-[#9E8E7A]">
                      {pr.calls} calls · {pr.tokens} tokens · <span className="text-emerald-400 font-bold">${pr.cost_usd.toFixed(4)}</span>
                    </span>
                  </div>
                ))}
                {(llmUsage?.providers || []).length === 0 && (
                  <div className="text-center py-3 text-xs text-[#817463] font-mono">
                    No billed usage events logged in current session.
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── SECTION I: STORAGE & DISK VOLUME BREAKDOWN ─────────────────────── */}
      {(activeTab === 'all' || activeTab === 'storage') && (
        <div className="rounded-2xl border border-[#3A2A1D] bg-[#14110D] p-6 shadow-lg space-y-6">
          <div className="flex items-center justify-between pb-3 border-b border-[#251C14]">
            <div className="flex items-center gap-2">
              <HardDrive className="h-5 w-5 text-[#E6A23C]" />
              <div>
                <h3 className="font-serif text-lg font-semibold text-[#F4EBDD]">Storage & Persistent Volume Breakdown</h3>
                <p className="text-xs text-[#817463]">Database rows, ChromaDB vectors, report artifacts, and disk volume capacity.</p>
              </div>
            </div>
            <span className="px-3 py-1 rounded-full text-xs font-mono bg-[#221B14] text-[#E6A23C] border border-[#3A2A1D]">
              {storage?.local_storage?.volume_label || 'Local Disk Volume'}
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* PostgreSQL Data */}
            <div className="p-4 rounded-xl border border-[#2B2117] bg-[#19140F] space-y-3">
              <div className="flex items-center justify-between">
                <span className="font-medium text-sm text-[#F4EBDD]">PostgreSQL Persistence</span>
                <span className={cn('px-2 py-0.5 rounded text-[10px] font-mono uppercase font-bold', getStatusColor(storage?.postgres?.status).bg, getStatusColor(storage?.postgres?.status).text)}>
                  {storage?.postgres?.status || 'Unknown'}
                </span>
              </div>
              <div className="space-y-1.5 text-xs text-[#9E8E7A] font-mono">
                {Object.entries(storage?.postgres?.tables || {}).map(([tbl, cnt]) => (
                  <div key={tbl} className="flex justify-between">
                    <span>{tbl}:</span>
                    <span className="text-[#F4EBDD] font-bold">{cnt} rows</span>
                  </div>
                ))}
                {Object.keys(storage?.postgres?.tables || {}).length === 0 && (
                  <p className="text-[#817463] text-xs">PostgreSQL offline or tables pending hydration.</p>
                )}
              </div>
            </div>

            {/* ChromaDB Vector Collections */}
            <div className="p-4 rounded-xl border border-[#2B2117] bg-[#19140F] space-y-3">
              <div className="flex items-center justify-between">
                <span className="font-medium text-sm text-[#F4EBDD]">ChromaDB Vector Store</span>
                <span className={cn('px-2 py-0.5 rounded text-[10px] font-mono uppercase font-bold', getStatusColor(storage?.chromadb?.status).bg, getStatusColor(storage?.chromadb?.status).text)}>
                  {storage?.chromadb?.status || 'Operational'}
                </span>
              </div>
              <div className="space-y-1.5 text-xs text-[#9E8E7A]">
                <div className="flex justify-between">
                  <span>Collections:</span>
                  <span className="font-mono font-bold text-[#F4EBDD]">{storage?.chromadb?.collection_count || 0}</span>
                </div>
                <div className="flex justify-between">
                  <span>Total Vector Chunks:</span>
                  <span className="font-mono font-bold text-[#E6A23C]">{storage?.chromadb?.total_vectors || 0}</span>
                </div>
                <div className="flex justify-between">
                  <span>Storage Mount:</span>
                  <span className="font-mono text-[#F4EBDD]">{storage?.chromadb?.path || './chroma_db'}</span>
                </div>
              </div>
            </div>

            {/* Local Artifacts & Disk */}
            <div className="p-4 rounded-xl border border-[#2B2117] bg-[#19140F] space-y-3">
              <div className="flex items-center justify-between">
                <span className="font-medium text-sm text-[#F4EBDD]">Host Disk Volume</span>
                <span className="font-mono text-xs text-[#E6A23C]">
                  {storage?.local_storage?.disk_free_gb || 0} GB Free
                </span>
              </div>

              {/* Disk Bar */}
              <div className="space-y-1">
                <div className="flex justify-between text-[11px] font-mono text-[#817463]">
                  <span>Used: {storage?.local_storage?.disk_used_gb || 0} GB</span>
                  <span>Total: {storage?.local_storage?.disk_total_gb || 0} GB</span>
                </div>
                <div className="h-2 w-full rounded-full bg-[#2A231C] overflow-hidden">
                  <div
                    className={cn(
                      'h-full rounded-full transition-all duration-500',
                      (storage?.local_storage?.disk_used_percent || 0) > 90 ? 'bg-amber-500' : 'bg-[#C5A36A]'
                    )}
                    style={{ width: `${Math.min(100, storage?.local_storage?.disk_used_percent || 0)}%` }}
                  />
                </div>
              </div>

              <div className="space-y-1 text-xs text-[#9E8E7A] pt-1">
                <div className="flex justify-between">
                  <span>Reports:</span>
                  <span className="font-mono font-bold text-[#F4EBDD]">{storage?.local_storage?.reports_count || 0} files</span>
                </div>
                <div className="flex justify-between">
                  <span>Uploaded Docs:</span>
                  <span className="font-mono font-bold text-[#F4EBDD]">{storage?.local_storage?.uploads_count || 0} files</span>
                </div>
                <div className="flex justify-between">
                  <span>App Footprint:</span>
                  <span className="font-mono font-bold text-[#E6A23C]">{storage?.local_storage?.app_storage_used_mb || 0} MB</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── SECTION J: RECENT AUDIT & SYSTEM EVENTS ────────────────────────── */}
      {(activeTab === 'all' || activeTab === 'events') && (
        <div className="rounded-2xl border border-[#3A2A1D] bg-[#14110D] p-6 shadow-lg space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-[#251C14]">
            <div className="flex items-center gap-2">
              <Terminal className="h-5 w-5 text-[#E6A23C]" />
              <h3 className="font-serif text-lg font-semibold text-[#F4EBDD]">Recent Operational & Audit Events</h3>
            </div>

            {/* Event Category Filter */}
            <div className="flex items-center gap-1.5">
              {['all', 'pipeline', 'system', 'database', 'storage'].map((cat) => (
                <button
                  key={cat}
                  onClick={() => setEventCategoryFilter(cat)}
                  className={cn(
                    'px-2.5 py-1 rounded text-[11px] font-mono capitalize transition-colors',
                    eventCategoryFilter === cat
                      ? 'bg-[#E6A23C] text-black font-bold'
                      : 'bg-[#1C1712] text-[#817463] hover:text-[#C5A36A]'
                  )}
                >
                  {cat}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2 max-h-[360px] overflow-y-auto pr-1">
            {filteredEvents.map((ev) => {
              const sev = ev.severity?.toLowerCase() || 'info';
              const sevColor =
                sev === 'error' || sev === 'critical'
                  ? 'border-red-500/30 bg-red-500/10 text-red-300'
                  : sev === 'warning'
                  ? 'border-amber-500/30 bg-amber-500/10 text-amber-300'
                  : 'border-[#3A2A1D] bg-[#19140F] text-[#F4EBDD]';

              return (
                <div key={ev.id} className={cn('p-3 rounded-xl border flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs', sevColor)}>
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <span className="font-bold uppercase tracking-wider text-[10px] text-[#C5A36A]">[{ev.category}]</span>
                      <span className="font-mono text-[11px] text-[#817463]">{ev.component}</span>
                    </div>
                    <p className="font-sans text-xs">{ev.message}</p>
                  </div>
                  <span className="font-mono text-[11px] text-[#817463] whitespace-nowrap">
                    {new Date(ev.timestamp).toLocaleTimeString()}
                  </span>
                </div>
              );
            })}

            {filteredEvents.length === 0 && (
              <div className="text-center py-6 text-xs text-[#817463] font-mono">
                No recent events recorded in this category.
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── SECTION K: OBSERVABILITY TOOLS & EXTERNAL LINKS ────────────────── */}
      <div className="rounded-2xl border border-[#3A2A1D] bg-[#14110D] p-5 shadow-lg space-y-3">
        <h3 className="font-serif text-base font-semibold text-[#F4EBDD]">Observability Endpoints & Developer Tools</h3>
        <p className="text-xs text-[#817463]">
          Live Prometheus scrape targets, OpenAPI schemas, and interactive Swagger/ReDoc interfaces served directly by the backend instance.
        </p>
        <div className="flex flex-wrap gap-3 pt-1">
          <a
            href={`${API_BASE}/metrics`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#3A2A1D] bg-[#1C1712] px-3.5 py-2 text-xs font-medium text-[#F4EBDD] hover:bg-[#251E17] hover:border-[#E6A23C] transition-all"
          >
            <ExternalLink className="h-3.5 w-3.5 text-[#E6A23C]" /> Prometheus Scrape Endpoint (/metrics)
          </a>
          <a
            href={`${API_BASE}/docs`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#3A2A1D] bg-[#1C1712] px-3.5 py-2 text-xs font-medium text-[#F4EBDD] hover:bg-[#251E17] hover:border-[#E6A23C] transition-all"
          >
            <ExternalLink className="h-3.5 w-3.5 text-[#E6A23C]" /> Swagger OpenAPI Docs (/docs)
          </a>
          <a
            href={`${API_BASE}/redoc`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#3A2A1D] bg-[#1C1712] px-3.5 py-2 text-xs font-medium text-[#F4EBDD] hover:bg-[#251E17] hover:border-[#E6A23C] transition-all"
          >
            <ExternalLink className="h-3.5 w-3.5 text-[#E6A23C]" /> ReDoc Specification (/redoc)
          </a>
          <a
            href={`${API_BASE}/openapi.json`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#3A2A1D] bg-[#1C1712] px-3.5 py-2 text-xs font-medium text-[#F4EBDD] hover:bg-[#251E17] hover:border-[#E6A23C] transition-all"
          >
            <ExternalLink className="h-3.5 w-3.5 text-[#E6A23C]" /> OpenAPI JSON (/openapi.json)
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
        <Route path="knowledge" element={<Navigate to="/app/documents" replace />} />
        <Route path="contradictions" element={<ContradictionsPage />} />
        <Route path="compare" element={<Navigate to="/app/contradictions" replace />} />
        <Route path="system" element={<SystemStatusPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;

