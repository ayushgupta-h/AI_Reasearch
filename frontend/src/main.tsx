/**
 * Research AI Quest — Frontend Entry Point
 * Mounts React 18 application with QueryClient, AuthProvider, UiProvider, and HashRouter.
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import './index.css';
import { queryClient } from './pages/index';
import { AuthProvider } from './providers/Auth';
import { UiProvider } from './providers/ui';
import App from './pages/index';

const rootElement = document.getElementById('root');
if (rootElement) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <UiProvider>
            <HashRouter>
              <App />
            </HashRouter>
          </UiProvider>
        </AuthProvider>
      </QueryClientProvider>
    </React.StrictMode>
  );
}
