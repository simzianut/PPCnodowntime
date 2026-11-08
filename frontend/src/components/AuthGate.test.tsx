import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import AuthGate from './AuthGate'
import { api, authApi } from '../api/client'

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status })
function mockFetch(status: unknown, login: () => Response = () => json(200, { authenticated: true })) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => String(url).includes('/auth/login') ? login() : json(200, status)))
}
const renderGate = () => render(<AuthGate><div>app</div></AuthGate>)

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('AuthGate', () => {
  it('renders children when auth not required', async () => {
    mockFetch({ required: false, authenticated: false })
    renderGate()
    expect(await screen.findByText('app')).toBeInTheDocument()
  })

  it('shows prompt instead of children when required', async () => {
    mockFetch({ required: true, authenticated: false })
    renderGate()
    expect(await screen.findByLabelText('Access key')).toBeInTheDocument()
    expect(screen.queryByText('app')).toBeNull()
  })

  it('shows an error on a wrong key', async () => {
    mockFetch({ required: true, authenticated: false }, () => json(401, { detail: 'no' }))
    renderGate()
    fireEvent.change(await screen.findByLabelText('Access key'), { target: { value: 'bad' } })
    fireEvent.click(screen.getByRole('button'))
    expect(await screen.findByRole('alert')).toHaveTextContent('That key is not valid.')
    expect(screen.getByLabelText('Access key')).toHaveValue('bad')
    expect(screen.queryByText('app')).toBeNull()
  })

  it('renders children after a correct key', async () => {
    mockFetch({ required: true, authenticated: false })
    renderGate()
    fireEvent.change(await screen.findByLabelText('Access key'), { target: { value: 'good' } })
    fireEvent.click(screen.getByRole('button'))
    expect(await screen.findByText('app')).toBeInTheDocument()
  })

  it('shows the prompt over children on ppc:auth-required', async () => {
    mockFetch({ required: false, authenticated: false })
    renderGate()
    await screen.findByText('app')
    window.dispatchEvent(new Event('ppc:auth-required'))
    expect(await screen.findByLabelText('Access key')).toBeInTheDocument()
    expect(screen.getByText('app')).toBeInTheDocument()
  })
})

describe('client 401 handling', () => {
  it('dispatches the event for normal routes but not login', async () => {
    const handler = vi.fn()
    window.addEventListener('ppc:auth-required', handler)
    vi.stubGlobal('fetch', vi.fn(async () => json(401, { detail: 'access key required' })))
    await expect(api.stations()).rejects.toMatchObject({ status: 401 })
    expect(handler).toHaveBeenCalledTimes(1)
    await expect(authApi.login('x')).rejects.toMatchObject({ status: 401 })
    expect(handler).toHaveBeenCalledTimes(1)
    window.removeEventListener('ppc:auth-required', handler)
  })
})
