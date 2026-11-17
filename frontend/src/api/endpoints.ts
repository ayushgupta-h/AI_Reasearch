/**
 * Research AI Quest — API Endpoints
 * Synchronized with the FastAPI backend routers.
 */

const winEnv = (typeof window !== 'undefined' ? (window as unknown as { __ENV__?: Record<string, string> }).__ENV__ : null) || {};

export const API_BASE = (
  (typeof window !== 'undefined' ? localStorage.getItem('questApi') : null) ||
  winEnv.API_BASE ||
  winEnv.VITE_API_URL ||
  winEnv.VITE_API_BASE ||
  (import.meta.env.VITE_API_URL as string | undefined) ||
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  (typeof window !== 'undefined' && (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
    ? 'http://127.0.0.1:8000'
    : 'https://quest-kcgz.onrender.com')
).replace(/\/$/, '');

export const API = {
  auth: {
    config: '/auth/config',
    me: '/auth/me',
  },
  research: {
    create: '/research',
    events: (id: string) => `/research/${id}/events`,
    session: (id: string) => `/research/${id}`,
    sessions: '/sessions',
    updateSession: (id: string) => `/research/${id}`,
    deleteSession: (id: string) => `/sessions/${id}`,
    progress: (id: string) => `/research/${id}/progress`,
    timeline: (id: string) => `/research/${id}/timeline`,
    agents: (id: string) => `/research/${id}/agents`,
    agentExecution: (id: string, executionId: string) => `/research/${id}/agents/${executionId}`,
    generateReport: (id: string) => `/research/${id}/report/generate`,
  },
  chat: {
    send: '/chat',
  },
  projects: {
    list: '/projects',
    create: '/projects',
    get: (id: string) => `/projects/${id}`,
    update: (id: string) => `/projects/${id}`,
    delete: (id: string) => `/projects/${id}`,
    research: (id: string) => `/projects/${id}/research`,
  },
  documents: {
    upload: '/documents/upload',
    list: '/documents',
    get: (id: string) => `/documents/${id}`,
    download: (id: string) => `/documents/${id}/download`,
    process: (id: string) => `/documents/${id}/process`,
    status: (id: string) => `/documents/${id}/status`,
    delete: (id: string) => `/documents/${id}`,
  },
  sources: {
    bySession: (id: string) => `/research/${id}/sources`,
    statistics: (id: string) => `/research/${id}/sources/statistics`,
    get: (id: string) => `/sources/${id}`,
    updateStatus: (id: string) => `/sources/${id}/status`,
    delete: (id: string) => `/sources/${id}`,
  },
  evidence: {
    bySession: (id: string) => `/research/${id}/claims`,
    get: (researchId: string, claimId: string) => `/research/${researchId}/claims/${claimId}`,
  },
  contradictions: {
    bySession: (id: string) => `/research/${id}/contradictions`,
  },
  reports: {
    bySession: (id: string) => `/research/${id}/reports`,
    get: (id: string) => `/reports/${id}`,
    update: (id: string) => `/reports/${id}/versions`,
    versions: (id: string) => `/reports/${id}/versions`,
    version: (id: string, versionId: string) => `/reports/${id}/versions/${versionId}`,
    restore: (id: string, versionId: string | number) => `/reports/${id}/versions`,
    export: (id: string) => `/reports/${id}/export`,
    download: (filename: string) => `/reports/${filename}`,
    share: (filename: string) => `/reports/${filename}/share`,
    assist: (id: string) => `/reports/${id}/assist`,
  },
  review: {
    plan: (id: string) => `/research/${id}/plan`,
    decide: (id: string) => `/research/${id}/plan/decision`,
    approvePlan: (id: string) => `/research/${id}/plan/approve`,
    rejectPlan: (id: string) => `/research/${id}/plan/reject`,
    requestChanges: (id: string) => `/research/${id}/plan/request-changes`,
    planComments: (id: string) => `/research/${id}/plan/comments`,
    reviews: (id: string) => `/research/${id}/reviews`,
    reportReviews: (id: string) => `/reports/${id}/reviews`,
    reportDecision: (id: string) => `/reports/${id}/reviews/decision`,
    reportRevise: (id: string) => `/reports/${id}/reviews/revise`,
  },
  memory: {
    search: '/memory/search',
    stats: '/memory/stats',
    list: '/memory',
    create: '/memory',
    get: (id: string) => `/memory/${id}`,
    update: (id: string) => `/memory/${id}`,
    delete: (id: string) => `/memory/${id}`,
  },
  knowledge: {
    search: '/knowledge/search',
    stats: '/knowledge/stats',
    list: '/knowledge',
    create: '/knowledge',
    get: (id: string) => `/knowledge/${id}`,
    update: (id: string) => `/knowledge/${id}`,
    delete: (id: string) => `/knowledge/${id}`,
  },
  completeness: {
    evaluate: (id: string) => `/research/${id}/completeness/evaluate`,
    objectives: (id: string) => `/research/${id}/completeness/objectives`,
    evaluations: (id: string) => `/research/${id}/completeness/evaluations`,
    latestEvaluation: (id: string) => `/research/${id}/completeness/evaluations/latest`,
  },
  notifications: {
    list: '/notifications',
    unread: '/notifications/unread',
    markRead: (id: string) => `/notifications/${id}/read`,
    readAll: '/notifications/read-all',
    delete: (id: string) => `/notifications/${id}`,
  },
  usage: {
    summary: '/usage/summary',
    byResearch: (id: string) => `/usage/research/${id}`,
    providers: '/usage/providers',
    models: '/usage/models',
  },
  system: {
    health: '/health',
    live: '/health/live',
    ready: '/health/ready',
    dependencies: '/health/dependencies',
    providers: '/providers',
    metrics: '/metrics',
  },
};

