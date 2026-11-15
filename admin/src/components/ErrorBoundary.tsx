import { Component, ReactNode } from 'react';

/** A rendering bug on one page must show a readable message, never a blank screen. Remount it per route (key) so navigating away recovers. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error) { console.error('page crashed:', error); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="rounded-2xl border border-down/40 bg-down/10 p-6">
        <div className="text-lg font-bold text-down">This page hit an error</div>
        <p className="mt-1 text-sm text-mut">The rest of the app is fine. Go back or pick another page; if it keeps happening, send this text:</p>
        <pre className="mt-3 overflow-x-auto rounded-lg bg-bg p-3 text-xs text-fg">{this.state.error.message}</pre>
        <div className="mt-3 flex gap-2">
          <button className="btn" onClick={() => this.setState({ error: null })}>Try again</button>
          <button className="btn" onClick={() => history.back()}>Go back</button>
        </div>
      </div>
    );
  }
}
