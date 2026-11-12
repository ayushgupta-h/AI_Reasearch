/**
 * Quest — Supabase Google Authentication Module
 * ==============================================
 * Integrates Supabase client, Google OAuth sign-in & logout,
 * session persistence across page reloads, and token attachment for API requests.
 */

(function (window) {
  'use strict';

  const API = window.localStorage.getItem('questApi') || 'http://127.0.0.1:8000';

  const QuestAuth = {
    client: null,
    session: null,
    user: null,
    supabaseUrl: null,
    supabaseAnonKey: null,
    _initPromise: null,
    _listeners: [],
    _ready: false,
    _readyCallbacks: [],

    /**
     * Resolves configuration from window.__ENV__, backend API /auth/config, or localStorage.
     */
    async resolveConfig() {
      // 1. Check window.__ENV__
      let url = window.__ENV__?.SUPABASE_URL || window.__ENV__?.NEXT_PUBLIC_SUPABASE_URL || window.__ENV__?.VITE_SUPABASE_URL;
      let key = window.__ENV__?.SUPABASE_ANON_KEY || window.__ENV__?.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || window.__ENV__?.VITE_SUPABASE_ANON_KEY || window.__ENV__?.SUPABASE_PUBLISHABLE_KEY;

      // Discard stale old project references if present in window.__ENV__
      if (url && url.includes('sldgiglemcswtqxgobpg')) url = null;
      if (key && (key.includes('6X9XiRuSzEe') || key.startsWith('sb_publishable_'))) key = null;

      // 2. Check localStorage overrides
      if (!url) url = localStorage.getItem('quest_supabase_url');
      if (!key) key = localStorage.getItem('quest_supabase_anon_key');

      // Discard and purge stale old project references from browser localStorage
      if (url && url.includes('sldgiglemcswtqxgobpg')) {
        url = null;
        try { localStorage.removeItem('quest_supabase_url'); } catch (_) {}
      }
      if (key && (key.includes('6X9XiRuSzEe') || key.startsWith('sb_publishable_'))) {
        key = null;
        try { localStorage.removeItem('quest_supabase_anon_key'); } catch (_) {}
      }

      // 3. Check backend API config
      if (!url || !key) {
        try {
          const resp = await fetch(API + '/auth/config');
          if (resp.ok) {
            const data = await resp.json();
            if (!url && data.supabaseUrl && !data.supabaseUrl.includes('sldgiglemcswtqxgobpg')) {
              url = data.supabaseUrl;
            }
            if (!key && data.supabaseAnonKey && !data.supabaseAnonKey.includes('6X9XiRuSzEe') && !data.supabaseAnonKey.startsWith('sb_publishable_')) {
              key = data.supabaseAnonKey;
            }
          }
        } catch (_) {
          /* backend might be offline or using different port */
        }
      }

      // 4. Default fallback: verified active Supabase project configuration
      this.supabaseUrl = url || 'https://vxqalflbzpjkvmtbbqrv.supabase.co';
      this.supabaseAnonKey = key || 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZ4cWFsZmxienBqa3ZtdGJicXJ2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc3ODQyNzgsImV4cCI6MjA5MzM2MDI3OH0.Itqr5hTJLCULhkQr_pc6qg0h0dTWkJbb2CRdChCN5h4';

      return { url: this.supabaseUrl, key: this.supabaseAnonKey };
    },

    /**
     * Ensures @supabase/supabase-js is loaded from CDN.
     */
    async loadSupabaseScript() {
      if (window.supabase) return;
      return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2';
        script.onload = () => resolve();
        script.onerror = () => reject(new Error('Failed to load Supabase SDK from CDN'));
        document.head.appendChild(script);
      });
    },

    /**
     * Initializes the Supabase client and restores the active session.
     */
    async init() {
      if (this._initPromise) return this._initPromise;

      this._initPromise = (async () => {
        await this.loadSupabaseScript();
        const { url, key } = await this.resolveConfig();

        if (url && key) {
          try {
            this.client = window.supabase.createClient(url, key, {
              auth: {
                persistSession: true,
                autoRefreshToken: true,
                detectSessionInUrl: false, // Handled explicitly below to avoid race conditions
                storage: window.localStorage,
              },
            });

            // 1. Listen for auth state changes immediately so no events are lost
            this.client.auth.onAuthStateChange((event, session) => {
              console.log('[QuestAuth] Auth state change event:', event, session ? session.user?.email : 'no user');
              this.session = session;
              this.user = session ? session.user : null;
              if (session) {
                this._cleanAuthUrlParams();
              }
              this._notifyListeners(event, session);
              this.updateUI();
              try {
                window.dispatchEvent(
                  new CustomEvent('quest-auth-changed', {
                    detail: { event, session, user: this.user },
                  })
                );
              } catch (_) {}
            });

            // 2. Check for OAuth callback errors in URL
            const urlParams = new URLSearchParams(window.location.search);
            const hashRaw = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : window.location.hash;
            const hashParams = new URLSearchParams(hashRaw);
            const oauthErrDesc = urlParams.get('error_description') || hashParams.get('error_description');
            const oauthErr = urlParams.get('error') || hashParams.get('error');

            if (oauthErrDesc || oauthErr) {
              const msg = oauthErrDesc || oauthErr;
              console.error('[QuestAuth] OAuth error callback:', msg);
              alert('Authentication error from provider: ' + decodeURIComponent(msg.replace(/\+/g, ' ')));
              this._cleanAuthUrlParams();
            }

            // 3. Handle Implicit OAuth tokens if present in hash fragment (#access_token=...)
            const hashAccessToken = hashParams.get('access_token');
            const hashRefreshToken = hashParams.get('refresh_token');
            if (hashAccessToken) {
              const jwtPayload = this._parseJwt(hashAccessToken);
              if (jwtPayload && jwtPayload.sub) {
                const userObj = {
                  id: jwtPayload.sub,
                  email: jwtPayload.email || '',
                  user_metadata: jwtPayload.user_metadata || {},
                  app_metadata: jwtPayload.app_metadata || {},
                  role: jwtPayload.role || 'authenticated',
                };
                this.session = {
                  access_token: hashAccessToken,
                  refresh_token: hashRefreshToken || '',
                  expires_at: jwtPayload.exp,
                  user: userObj,
                };
                this.user = userObj;
                try {
                  localStorage.setItem('quest_active_session', JSON.stringify(this.session));
                } catch (_) {}
                console.log('[QuestAuth] Validated JWT session from OAuth redirect for:', this.user?.email);
                this._cleanAuthUrlParams();
              }

              // Also notify Supabase SDK client in background if initialized
              if (this.client && typeof this.client.auth.setSession === 'function') {
                try {
                  await this.client.auth.setSession({
                    access_token: hashAccessToken,
                    refresh_token: hashRefreshToken || '',
                  });
                } catch (hashEx) {
                  console.info('[QuestAuth] Supabase SDK client setSession background note:', hashEx);
                }
              }
            }

            // 4. Handle PKCE OAuth auth code if present in query parameters (?code=...)
            const authCode = urlParams.get('code');
            if (!this.session && authCode && typeof this.client.auth.exchangeCodeForSession === 'function') {
              try {
                console.log('[QuestAuth] Exchanging PKCE code for session...');
                const { data: exData, error: exErr } = await this.client.auth.exchangeCodeForSession(authCode);
                if (exErr) {
                  console.error('[QuestAuth] exchangeCodeForSession returned error:', exErr);
                  alert('Sign in code exchange failed: ' + (exErr.message || JSON.stringify(exErr)));
                } else if (exData && exData.session) {
                  this.session = exData.session;
                  this.user = exData.session.user;
                  try {
                    localStorage.setItem('quest_active_session', JSON.stringify(this.session));
                  } catch (_) {}
                  console.log('[QuestAuth] PKCE exchange successful for:', this.user?.email);
                  this._cleanAuthUrlParams();
                }
              } catch (ex) {
                console.error('[QuestAuth] PKCE code exchange exception:', ex);
                alert('Sign in exception: ' + (ex.message || ex));
              }
            }

            // 5. Restore active session if not already set from current redirect
            if (!this.session) {
              try {
                const stored = localStorage.getItem('quest_active_session');
                if (stored) {
                  const parsed = JSON.parse(stored);
                  if (parsed && parsed.access_token) {
                    const payload = this._parseJwt(parsed.access_token);
                    // Check token expiration (give 30s buffer)
                    if (payload && payload.exp && payload.exp * 1000 > Date.now() + 30000) {
                      this.session = parsed;
                      this.user = parsed.user;
                    } else {
                      localStorage.removeItem('quest_active_session');
                    }
                  }
                }
              } catch (_) {}
            }
            if (!this.session && this.client) {
              try {
                const { data } = await this.client.auth.getSession();
                if (data && data.session) {
                  this.session = data.session;
                  this.user = data.session.user;
                  this._cleanAuthUrlParams();
                }
              } catch (_) {}
            }
          } catch (err) {
            console.warn('[QuestAuth] Client init failed:', err);
          }
        } else {
          console.info('[QuestAuth] Supabase Anon Key not set yet.');
        }

        this._ready = true;
        while (this._readyCallbacks.length) {
          try {
            const cb = this._readyCallbacks.shift();
            cb(this.session);
          } catch (e) {
            console.error(e);
          }
        }

        // Render UI on page load
        if (document.readyState === 'loading') {
          document.addEventListener('DOMContentLoaded', () => this.updateUI());
        } else {
          this.updateUI();
        }

        try {
          window.dispatchEvent(
            new CustomEvent('quest-auth-ready', {
              detail: { session: this.session, user: this.user },
            })
          );
        } catch (_) {}

        return this;
      })();

      return this._initPromise;
    },

    /**
     * Register a callback to be called as soon as Auth finishes initialising.
     */
    onReady(cb) {
      if (this._ready) {
        cb(this.session);
      } else {
        this._readyCallbacks.push(cb);
      }
    },

    /**
     * Subscribe to auth changes.
     */
    onAuthStateChange(cb) {
      this._listeners.push(cb);
      if (this.session) cb('INITIAL_SESSION', this.session);
    },

    _notifyListeners(event, session) {
      this._listeners.forEach((cb) => {
        try {
          cb(event, session);
        } catch (e) {
          console.error(e);
        }
      });
    },

    /**
     * Removes temporary OAuth parameters (?code=..., #access_token=...) from address bar.
     */
    _cleanAuthUrlParams() {
      try {
        const hasCode = window.location.search && (
          window.location.search.includes('code=') ||
          window.location.search.includes('error=') ||
          window.location.search.includes('error_description=')
        );
        const hasHash = window.location.hash && (
          window.location.hash.includes('access_token=') ||
          window.location.hash.includes('error=') ||
          window.location.hash.includes('error_description=')
        );

        if (hasCode || hasHash) {
          const params = new URLSearchParams(window.location.search);
          params.delete('code');
          params.delete('state');
          params.delete('error');
          params.delete('error_description');
          params.delete('error_code');
          const cleanQuery = params.toString() ? ('?' + params.toString()) : '';
          const cleanUrl = window.location.pathname + cleanQuery;
          window.history.replaceState(null, document.title, cleanUrl);
        }
      } catch (_) {}
    },

    /**
     * Safely decodes a JWT without external libraries.
     */
    _parseJwt(token) {
      if (!token || typeof token !== 'string') return null;
      try {
        const parts = token.split('.');
        if (parts.length < 2) return null;
        const base64Url = parts[1];
        const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
        const jsonPayload = decodeURIComponent(
          atob(base64)
            .split('')
            .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
            .join('')
        );
        return JSON.parse(jsonPayload);
      } catch (_) {
        return null;
      }
    },

    /**
     * Returns true if user is logged in.
     */
    isAuthenticated() {
      return !!(this.session && this.user);
    },

    /**
     * Returns the currently authenticated user object or null.
     */
    getUser() {
      return this.user;
    },

    /**
     * Returns the JWT access token for Authorization header.
     */
    getAccessToken() {
      return this.session?.access_token || '';
    },

    /**
     * Returns standard headers containing Bearer token if logged in.
     */
    getAuthHeaders(baseHeaders = {}) {
      const headers = Object.assign({}, baseHeaders);
      const token = this.getAccessToken();
      if (token) {
        headers['Authorization'] = 'Bearer ' + token;
      }
      return headers;
    },

    /**
     * Initiates Google OAuth Sign-in.
     * Redirects to dashboard.html upon success.
     */
    async signInWithGoogle() {
      if (!this.client) {
        this.showConfigModal();
        return;
      }

      // Check if running on file:// protocol (OAuth redirects cannot return to file://)
      if (window.location.protocol === 'file:') {
        alert(
          'OAuth authentication cannot run directly from a file:// URL.\n\n' +
          'Please launch Quest using start_quest.bat or serve the frontend on http://127.0.0.1:8123 (or http://localhost:8123).'
        );
        return;
      }

      // Determine redirect URL: preserve current page (e.g. dashboard.html, new.html, history.html)
      let redirectUrl = window.location.origin + '/dashboard.html';
      if (window.location.pathname && (
        window.location.pathname.endsWith('.html') ||
        window.location.pathname.length > 1
      )) {
        redirectUrl = window.location.origin + window.location.pathname;
      }

      try {
        const { error } = await this.client.auth.signInWithOAuth({
          provider: 'google',
          options: {
            redirectTo: redirectUrl,
            queryParams: {
              access_type: 'offline',
              prompt: 'consent',
            },
          },
        });
        if (error) throw error;
      } catch (err) {
        alert('Google Sign-In failed: ' + (err.message || err));
      }
    },

    /**
     * Signs out the user and reloads page state.
     */
    async signOut() {
      if (this.client) {
        try {
          await this.client.auth.signOut();
        } catch (_) {}
      }
      this.session = null;
      this.user = null;
      window.localStorage.removeItem('quest_user');
      window.localStorage.removeItem('quest_active_session');
      this.updateUI();
      window.location.reload();
    },

    /**
     * Guards private functionality. If logged out, shows login prompt.
     */
    requireAuth(actionCallback, promptMsg) {
      if (this.isAuthenticated()) {
        if (typeof actionCallback === 'function') actionCallback();
        return true;
      }
      this.showLoginModal(promptMsg || 'Sign in with Google to access this feature.');
      return false;
    },

    /**
     * Updates topbar / navigation auth UI components across all pages.
     */
    updateUI() {
      this._injectStyles();

      // Topbar injection target
      const topbar = document.querySelector('.tb-right') || document.querySelector('.topbar') || document.querySelector('.nr-top');
      if (topbar) {
        let authSlot = topbar.querySelector('.quest-auth-slot');
        if (!authSlot) {
          authSlot = document.createElement('div');
          authSlot.className = 'quest-auth-slot';
          topbar.appendChild(authSlot);
        }

        if (this.isAuthenticated()) {
          const userMeta = this.user.user_metadata || {};
          const name = userMeta.full_name || userMeta.name || this.user.email.split('@')[0];
          const avatar = userMeta.avatar_url || userMeta.picture;
          const initial = (name || 'U').charAt(0).toUpperCase();

          authSlot.innerHTML = `
            <div class="q-auth-user">
              ${
                avatar
                  ? `<img class="q-auth-avatar" src="${avatar}" alt="${name}" referrerpolicy="no-referrer" />`
                  : `<div class="q-auth-avatar q-auth-initial">${initial}</div>`
              }
              <span class="q-auth-name">${name}</span>
              <button class="q-auth-btn q-auth-signout" type="button" title="Sign out of Quest">Sign out</button>
            </div>
          `;

          const outBtn = authSlot.querySelector('.q-auth-signout');
          if (outBtn) outBtn.onclick = () => this.signOut();
        } else {
          authSlot.innerHTML = `
            <button class="q-auth-btn q-auth-signin" type="button" title="Sign in with Google">
              <svg viewBox="0 0 18 18" width="16" height="16" aria-hidden="true">
                <path fill="#4285F4" d="M17.64 9.2c0-.63-.06-1.25-.16-1.84H9v3.49h4.84a4.14 4.14 0 0 1-1.8 2.71v2.26h2.92c1.71-1.57 2.68-3.89 2.68-6.62z"/>
                <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.91-2.26c-.8.54-1.83.86-3.05.86-2.34 0-4.33-1.58-5.04-3.71H.96v2.33C2.44 15.86 5.48 18 9 18z"/>
                <path fill="#FBBC05" d="M3.96 10.71c-.18-.54-.28-1.12-.28-1.71s.1-1.17.28-1.71V4.96H.96A8.996 8.996 0 0 0 0 9c0 1.45.35 2.82.96 4.04l3-2.33z"/>
                <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.59C13.46.89 11.43 0 9 0 5.48 0 2.44 2.14.96 5.04l3 2.33c.71-2.13 2.7-3.79 5.04-3.79z"/>
              </svg>
              <span>Sign in with Google</span>
            </button>
          `;

          const inBtn = authSlot.querySelector('.q-auth-signin');
          if (inBtn) inBtn.onclick = () => this.signInWithGoogle();
        }
      }

      // Check if page has private CTA buttons (e.g. Start New Research)
      const newResearchLinks = document.querySelectorAll('a[href="new.html"], .hero-cta');
      newResearchLinks.forEach((el) => {
        if (!el.__authProtected) {
          el.__authProtected = true;
          el.addEventListener('click', (e) => {
            if (!this.isAuthenticated()) {
              e.preventDefault();
              this.showLoginModal('Sign in with Google to start research and manage your private workspace.');
            }
          });
        }
      });
    },

    /**
     * Modal dialog prompting for Google login.
     */
    showLoginModal(message) {
      let modal = document.getElementById('questAuthModal');
      if (!modal) {
        modal = document.createElement('div');
        modal.id = 'questAuthModal';
        modal.className = 'q-modal-overlay';
        modal.innerHTML = `
          <div class="q-modal-card">
            <button class="q-modal-close" aria-label="Close modal">&times;</button>
            <div class="q-modal-icon">
              <svg viewBox="0 0 24 24" width="36" height="36" fill="none">
                <path d="M12 2.2 20 5v6.1c0 4.9-3.3 9-8 10.7-4.7-1.7-8-5.8-8-10.7V5l8-2.8Z" stroke="#e9c99b" stroke-width="1.3"/>
                <path d="m8.4 12 2.5 2.5 4.9-4.9" stroke="#e9c99b" stroke-width="1.3"/>
              </svg>
            </div>
            <h2 class="q-modal-title">Sign in to Quest</h2>
            <p class="q-modal-sub" id="qModalMsg"></p>
            <button class="q-auth-btn q-modal-google-btn" id="qModalGoogleBtn" type="button">
              <svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true">
                <path fill="#4285F4" d="M17.64 9.2c0-.63-.06-1.25-.16-1.84H9v3.49h4.84a4.14 4.14 0 0 1-1.8 2.71v2.26h2.92c1.71-1.57 2.68-3.89 2.68-6.62z"/>
                <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.91-2.26c-.8.54-1.83.86-3.05.86-2.34 0-4.33-1.58-5.04-3.71H.96v2.33C2.44 15.86 5.48 18 9 18z"/>
                <path fill="#FBBC05" d="M3.96 10.71c-.18-.54-.28-1.12-.28-1.71s.1-1.17.28-1.71V4.96H.96A8.996 8.996 0 0 0 0 9c0 1.45.35 2.82.96 4.04l3-2.33z"/>
                <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.59C13.46.89 11.43 0 9 0 5.48 0 2.44 2.14.96 5.04l3 2.33c.71-2.13 2.7-3.79 5.04-3.79z"/>
              </svg>
              <span>Continue with Google</span>
            </button>
            <p class="q-modal-note">Authenticated sessions grant private access to your research reports and queries.</p>
          </div>
        `;
        document.body.appendChild(modal);

        modal.querySelector('.q-modal-close').onclick = () => (modal.style.display = 'none');
        modal.onclick = (e) => {
          if (e.target === modal) modal.style.display = 'none';
        };
        modal.querySelector('#qModalGoogleBtn').onclick = () => {
          modal.style.display = 'none';
          this.signInWithGoogle();
        };
      }

      document.getElementById('qModalMsg').textContent =
        message || 'Sign in with Google to protect your research and access your private dashboard.';
      modal.style.display = 'flex';
    },

    /**
     * Modal dialog to configure Supabase Anon Key if missing.
     */
    showConfigModal() {
      let modal = document.getElementById('questConfigModal');
      if (!modal) {
        modal = document.createElement('div');
        modal.id = 'questConfigModal';
        modal.className = 'q-modal-overlay';
        modal.innerHTML = `
          <div class="q-modal-card">
            <button class="q-modal-close" aria-label="Close modal">&times;</button>
            <h2 class="q-modal-title">Supabase Publishable Key</h2>
            <p class="q-modal-sub">Enter your Supabase Anon / Publishable key to activate Google Authentication.</p>
            <input class="q-modal-input" id="qAnonKeyInput" type="text" placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6..." />
            <button class="q-auth-btn q-modal-save-btn" id="qSaveKeyBtn" type="button">Save \u0026 Initialize</button>
            <p class="q-modal-note">Project URL: ${this.supabaseUrl}<br>This key is public and saved in your browser localStorage.</p>
          </div>
        `;
        document.body.appendChild(modal);

        modal.querySelector('.q-modal-close').onclick = () => (modal.style.display = 'none');
        modal.querySelector('#qSaveKeyBtn').onclick = () => {
          const val = modal.querySelector('#qAnonKeyInput').value.trim();
          if (val) {
            localStorage.setItem('quest_supabase_anon_key', val);
            this.supabaseAnonKey = val;
            modal.style.display = 'none';
            this._initPromise = null;
            this.init().then(() => this.signInWithGoogle());
          }
        };
      }
      modal.style.display = 'flex';
    },

    _injectStyles() {
      if (document.getElementById('questAuthStyles')) return;
      const style = document.createElement('style');
      style.id = 'questAuthStyles';
      style.textContent = `
        .quest-auth-slot {
          margin-left: auto;
          display: flex;
          align-items: center;
          gap: 12px;
          z-index: 105;
        }
        .q-auth-btn {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 8px 16px;
          border-radius: 9999px;
          font-family: var(--sans, 'Outfit', sans-serif);
          font-size: 13px;
          font-weight: 400;
          cursor: pointer;
          transition: all .25s cubic-bezier(.22,1,.36,1);
          text-decoration: none;
        }
        .q-auth-signin {
          background: rgba(255,255,255,.05);
          border: 1px solid rgba(233,201,155,.45);
          color: var(--ink, #f4efe8);
        }
        .q-auth-signin:hover {
          background: rgba(233,201,155,.15);
          border-color: var(--amber, #e9c99b);
          transform: translateY(-1px);
        }
        .q-auth-user {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 4px 6px 4px 4px;
          border-radius: 9999px;
          background: rgba(255,255,255,.03);
          border: 1px solid rgba(255,255,255,.1);
        }
        .q-auth-avatar {
          width: 28px;
          height: 28px;
          border-radius: 50%;
          object-fit: cover;
          border: 1px solid rgba(233,201,155,.5);
        }
        .q-auth-initial {
          display: flex;
          align-items: center;
          justify-content: center;
          background: var(--amber, #e9c99b);
          color: #0b0906;
          font-weight: 500;
          font-size: 13px;
        }
        .q-auth-name {
          font-size: 12.5px;
          color: var(--ink, #f4efe8);
          max-width: 140px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font-weight: 400;
        }
        .q-auth-signout {
          padding: 4px 10px;
          font-size: 11px;
          background: transparent;
          border: 1px solid rgba(255,255,255,.15);
          color: rgba(244,239,232,.6);
          border-radius: 9999px;
        }
        .q-auth-signout:hover {
          background: rgba(255,80,80,.15);
          border-color: rgba(255,80,80,.4);
          color: #ff9999;
        }
        .q-modal-overlay {
          position: fixed;
          inset: 0;
          z-index: 1000;
          display: none;
          align-items: center;
          justify-content: center;
          background: rgba(5,5,5,.82);
          backdrop-filter: blur(16px);
          -webkit-backdrop-filter: blur(16px);
        }
        .q-modal-card {
          position: relative;
          background: #0d0d12;
          border: 1px solid rgba(233,201,155,.3);
          border-radius: 12px;
          padding: 36px 32px;
          max-width: 440px;
          width: 90%;
          text-align: center;
          box-shadow: 0 24px 64px rgba(0,0,0,.7);
          animation: qModalIn .3s cubic-bezier(.22,1,.36,1);
        }
        @keyframes qModalIn {
          from { opacity: 0; transform: scale(.94) translateY(12px); }
          to { opacity: 1; transform: none; }
        }
        .q-modal-close {
          position: absolute;
          top: 14px;
          right: 18px;
          font-size: 24px;
          color: rgba(255,255,255,.4);
          background: none;
          border: none;
          cursor: pointer;
        }
        .q-modal-close:hover { color: #fff; }
        .q-modal-icon { margin-bottom: 16px; }
        .q-modal-title {
          font-family: var(--serif, Georgia, serif);
          font-size: 24px;
          font-weight: 400;
          color: var(--ink, #f4efe8);
          margin-bottom: 8px;
        }
        .q-modal-sub {
          font-size: 13.5px;
          color: rgba(244,239,232,.65);
          margin-bottom: 24px;
          line-height: 1.5;
        }
        .q-modal-google-btn {
          width: 100%;
          justify-content: center;
          padding: 12px 20px;
          font-size: 14px;
          background: #171720;
          border: 1px solid rgba(233,201,155,.5);
          color: #f4efe8;
          border-radius: 8px;
          box-shadow: 0 4px 16px rgba(0,0,0,.3);
        }
        .q-modal-google-btn:hover {
          background: #20202e;
          border-color: var(--amber, #e9c99b);
          transform: translateY(-2px);
        }
        .q-modal-input {
          width: 100%;
          background: #14141c;
          border: 1px solid rgba(233,201,155,.3);
          border-radius: 6px;
          padding: 10px 14px;
          color: #f4efe8;
          font-family: monospace;
          font-size: 12px;
          margin-bottom: 16px;
        }
        .q-modal-save-btn {
          width: 100%;
          justify-content: center;
          padding: 10px 20px;
          background: var(--amber, #e9c99b);
          color: #0b0906;
          font-weight: 500;
          border-radius: 6px;
        }
        .q-modal-note {
          margin-top: 18px;
          font-size: 11px;
          color: rgba(244,239,232,.38);
          line-height: 1.4;
        }
      `;
      document.head.appendChild(style);
    },
  };

  window.QuestAuth = QuestAuth;
  QuestAuth.init();
})(window);
