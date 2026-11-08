import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { ApiError, authApi } from '../api/client'
import './authGate.css'

type Phase = 'loading' | 'open' | 'prompt'

export default function AuthGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>('loading')
  const [viaEvent, setViaEvent] = useState(false)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    authApi.status()
      .then(s => { if (live) setPhase(s.required && !s.authenticated ? 'prompt' : 'open') })
      .catch(() => { if (live) setPhase('open') }) // older API or unreachable: fail open
    const onRequired = () => { setViaEvent(true); setPhase('prompt') }
    window.addEventListener('ppc:auth-required', onRequired)
    return () => { live = false; window.removeEventListener('ppc:auth-required', onRequired) }
  }, [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy || !key) return
    setBusy(true)
    setError(null)
    try {
      await authApi.login(key)
      setKey('')
      if (viaEvent) window.location.reload()
      else setPhase('open')
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? 'That key is not valid.' : 'Could not reach the server. Try again.')
    } finally {
      setBusy(false)
    }
  }

  if (phase === 'loading') return null
  const initialPrompt = phase === 'prompt' && !viaEvent
  return (
    <>
      {!initialPrompt && children}
      {phase === 'prompt' && (
        <div className="auth-gate">
          <form className="auth-card" onSubmit={submit}>
            <h1>Access key</h1>
            <p>Enter the access key for this demo. Ask the team if you don't have it.</p>
            <label htmlFor="auth-key">Access key</label>
            <input id="auth-key" type="password" autoComplete="current-password" autoFocus value={key} onChange={e => setKey(e.target.value)} />
            {error && <div role="alert" className="auth-error">{error}</div>}
            <button type="submit" disabled={busy || !key}>Continue</button>
          </form>
        </div>
      )}
    </>
  )
}
