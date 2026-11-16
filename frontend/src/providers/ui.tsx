/**
 * Research AI Quest — UI Theme & Toast Provider
 */

import React, { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

export type Theme = 'light' | 'dark' | 'system';

interface ToastItem {
  id: string;
  message: string;
  type: 'info' | 'success' | 'error';
}

interface UiContextType {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toast: {
    push: (message: string, type?: 'info' | 'success' | 'error') => void;
  };
}

const UiContext = createContext<UiContextType | undefined>(undefined);

export function UiProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    return (localStorage.getItem('rai-theme') as Theme) || 'system';
  });
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  useEffect(() => {
    localStorage.setItem('rai-theme', theme);
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
    } else if (theme === 'light') {
      root.classList.remove('dark');
    } else {
      const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      root.classList.toggle('dark', isDark);
    }
  }, [theme]);

  const push = (message: string, type: 'info' | 'success' | 'error' = 'info') => {
    const id = Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3800);
  };

  return (
    <UiContext.Provider value={{ theme, setTheme, toast: { push } }}>
      {children}
      {/* Toast rendering container */}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm pointer-events-none">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto rounded-lg px-4 py-2.5 text-[13px] shadow-lg border transition-all transform translate-y-0 opacity-100 ${
              t.type === 'error'
                ? 'bg-red-950 border-red-800 text-red-200'
                : t.type === 'success'
                ? 'bg-emerald-950 border-emerald-800 text-emerald-200'
                : 'bg-zinc-900 border-zinc-700 text-zinc-100'
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>
    </UiContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(UiContext);
  if (!ctx) throw new Error('useTheme must be used within UiProvider');
  return { theme: ctx.theme, setTheme: ctx.setTheme };
}

export function useToast() {
  const ctx = useContext(UiContext);
  if (!ctx) throw new Error('useToast must be used within UiProvider');
  return ctx.toast;
}

