/**
 * Research AI Quest — Server-Sent Events (SSE) Stream Subscriber
 */

import { API_BASE } from '../api/endpoints';

export interface SSEMessage {
  id?: string;
  event: string;
  data: Record<string, unknown>;
}

export interface StreamSSEOptions {
  method?: 'GET' | 'POST';
  body?: unknown;
  token?: string | null;
  lastEventId?: string | null;
  signal?: AbortSignal;
  onMessage: (msg: SSEMessage) => void;
}

export async function streamSSE(url: string, options: StreamSSEOptions): Promise<void> {
  const { token, lastEventId, signal, onMessage, method = 'GET', body } = options;

  let streamEndpoint = url.startsWith('http') ? url : `${API_BASE}${url.startsWith('/') ? '' : '/'}${url}`;
  const params = new URLSearchParams();
  if (token) params.set('token', token);
  if (lastEventId) params.set('last_event_id', lastEventId);
  const qs = params.toString();
  if (qs) {
    streamEndpoint += (streamEndpoint.includes('?') ? '&' : '?') + qs;
  }

  const headers = new Headers();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (lastEventId) headers.set('Last-Event-ID', lastEventId);
  headers.set('Accept', 'text/event-stream');
  if (body) headers.set('Content-Type', 'application/json');

  const res = await fetch(streamEndpoint, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });

  if (!res.ok) {
    throw new Error(`SSE stream connection failed (${res.status}): ${res.statusText}`);
  }

  const reader = res.body?.getReader();
  if (!reader) throw new Error('Response body stream is not readable.');

  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    if (signal?.aborted) {
      await reader.cancel();
      break;
    }

    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n\n');
    buffer = lines.pop() || '';

    for (const block of lines) {
      if (!block.trim() || block.startsWith(':')) continue; // skip keep-alive comments

      let eventType = 'message';
      let dataText = '';
      let eventId: string | undefined;

      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          dataText += (dataText ? '\n' : '') + line.slice(5).trim();
        } else if (line.startsWith('id:')) {
          eventId = line.slice(3).trim();
        }
      }

      if (dataText) {
        let parsedData: Record<string, unknown> = {};
        try {
          parsedData = JSON.parse(dataText);
        } catch (_) {
          parsedData = { raw: dataText };
        }

        onMessage({
          id: eventId,
          event: eventType,
          data: parsedData,
        });
      }
    }
  }
}

