import { useEffect, useState, type FormEvent, type ReactNode } from 'react';

type SessionBody = {
  authenticated?: boolean;
  login?: string | null;
  user?: string | null;
  username?: string | null;
};

type Gate = 'checking' | 'form' | 'open';

/**
 * Password form for a remote browser. The username is the Kerberos user
 * principal. SPNEGO is not required; a cookie session is enough.
 */
export default function LoginGate({ children }: { children: ReactNode }) {
  const [gate, setGate] = useState<Gate>('checking');
  const [username, setUsername] = useState('zndx');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let active = true;
    void fetch('/api/session')
      .then(async (response) => {
        if (!response.ok) return null;
        return await response.json() as SessionBody;
      })
      .then((body) => {
        if (!active) return;
        if (!body || body.authenticated || !body.login) {
          setGate('open');
          return;
        }
        if (body.user) setUsername(body.user);
        setGate('form');
      })
      .catch(() => {
        if (active) setGate('open');
      });
    return () => {
      active = false;
    };
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError('');
    try {
      const response = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: string } | null;
        setError(body?.detail || `Sign-in failed (${response.status})`);
        return;
      }
      setGate('open');
    } catch {
      setError('Sign-in failed. The server did not answer.');
    } finally {
      setPending(false);
    }
  }

  if (gate === 'checking') {
    return <div className="workspace-loading">Checking sign-in…</div>;
  }
  if (gate === 'open') {
    return <>{children}</>;
  }

  return (
    <main className="landing-page">
      <div className="landing-shell">
        <section className="landing-hero">
          <div className="landing-copy">
            <div className="landing-kicker">Semantica Knowledge Explorer</div>
            <h1 className="landing-title">Sign in</h1>
            <p className="landing-subtitle">
              The username is the Kerberos user principal. A browser that can
              send SPNEGO may skip this form.
            </p>
            <form onSubmit={onSubmit} style={{ display: 'grid', gap: '0.75rem', maxWidth: '22rem' }}>
              <label>
                Username
                <input
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  autoComplete="username"
                  required
                  style={{ display: 'block', width: '100%', marginTop: '0.25rem' }}
                />
              </label>
              <label>
                Password
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  required
                  style={{ display: 'block', width: '100%', marginTop: '0.25rem' }}
                />
              </label>
              {error ? <p role="alert">{error}</p> : null}
              <button className="landing-cta-primary" type="submit" disabled={pending}>
                {pending ? 'Signing in…' : 'Sign in'}
              </button>
            </form>
          </div>
        </section>
      </div>
    </main>
  );
}
