import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import './index.css';

// The extension shows this app in its own small windows (?panel, ?embed=1, /embed-chart): they keep the compact desktop sizing, only the phone layout is switched off for them.
{
  const q = new URLSearchParams(location.search);
  if (q.has('panel') || q.has('embed') || location.pathname.endsWith('/embed-chart')) document.documentElement.classList.add('qx-embed');
}

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/admin">
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
