import React, { useEffect, useState } from 'react'

interface SettingsProps {
  onThemeChange?: (theme: string) => void
}

export default function Settings({ onThemeChange }: SettingsProps): JSX.Element {
  const [currentTheme, setCurrentTheme] = useState<string>('dark')
  const [savingTheme, setSavingTheme] = useState(false)
  const [themeMsg, setThemeMsg] = useState('')

  const [currentFontSize, setCurrentFontSize] = useState<string>('medium')
  const [savingFont, setSavingFont] = useState(false)
  const [fontMsg, setFontMsg] = useState('')
  
  // Password change
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [savingPassword, setSavingPassword] = useState(false)
  const [passwordMsg, setPasswordMsg] = useState('')

  const themes = [
    // Dark themes
    { id: 'dark', name: 'Dark', icon: '🌙', description: 'Deep navy dark theme', category: 'dark' },
    { id: 'ocean', name: 'Ocean', icon: '🌊', description: 'Deep blue ocean vibes', category: 'dark' },
    { id: 'forest', name: 'Forest', icon: '🌲', description: 'Natural green tones', category: 'dark' },
    { id: 'sunset', name: 'Sunset', icon: '🌅', description: 'Warm rose gradient', category: 'dark' },
    // Light themes (better readability)
    { id: 'light', name: 'Clean White', icon: '☀️', description: 'Clean white theme', category: 'light' },
    { id: 'soft-ivory', name: 'Soft Ivory', icon: '🍦', description: 'Warmer white, less glare', category: 'light' },
    { id: 'minimal-gray', name: 'Minimal Gray', icon: '🩶', description: 'Gray reduces eye strain', category: 'light' },
    { id: 'paper-white', name: 'Paper White', icon: '📄', description: 'Max clarity, teal accents', category: 'light' },
    { id: 'light-warm', name: 'Light Warm', icon: '🌾', description: 'Cream with earthy tones', category: 'light' },
    { id: 'high-contrast', name: 'High Contrast', icon: '🔲', description: 'Maximum readability', category: 'light' }
  ]

  const fontSizes = [
    { id: 'small',  name: 'Small',   zoom: '90%',  icon: 'A',  desc: 'Compact view, more data' },
    { id: 'medium', name: 'Medium',  zoom: '100%', icon: 'A',  desc: 'Default size' },
    { id: 'large',  name: 'Large',   zoom: '110%', icon: 'A',  desc: 'Easier to read' },
    { id: 'xlarge', name: 'X-Large', zoom: '120%', icon: 'A',  desc: 'Maximum readability' },
  ]

  const fontZoomMap: Record<string, number> = { small: 0.9, medium: 1, large: 1.1, xlarge: 1.2 }

  const applyFontSize = (fs: string) => {
    const zoom = fontZoomMap[fs] || 1
    document.documentElement.style.zoom = String(zoom)
    localStorage.setItem('font_size', fs)
  }

  useEffect(() => {
    loadSettings()
  }, [])

  const loadSettings = async () => {
    try {
      const token = sessionStorage.getItem('token')
      if (!token) return
      
      const resp = await fetch('/api/auth/settings', {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        const data = await resp.json()
        setCurrentTheme(data.theme || 'dark')
        const fs = data.font_size || 'medium'
        setCurrentFontSize(fs)
        applyFontSize(fs)
      }
    } catch (e) {
      console.error('Failed to load settings:', e)
    }
  }

  const saveTheme = async (themeId: string) => {
    setSavingTheme(true)
    setThemeMsg('')
    
    try {
      const token = sessionStorage.getItem('token')
      if (!token) throw new Error('Not authenticated')
      
      const body = new URLSearchParams()
      body.set('theme', themeId)
      
      const resp = await fetch('/api/auth/settings', {
        method: 'POST',
        headers: { 
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/x-www-form-urlencoded'
        },
        body
      })
      
      if (!resp.ok) throw new Error('Failed to save theme')
      
      // Apply theme immediately
      document.documentElement.setAttribute('data-theme', themeId)
      setCurrentTheme(themeId)
      
      // Also update localStorage for immediate page loads
      localStorage.setItem('theme', themeId)
      
      // Notify parent if callback provided
      if (onThemeChange) {
        onThemeChange(themeId)
      }
      
      setThemeMsg('✓ Theme saved')
      setTimeout(() => setThemeMsg(''), 2000)
    } catch (e: any) {
      setThemeMsg('Failed to save theme')
    } finally {
      setSavingTheme(false)
    }
  }

  const saveFontSize = async (fs: string) => {
    setSavingFont(true)
    setFontMsg('')
    try {
      const token = sessionStorage.getItem('token')
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('font_size', fs)
      const resp = await fetch('/api/auth/settings', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      if (!resp.ok) throw new Error('Failed to save font size')
      applyFontSize(fs)
      setCurrentFontSize(fs)
      setFontMsg('✓ Font size saved')
      setTimeout(() => setFontMsg(''), 2000)
    } catch (e: any) {
      setFontMsg('Failed to save font size')
    } finally {
      setSavingFont(false)
    }
  }

  const changePassword = async () => {
    setPasswordMsg('')
    
    if (!newPassword || !confirmPassword) {
      setPasswordMsg('Please fill both password fields')
      return
    }
    
    if (newPassword !== confirmPassword) {
      setPasswordMsg('Passwords do not match')
      return
    }
    
    if (newPassword.length < 8) {
      setPasswordMsg('Password must be at least 8 characters')
      return
    }
    
    setSavingPassword(true)
    
    try {
      const token = sessionStorage.getItem('token')
      if (!token) throw new Error('Not authenticated')
      
      const body = new URLSearchParams()
      body.set('new_password', newPassword)
      body.set('confirm_password', confirmPassword)
      
      const resp = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/x-www-form-urlencoded'
        },
        body
      })
      
      if (!resp.ok) {
        const data = await resp.json()
        throw new Error(data.detail || 'Failed to change password')
      }
      
      setPasswordMsg('✓ Password changed successfully')
      setNewPassword('')
      setConfirmPassword('')
    } catch (e: any) {
      setPasswordMsg(e.message || 'Failed to change password')
    } finally {
      setSavingPassword(false)
    }
  }

  const username = sessionStorage.getItem('username') || ''

  return (
    <div className="container" style={{ maxWidth: 900, margin: '0 auto', padding: '20px' }}>
      <div className="hero">
        <h1>⚙️ Settings</h1>
        <p className="muted">Manage your personal preferences</p>
      </div>

      {/* User Info */}
      <div className="card" style={{ marginTop: 16, marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ 
            width: 60, 
            height: 60, 
            borderRadius: '50%', 
            background: 'linear-gradient(135deg, var(--brand), var(--accent))',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 24,
            color: 'white',
            fontWeight: 700
          }}>
            {username.charAt(0).toUpperCase()}
          </div>
          <div>
            <div style={{ fontSize: 18, fontWeight: 600 }}>{username}</div>
            <div className="muted" style={{ fontSize: 13 }}>
              Role: <strong>{sessionStorage.getItem('role') || 'unknown'}</strong>
            </div>
          </div>
        </div>
      </div>

      {/* Theme Settings */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h2>🎨 Theme</h2>
        <p className="muted">Choose your preferred color theme. Your selection is saved to your account.</p>
        
        {/* Dark Themes */}
        <h3 style={{ fontSize: 14, marginTop: 20, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          🌙 Dark Themes
        </h3>
        <div style={{ 
          display: 'grid', 
          gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', 
          gap: 10
        }}>
          {themes.filter(t => t.category === 'dark').map(theme => (
            <div
              key={theme.id}
              onClick={() => !savingTheme && saveTheme(theme.id)}
              style={{
                padding: 12,
                borderRadius: 10,
                border: currentTheme === theme.id 
                  ? '2px solid var(--brand)' 
                  : '1px solid var(--border)',
                background: currentTheme === theme.id 
                  ? 'rgba(59, 130, 246, 0.1)' 
                  : 'transparent',
                cursor: savingTheme ? 'wait' : 'pointer',
                transition: 'all 0.2s ease',
                position: 'relative',
                opacity: savingTheme ? 0.7 : 1
              }}
            >
              {currentTheme === theme.id && (
                <span style={{
                  position: 'absolute',
                  top: 6,
                  right: 6,
                  background: 'var(--brand)',
                  color: 'white',
                  borderRadius: '50%',
                  width: 18,
                  height: 18,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 10
                }}>
                  ✓
                </span>
              )}
              <div style={{ fontSize: 22, marginBottom: 4 }}>{theme.icon}</div>
              <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 2 }}>{theme.name}</div>
              <div style={{ fontSize: 10, color: 'var(--muted)' }}>{theme.description}</div>
            </div>
          ))}
        </div>

        {/* Light Themes */}
        <h3 style={{ fontSize: 14, marginTop: 20, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          ☀️ Light Themes <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--muted)' }}>(Better Readability)</span>
        </h3>
        <div style={{ 
          display: 'grid', 
          gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', 
          gap: 10
        }}>
          {themes.filter(t => t.category === 'light').map(theme => (
            <div
              key={theme.id}
              onClick={() => !savingTheme && saveTheme(theme.id)}
              style={{
                padding: 12,
                borderRadius: 10,
                border: currentTheme === theme.id 
                  ? '2px solid var(--brand)' 
                  : '1px solid var(--border)',
                background: currentTheme === theme.id 
                  ? 'rgba(59, 130, 246, 0.1)' 
                  : 'transparent',
                cursor: savingTheme ? 'wait' : 'pointer',
                transition: 'all 0.2s ease',
                position: 'relative',
                opacity: savingTheme ? 0.7 : 1
              }}
            >
              {currentTheme === theme.id && (
                <span style={{
                  position: 'absolute',
                  top: 6,
                  right: 6,
                  background: 'var(--brand)',
                  color: 'white',
                  borderRadius: '50%',
                  width: 18,
                  height: 18,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 10
                }}>
                  ✓
                </span>
              )}
              <div style={{ fontSize: 22, marginBottom: 4 }}>{theme.icon}</div>
              <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 2 }}>{theme.name}</div>
              <div style={{ fontSize: 10, color: 'var(--muted)' }}>{theme.description}</div>
            </div>
          ))}
        </div>
        
        {themeMsg && (
          <div style={{ 
            marginTop: 16, 
            fontSize: 12, 
            color: themeMsg.includes('✓') ? 'var(--ok)' : 'var(--danger)'
          }}>
            {themeMsg}
          </div>
        )}
      </div>

      {/* Font Size */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h2>🔤 Font Size</h2>
        <p className="muted">Adjust text size for better readability. Your selection is saved to your account.</p>
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 10,
          marginTop: 16
        }}>
          {fontSizes.map(fs => (
            <div
              key={fs.id}
              onClick={() => !savingFont && saveFontSize(fs.id)}
              style={{
                padding: 16,
                borderRadius: 10,
                border: currentFontSize === fs.id ? '2px solid var(--brand)' : '1px solid var(--border)',
                background: currentFontSize === fs.id ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
                cursor: savingFont ? 'wait' : 'pointer',
                transition: 'all 0.2s ease',
                textAlign: 'center',
                position: 'relative',
                opacity: savingFont ? 0.7 : 1
              }}
            >
              {currentFontSize === fs.id && (
                <span style={{
                  position: 'absolute', top: 6, right: 6,
                  background: 'var(--brand)', color: 'white', borderRadius: '50%',
                  width: 18, height: 18, display: 'flex', alignItems: 'center',
                  justifyContent: 'center', fontSize: 10
                }}>✓</span>
              )}
              <div style={{
                fontSize: fs.id === 'small' ? 16 : fs.id === 'medium' ? 20 : fs.id === 'large' ? 24 : 28,
                fontWeight: 700, marginBottom: 6
              }}>
                {fs.icon}
              </div>
              <div style={{ fontWeight: 600, fontSize: 13 }}>{fs.name}</div>
              <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{fs.zoom}</div>
              <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{fs.desc}</div>
            </div>
          ))}
        </div>
        {fontMsg && (
          <div style={{
            marginTop: 16, fontSize: 12,
            color: fontMsg.includes('✓') ? 'var(--ok)' : 'var(--danger)'
          }}>
            {fontMsg}
          </div>
        )}
      </div>

      {/* Password Change */}
      <div className="card">
        <h2>🔐 Change Password</h2>
        <p className="muted">Update your account password</p>
        
        <div style={{ marginTop: 16, maxWidth: 400 }}>
          <div style={{ marginBottom: 12 }}>
            <label style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>New Password</label>
            <input
              type="password"
              className="input"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="Minimum 8 characters"
              style={{ width: '100%' }}
            />
          </div>
          
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>Confirm Password</label>
            <input
              type="password"
              className="input"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Re-enter password"
              style={{ width: '100%' }}
            />
          </div>
          
          <button 
            className="btn" 
            onClick={changePassword}
            disabled={savingPassword || !newPassword || !confirmPassword}
          >
            {savingPassword ? 'Saving...' : 'Change Password'}
          </button>
          
          {passwordMsg && (
            <div style={{ 
              marginTop: 12, 
              fontSize: 12, 
              color: passwordMsg.includes('✓') ? 'var(--ok)' : 'var(--danger)'
            }}>
              {passwordMsg}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

