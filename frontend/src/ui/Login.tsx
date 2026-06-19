import React, { useState } from 'react'

export default function Login({ setRole }: { setRole: (r: string | null) => void }): JSX.Element {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState<string>('')

  const submit = async () => {
    setStatus('')
    const body = new URLSearchParams()
    body.set('username', username)
    body.set('password', password)
    try {
      const resp = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body })
      if (!resp.ok) throw new Error('Login failed')
      const data = await resp.json()
      sessionStorage.setItem('token', data.access_token)
      sessionStorage.setItem('role', data.role)
      sessionStorage.setItem('username', data.username)
      if (data.must_change) {
        sessionStorage.setItem('must_change', '1')
      } else {
        sessionStorage.removeItem('must_change')
      }
      // Apply user's theme preference from server
      if (data.theme) {
        document.documentElement.setAttribute('data-theme', data.theme)
        localStorage.setItem('theme', data.theme)
      }
      setRole(data.role)
      setStatus('Logged in')
    } catch (e) {
      setStatus('Login failed')
    }
  }

  const [setupMode, setSetupMode] = useState<boolean>(false)
  const [adminUser, setAdminUser] = useState('admin')
  const [adminPass, setAdminPass] = useState('')

  const checkSetup = async () => {
    try {
      const resp = await fetch('/api/auth/setup-state')
      if (resp.ok) {
        const data = await resp.json()
        setSetupMode(!!data.setup_required)
      }
    } catch {}
  }

  React.useEffect(() => { void checkSetup() }, [])

  const doSetup = async () => {
    setStatus('')
    try {
      const params = new URLSearchParams()
      params.set('username', adminUser)
      params.set('password', adminPass)
      const resp = await fetch('/api/auth/setup-admin', { method: 'POST', body: params })
      if (!resp.ok) throw new Error('Setup failed')
      setSetupMode(false)
      setStatus('Admin configured')
    } catch (e) {
      setStatus('Setup failed')
    }
  }

  return (
    <div className="inputs" style={{ flexWrap: 'wrap' }}>
      {setupMode ? (
        <>
          <span className="muted">Initial admin setup</span>
          <input className="input" placeholder="admin" value={adminUser} onChange={(e) => setAdminUser(e.target.value)} style={{ width: 160 }} />
          <input className="input" placeholder="password" type="password" value={adminPass} onChange={(e) => setAdminPass(e.target.value)} style={{ width: 200 }} />
          <button className="btn" onClick={doSetup}>Save</button>
        </>
      ) : (
        <>
          <input className="input" placeholder="user" value={username} onChange={(e) => setUsername(e.target.value)} style={{ width: 160 }} />
          <input className="input" placeholder="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} style={{ width: 200 }} />
          <button className="btn" onClick={submit}>Login</button>
        </>
      )}
      {status && <span className="muted" style={{ fontSize: 12 }}>{status}</span>}
    </div>
  )
}


