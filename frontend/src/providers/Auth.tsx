/**
 * Research AI Quest — Authentication Provider (Supabase + Google OAuth)
 */

import React, { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import type { Session, User } from '@supabase/supabase-js';
import { supabase, supabaseConfigured } from '../lib/supabase';

interface AuthContextType {
  user: User | null;
  session: Session | null;
  loading: boolean;
  signIn: (email: string, pass: string) => Promise<{ error: Error | null }>;
  signUp: (email: string, pass: string) => Promise<{ error: Error | null }>;
  signInWithGoogle: () => Promise<{ error: Error | null }>;
  signOut: () => Promise<void>;
  resetPassword: (email: string) => Promise<{ error: Error | null }>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!supabaseConfigured) {
      setLoading(false);
      return;
    }

    // Handle OAuth hash tokens if directly landed on index.html
    if (typeof window !== 'undefined' && window.location.hash.includes('access_token')) {
      try {
        const hashStr = window.location.hash.replace(/^#\/?/, '');
        const params = new URLSearchParams(hashStr);
        const at = params.get('access_token');
        const rt = params.get('refresh_token');
        if (at) {
          localStorage.setItem('quest_access_token', at);
          supabase.auth.setSession({
            access_token: at,
            refresh_token: rt || '',
          }).then(({ data }) => {
            if (data?.session) {
              setSession(data.session);
              setUser(data.session.user);
              try {
                localStorage.setItem('quest_active_session', JSON.stringify(data.session));
              } catch (_) {}
            }
            window.location.hash = '#/app/new';
            setLoading(false);
          });
        }
      } catch (_) {}
    }

    // 1. Initial session load
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setUser(data.session?.user || null);
      if (data.session) {
        try {
          localStorage.setItem('quest_active_session', JSON.stringify(data.session));
        } catch (_) {}
      }
      setLoading(false);
    });

    // 2. Auth state change listener
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, currentSession) => {
      setSession(currentSession);
      setUser(currentSession?.user || null);
      if (currentSession) {
        try {
          localStorage.setItem('quest_active_session', JSON.stringify(currentSession));
        } catch (_) {}
      } else {
        try {
          localStorage.removeItem('quest_active_session');
        } catch (_) {}
      }
      setLoading(false);
    });

    return () => subscription.unsubscribe();
  }, []);

  const signIn = async (email: string, pass: string) => {
    const { data, error } = await supabase.auth.signInWithPassword({
      email,
      password: pass,
    });
    if (!error && data.session) {
      setSession(data.session);
      setUser(data.session.user);
    }
    return { error: error ? new Error(error.message) : null };
  };

  const signUp = async (email: string, pass: string) => {
    const { data, error } = await supabase.auth.signUp({
      email,
      password: pass,
    });
    if (!error && data.session) {
      setSession(data.session);
      setUser(data.session.user);
    }
    return { error: error ? new Error(error.message) : null };
  };

  const signInWithGoogle = async () => {
    // Preserve verified Google Cloud & Supabase OAuth redirect callback
    const redirectUrl =
      typeof window !== 'undefined'
        ? window.location.origin + '/dashboard.html'
        : 'http://127.0.0.1:8123/dashboard.html';

    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: redirectUrl,
        queryParams: {
          access_type: 'offline',
          prompt: 'consent',
        },
      },
    });
    return { error: error ? new Error(error.message) : null };
  };

  const signOut = async () => {
    try {
      await supabase.auth.signOut();
    } catch (_) {}
    setSession(null);
    setUser(null);
    try {
      localStorage.removeItem('quest_active_session');
      localStorage.removeItem('quest_access_token');
      for (let i = localStorage.length - 1; i >= 0; i--) {
        const key = localStorage.key(i);
        if (key && (key.startsWith('sb-') || key.includes('supabase.auth.token'))) {
          localStorage.removeItem(key);
        }
      }
    } catch (_) {}
  };

  const resetPassword = async (email: string) => {
    const { error } = await supabase.auth.resetPasswordForEmail(email);
    return { error: error ? new Error(error.message) : null };
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        session,
        loading,
        signIn,
        signUp,
        signInWithGoogle,
        signOut,
        resetPassword,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
