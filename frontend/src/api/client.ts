/**
 * Research AI Quest — API Client
 * Automatic bearer token injection, error mapping, and stream URL resolution.
 */

import { API_BASE } from './endpoints';
import type { ApiErrorPayload } from './types';

export class ApiError extends Error {
  status: number;
  error_code?: string;
  request_id?: string;
  details?: Record<string, unknown> | null;

  constructor(payload: ApiErrorPayload, status: number) {
    super(payload.detail || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.error_code = payload.error_code;
    this.request_id = payload.request_id;
    this.details = payload.details;
  }
}

export function getAccessToken(): string | null {
  if (typeof window === 'undefined') return null;

  // 1. Check window.QuestAuth
  const questAuth = (window as unknown as { QuestAuth?: { getAccessToken?: () => string | null } }).QuestAuth;
  if (questAuth?.getAccessToken) {
    const t = questAuth.getAccessToken();
    if (t) return t;
  }

  // 2. Check localStorage quest_active_session
  try {
    const raw = localStorage.getItem('quest_active_session');
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed?.access_token) return parsed.access_token;
    }
  } catch (_) {}

  // 3. Check Supabase auth token keys
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key && (key.startsWith('sb-') || key.includes('supabase.auth.token')) && key.endsWith('-auth-token')) {
      try {
        const item = JSON.parse(localStorage.getItem(key) || '{}');
        const token = item?.access_token || item?.currentSession?.access_token;
        if (token) return token;
      } catch (_) {}
    }
  }

  return null;
}

export async function request<T = unknown>(
  path: string,
  init: RequestInit = {}
): Promise<{ data: T; status: number; headers: Headers }> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
  const headers = new Headers(init.headers || {});

  const token = getAccessToken();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  // Auto set Content-Type unless multipart FormData
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const res = await fetch(url, { ...init, headers });

  if (!res.ok) {
    let errPayload: ApiErrorPayload;
    try {
      errPayload = await res.json();
    } catch (_) {
      errPayload = { detail: res.statusText || `Request failed with status ${res.status}` };
    }
    throw new ApiError(errPayload, res.status);
  }

  // Handle empty bodies (204 No Content, DELETE, etc.)
  if (res.status === 204) {
    return { data: null as unknown as T, status: res.status, headers: res.headers };
  }

  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    const json = await res.json();
    return { data: json, status: res.status, headers: res.headers };
  }

  const text = await res.text();
  return { data: text as unknown as T, status: res.status, headers: res.headers };
}

export const api = {
  get: <T = unknown>(path: string, init?: RequestInit) => request<T>(path, { ...init, method: 'GET' }),
  post: <T = unknown>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>(path, {
      ...init,
      method: 'POST',
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    }),
  put: <T = unknown>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>(path, {
      ...init,
      method: 'PUT',
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    }),
  patch: <T = unknown>(path: string, body?: unknown, init?: RequestInit) =>
    request<T>(path, {
      ...init,
      method: 'PATCH',
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    }),
  delete: <T = unknown>(path: string, init?: RequestInit) => request<T>(path, { ...init, method: 'DELETE' }),
};

export function apiError(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  if (typeof e === 'string') return e;
  return 'An unexpected error occurred.';
}

export function streamUrl(sessionId: string): string {
  return `${API_BASE}/research/${sessionId}/events`;
}

export async function downloadExport(sessionId: string, format: 'markdown' | 'pdf' | 'docx'): Promise<void> {
  const token = getAccessToken();
  const headers = new Headers();
  if (token) headers.set('Authorization', `Bearer ${token}`);

  // Resolve the report id for this research session first.
  // The export endpoint expects a report_id (hex32), not a research session id.
  let reportId: string | null = null;
  try {
    const repRes = await fetch(`${API_BASE}/research/${sessionId}/reports`, {
      method: 'GET',
      headers: { ...Object.fromEntries(headers.entries()), 'Content-Type': 'application/json' },
    });
    if (repRes.ok) {
      const repJson = await repRes.json();
      const reports = repJson?.reports ?? repJson ?? [];
      const first = Array.isArray(reports) ? reports[0] : reports;
      reportId = first?.id ?? first?.report_id ?? null;
    }
  } catch (_) {
    // ignore lookup errors; fall back below
  }

  const res = await fetch(`${API_BASE}/reports/${reportId ?? sessionId}/export`, {
    method: 'POST',
    headers: {
      ...Object.fromEntries(headers.entries()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ format: format === 'markdown' ? 'md' : format }),
  });

  if (!res.ok) {
    throw new Error(`Export download failed (${res.status}): ${res.statusText}`);
  }

  const blob = await res.blob();
  const disposition = res.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : `research-${sessionId}.${format === 'markdown' ? 'md' : format}`;

  const downloadUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = downloadUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(downloadUrl);
}

