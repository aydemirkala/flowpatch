export function getAuthToken(): string | null {
  try {
    return sessionStorage.getItem('token')
  } catch {
    return null
  }
}

export function getRole(): string | null {
  try {
    return sessionStorage.getItem('role')
  } catch {
    return null
  }
}

// Decode JWT token to check expiration
function decodeJWT(token: string): { exp?: number } | null {
  try {
    const parts = token.split('.')
    if (parts.length !== 3) return null
    const payload = JSON.parse(atob(parts[1]))
    return payload
  } catch {
    return null
  }
}

// Check if token is expired or will expire soon (within 5 minutes)
export function isTokenExpired(token: string | null): boolean {
  if (!token) return true
  
  const decoded = decodeJWT(token)
  if (!decoded || !decoded.exp) return true
  
  // Check if token expires within 5 minutes (300 seconds buffer)
  const expiresAt = decoded.exp * 1000 // Convert to milliseconds
  const now = Date.now()
  const bufferMs = 5 * 60 * 1000 // 5 minutes
  
  return expiresAt <= (now + bufferMs)
}

// Clear session and redirect to login
export function handleTokenExpiration() {
  sessionStorage.removeItem('token')
  sessionStorage.removeItem('role')
  sessionStorage.removeItem('must_change')
  
  // Show a brief message before redirect
  const body = document.body
  const overlay = document.createElement('div')
  overlay.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.8);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10000;
    color: white;
    font-size: 18px;
    font-family: Inter, system-ui, sans-serif;
  `
  overlay.innerHTML = '<div style="background: #1a1a1a; padding: 30px 50px; border-radius: 8px; border: 2px solid #ff6b6b;"><div style="margin-bottom: 15px; font-size: 24px;">⏱️ Session Expired</div><div>Redirecting to login...</div></div>'
  body.appendChild(overlay)
  
  // Redirect after 2 seconds
  setTimeout(() => {
    window.location.href = '/login'
  }, 2000)
}


