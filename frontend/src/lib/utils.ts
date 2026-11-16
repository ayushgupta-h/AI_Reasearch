/**
 * Research AI Quest — Formatting & DOM Utilities
 */

export function cn(...classes: (string | boolean | undefined | null)[]): string {
  return classes.filter(Boolean).join(' ');
}

export function fmtDate(iso?: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  } catch (_) {
    return iso;
  }
}

export function fmtDuration(ms?: number | null): string {
  if (ms == null) return '—';
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  if (m === 0) return `${s}s`;
  return `${m}m ${s.toString().padStart(2, '0')}s`;
}

export function titleFromQuery(text?: string | null): string {
  if (!text) return 'Research Investigation';
  const clean = text.trim();
  if (clean.toLowerCase() === 'unknown' || clean.toLowerCase() === 'untitled' || clean.toLowerCase() === 'untitled research') {
    return 'Research Investigation';
  }
  if (clean.length <= 64) return clean;
  return clean.slice(0, 60) + '…';
}

