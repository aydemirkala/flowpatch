import React, { useEffect, useState, useRef, useMemo } from 'react'
import logo5 from '../../logo5.jpg'
import logoApp from '../../logo-app.svg'
import Login from './Login'
import Admin from './Admin'
import Dashboard from './Dashboard'
import ImageUpdate from './ImageUpdate'
import Compare from './Compare'
import Settings from './Settings'
import { Landscape } from './Landscape'
import { fetchSyncLogs } from '../services/multiBackend'
import { isTokenExpired, handleTokenExpiration } from './auth'

const diffLabel = (raw: string) => raw === 'same' ? 'Up to date' : raw === 'major' ? 'Major update' : raw === 'minor' ? 'Patch update' : raw
const helmLabel = (raw: string) => raw === 'ok' ? 'OK' : raw === 'drift' ? 'Drift' : raw === 'none' ? 'None' : raw === 'unknown' ? 'Unknown' : raw
// EOL status dimension: a real public date ('dated'), else the AI-derived status, else no data.
const eolStatusKey = (r: any): string => r.eol_date ? 'dated' : (r.eol_support_status || 'none')
const eolStatusLabel = (raw: string) => raw === 'dated' ? 'Has date' : raw === 'supported' ? 'Supported (AI)' : raw === 'eol' ? 'Outdated (AI)' : raw === 'unknown' ? 'Unknown (AI)' : raw === 'none' ? 'No data' : raw

type Resource = {
  id: number
  platform: string
  namespace: string
  resource_name: string
  kind: string
  replicas?: number | null
  image?: string | null
  current_version?: string | null
  latest_version?: string | null
  version_diff?: string | null
  last_updated_at?: string | null
  last_checked_at?: string | null
  vulnerability_count?: number | null
  note?: string | null
  planned_upgrade_at?: string | null
  vulnerabilities?: string[] | null
  // Computed flag from backend security_info.exists_in_cluster
  advice?: string | null
  update_history?: { version?: string | null; checked_at?: string | null; image?: string | null; latest_version?: string | null; version_diff?: string | null; container_name?: string | null; source?: string | null; job_id?: number | null }[] | null
  // Number of distinct change-history points (from slim listing). Full history is
  // lazy-loaded by the History modal via GET /api/resources/{id}/history.
  history_count?: number | null
  security_info?: any | null
  product_name?: string | null
  eol_date?: string | null
  // LLM-derived support status shown only when there's no public EOL date.
  eol_support_status?: string | null
  eol_support_note?: string | null
  // Helm release status (Phase 1A): 'ok' | 'drift' | 'none' | 'unknown'. drift detail
  // is lazy-loaded on click via resource-detail (helm_drift_detail).
  helm_status?: string | null
  helm_release_name?: string | null
  _backend?: string
}

// Helper: check if an image matches a product name
function imageMatchesProduct(image: string, product: string): boolean {
  if (!image || !product) return false
  const imgLower = image.toLowerCase()
  const productLower = product.toLowerCase()
  const parts = imgLower.split('/')
  const lastPart = parts[parts.length - 1].split(':')[0]
  const repoPath = parts.length > 1 
    ? parts.slice(1).join('/').split(':')[0] 
    : lastPart
  return repoPath.includes(productLower) || lastPart.includes(productLower)
}

// Detect product_name from container image by matching against known managed products (longest match wins)
function detectProductFromImage(image: string, managedProducts: string[]): string | null {
  if (!image || managedProducts.length === 0) return null
  const matches = managedProducts.filter(p => imageMatchesProduct(image, p))
  if (matches.length === 0) return null
  return matches.reduce((a, b) => a.length >= b.length ? a : b)
}

type ResourceFetchResult = { resources: Resource[], failedBackends: string[] }

async function fetchResourceData(): Promise<ResourceFetchResult> {
  const token = sessionStorage.getItem('token') || ''
  if (!token) {
    console.warn('No token found, fetching from default backend only')
    const resp = await fetch('/api/resources', {
      headers: { 'Cache-Control': 'no-cache, no-store, must-revalidate' },
      cache: 'no-store'
    })
    if (!resp.ok) throw new Error('Failed to fetch resources')
    return { resources: await resp.json(), failedBackends: [] }
  }

  console.log('Token found, attempting multi-backend fetch...')
  try {
    const { fetchResourcesWithStatus } = await import('../services/multiBackend')
    const result = await fetchResourcesWithStatus(token)
    console.log(`Multi-backend fetch: ${result.resources.length} resources, ${result.failedBackends.length} failed`)
    return result
  } catch (e) {
    console.error('Multi-backend fetch failed, falling back to default:', e)
    const resp = await fetch('/api/resources', {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Cache-Control': 'no-cache, no-store, must-revalidate'
      },
      cache: 'no-store'
    })
    if (!resp.ok) throw new Error('Failed to fetch resources')
    return { resources: await resp.json(), failedBackends: [] }
  }
}

// Route write API calls to the correct backend via proxy when resource came from a remote backend
function makeApiUrl(resource: Resource, path: string): string {
  if (resource._backend) return `/api/proxy/${encodeURIComponent(resource._backend)}/api/${path}`
  return `/api/${path}`
}

async function triggerRefresh(): Promise<void> {
  // Extended timeout for refresh - 2 hours for large-scale deployments with dynamic discovery
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 7200000) // 2 hours
  const token = sessionStorage.getItem('token') || ''
  try {
    const resp = await fetch('/api/resources/refresh', { 
      method: 'POST',
      signal: controller.signal,
      headers: { 
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      }
    })
    clearTimeout(timeoutId)
  if (!resp.ok) throw new Error('Failed to refresh resources')
  } catch (e: any) {
    clearTimeout(timeoutId)
    if (e.name === 'AbortError') {
      throw new Error('Refresh timed out after 2 hours')
    }
    throw e
  }
}

// Sidebar Component - Reusable navigation
function Sidebar({ role, username, setRole }: { role: string | null, username: string | null, setRole: (r: string | null) => void }) {
  const handleLogout = () => {
    sessionStorage.removeItem('token')
    sessionStorage.removeItem('role')
    sessionStorage.removeItem('username')
    sessionStorage.removeItem('must_change')
    setRole(null)
    window.location.hash = '#/'
  }

  return (
    <aside className="sidebar">
      <div className="brand" style={{ fontSize: 11, lineHeight: 1.3, padding: '12px 8px', flexDirection: 'column', alignItems: 'center', textAlign: 'center' }}>
        <img src={logoApp} alt="Logo" style={{ width: 145, height: 145, marginBottom: 6 }} />
        <span>K8s ThirdParty<br/>Version Management</span>
      </div>
      <nav className="sideNav">
        <a className="sideLink" href="#/">📋 Resources</a>
        <a className="sideLink" href="#/dashboard">📊 Dashboard</a>
        {role === 'admin' && <a className="sideLink" href="#/image-update">🔄 Update Product</a>}
        <a className="sideLink" href="#/sync">♻️ Sync</a>
        <a className="sideLink" href="#/download">📥 Download</a>
        <a className="sideLink" href="#/compare">🔍 Compare</a>
        <a className="sideLink" href="#/landscape">🗺️ Landscape</a>
        <a className="sideLink" href="#/settings">👤 Settings</a>
        {role === 'admin' && (
          <>
            <div style={{ borderTop: '1px solid var(--border)', margin: '12px 0' }} />
            <a className="sideLink" href="#/admin">⚙️ Admin</a>
          </>
        )}
      </nav>
      <div className="sideActions">
        <button className="btn secondary" onClick={handleLogout}>🚪 Logout</button>
      </div>
      <div className="userSection">
        <div className="muted" style={{ fontSize: 11 }}>Signed in as</div>
        <div style={{ fontWeight: 600, fontSize: 13 }}>{username || ''}</div>
      </div>
    </aside>
  )
}

export function App(): JSX.Element {
  const [resources, setResources] = useState<Resource[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [failedBackends, setFailedBackends] = useState<string[]>([])
  const [role, setRole] = useState<string | null>(null)
  const [username, setUsername] = useState<string | null>(null)
  const [route, setRoute] = useState<string>(window.location.hash)
  const [securityModal, setSecurityModal] = useState<Resource | null>(null)
  const [helmDriftModal, setHelmDriftModal] = useState<Resource | null>(null)
  const [eolAiModal, setEolAiModal] = useState<Resource | null>(null)
  const [historyModal, setHistoryModal] = useState<Resource | null>(null)
  const [noteModal, setNoteModal] = useState<Resource | null>(null)
  const [planModal, setPlanModal] = useState<Resource | null>(null)
  const [syncProgress, setSyncProgress] = useState<string>('')
  const [syncingResourceId, setSyncingResourceId] = useState<number | null>(null)
  const [aiModal, setAiModal] = useState<{resource: Resource, advice: string, loading: boolean, cached: boolean} | null>(null)
  // Filters (multi-select arrays for key filters)
  const [nsFilter, setNsFilter] = useState<string[]>([])
  const [productFilter, setProductFilter] = useState<string[]>([])
  const [platformFilter, setPlatformFilter] = useState<string[]>([])
  const [kindFilter, setKindFilter] = useState<string[]>([])
  const [containerFilter, setContainerFilter] = useState<string[]>([])
  const [versionDiffFilter, setVersionDiffFilter] = useState<string[]>([])
  const [helmFilter, setHelmFilter] = useState<string[]>([])
  const [eolStatusFilter, setEolStatusFilter] = useState<string[]>([])
  // Single-value filters
  const [currentFilter, setCurrentFilter] = useState<string>('')
  const [riskMin, setRiskMin] = useState<string>('')
  const [eolBefore, setEolBefore] = useState<string>('')
  const [registryFilter, setRegistryFilter] = useState<string[]>([])
  const [hasNoteFilter, setHasNoteFilter] = useState<string>('')
  const [hasPlanFilter, setHasPlanFilter] = useState<string>('')
  const [imageRepoFilter, setImageRepoFilter] = useState<string>('')
  const [replicasFilter, setReplicasFilter] = useState<string>('')
  // Column visibility
  const [hiddenColumns, setHiddenColumns] = useState<Set<string>>(() => {
    const stored = localStorage.getItem('hiddenColumns')
    if (stored) {
      try { return new Set(JSON.parse(stored)) } catch { /* ignore */ }
    }
    return new Set(['Kind', 'Replicas', 'Container', 'Diff', 'EOL'])
  })
  // Pagination
  const [currentPage, setCurrentPage] = useState<number>(1)
  const [pageSize, setPageSize] = useState<number>(20)
  // Sorting
  const [sortColumn, setSortColumn] = useState<string>('')
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc')
  // Backend selection for SYNC
  const [showSyncModal, setShowSyncModal] = useState<boolean>(false)
  const [backends, setBackends] = useState<any[]>([])
  const [selectedBackends, setSelectedBackends] = useState<Set<string>>(new Set())
  // Auto-refresh feature - defaults to 5 minutes (300 seconds)
  const [refreshInterval, setRefreshInterval] = useState<number>(() => {
    // Try to load from localStorage, default to 300 (5 minutes)
    const stored = localStorage.getItem('autoRefreshInterval')
    return stored ? Number(stored) : 300
  })
  const [lastRefreshTime, setLastRefreshTime] = useState<Date | null>(null)
  const [lastSyncsByBackend, setLastSyncsByBackend] = useState<Map<string, Date>>(new Map())
  // Security thresholds from backend config
  const [securityThresholds, setSecurityThresholds] = useState<{
    red: { riskFactor: number; critical: number };
    orange: { riskFactorMin: number; riskFactorMax: number; critical: number };
    green: { riskFactor: number; critical: number };
  }>({
    red: { riskFactor: 10, critical: 0 },
    orange: { riskFactorMin: 5, riskFactorMax: 10, critical: 0 },
    green: { riskFactor: 5, critical: 0 },
  })

  const loadLatestSyncTime = async () => {
    try {
      const token = sessionStorage.getItem('token')
      if (!token) return
      
      // Fetch sync logs from all backends
      const allLogs = await fetchSyncLogs(token)
      
      // Find the most recent completed sync per backend
      const completedLogs = allLogs.filter((log: any) => 
        log.finished_at && log.status === 'success'
      )
      
      // Group by backend and find latest for each
      const syncsByBackend = new Map<string, Date>()
      
      for (const log of completedLogs) {
        const backendName = log.backend_name || log._backend || 'Unknown'
        const finishedAt = new Date(log.finished_at)
        
        const existing = syncsByBackend.get(backendName)
        if (!existing || finishedAt > existing) {
          syncsByBackend.set(backendName, finishedAt)
        }
      }
      
      setLastSyncsByBackend(syncsByBackend)
    } catch (e) {
      console.error('Failed to load latest sync time:', e)
    }
  }

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetchResourceData()
      setResources(result.resources)
      setFailedBackends(result.failedBackends)
      setLastRefreshTime(new Date())
    } catch (e: any) {
      setError(e.message || String(e))
    } finally {
      setLoading(false)
    }
  }

  const syncSingleResource = async (resource: Resource) => {
    const token = sessionStorage.getItem('token') || ''
    if (!token) return
    
    setSyncingResourceId(resource.id)
    try {
      // All syncs go through federation proxy (handles both local and remote)
      const resp = await fetch('/api/federation/sync-resource', {
        method: 'POST',
        headers: { 
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          platform: resource.platform,
          namespace: resource.namespace,
          resource_name: resource.resource_name,
          kind: resource.kind,
        })
      })
      
      const data = await resp.json()
      
      if (data.ok) {
        // Show non-blocking notification before reloading
        const productName = resource.product_name || 'this product'
        if (data.discovered_new && data.discovered_new > 0) {
          setSyncProgress(`✓ Sync complete! ${data.discovered_new} new ${productName} resource(s) discovered and added.`)
        } else {
          setSyncProgress(`✓ Sync complete for ${resource.resource_name}.`)
        }
        setTimeout(() => setSyncProgress(''), 10000)
        
        // Reload resources to reflect the update
        await load()
      } else {
        console.error('Sync failed:', data.error || data.detail)
        setError(`Sync failed: ${data.error || data.detail || 'Unknown error'}`)
      }
    } catch (e: any) {
      console.error('Sync error:', e)
      setError(`Sync error: ${e.message || String(e)}`)
    } finally {
      setSyncingResourceId(null)
    }
  }

  const askAI = async (resource: Resource, force = false) => {
    const token = sessionStorage.getItem('token') || ''
    if (!token) return
    setAiModal({ resource, advice: '', loading: true, cached: false })
    try {
      const resp = await fetch(makeApiUrl(resource, `resources/${resource.id}/ask-ai${force ? '?force=true' : ''}`), {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` }
      })
      if (!resp.ok) {
        const d = await resp.json().catch(() => ({}))
        setAiModal(prev => prev ? { ...prev, loading: false, advice: `Error: ${d.detail || resp.statusText}` } : null)
        return
      }
      const data = await resp.json()
      setAiModal(prev => prev ? { ...prev, loading: false, advice: data.advice, cached: data.cached } : null)
    } catch (e: any) {
      setAiModal(prev => prev ? { ...prev, loading: false, advice: `Error: ${e.message}` } : null)
    }
  }

  // (column visibility panel is always visible — no click-outside handler needed)
  useEffect(() => {
  }, [])

  // Save refresh interval preference to localStorage
  useEffect(() => {
    localStorage.setItem('autoRefreshInterval', String(refreshInterval))
  }, [refreshInterval])

  // Save column visibility preference to localStorage
  useEffect(() => {
    localStorage.setItem('hiddenColumns', JSON.stringify([...hiddenColumns]))
  }, [hiddenColumns])

  // Auto-refresh effect (only on Resources page)
  useEffect(() => {
    if (refreshInterval === 0) return
    const isResourcesPage = !route || route === '' || route === '#/' || route === '#'
    if (!isResourcesPage) return

    const intervalMs = refreshInterval * 1000
    const timer = setInterval(() => {
      void load()
    }, intervalMs)
    
    return () => clearInterval(timer)
  }, [refreshInterval, route])

  // Token expiration check - runs every 60 seconds
  useEffect(() => {
    const checkToken = () => {
      const token = sessionStorage.getItem('token')
      if (token && isTokenExpired(token)) {
        console.log('Token expired, redirecting to login...')
        handleTokenExpiration()
      }
    }
    
    // Check immediately on mount
    checkToken()
    
    // Then check every 60 seconds
    const timer = setInterval(checkToken, 60000)
    
    return () => clearInterval(timer)
  }, [])

  // Periodically check for new sync completions (every 30 seconds)
  useEffect(() => {
    const checkSyncUpdates = async () => {
      await loadLatestSyncTime()
    }
    
    // Only start checking if user is authenticated
    if (!role) return
    
    // Check immediately when authenticated
    checkSyncUpdates()
    
    // Then check every 30 seconds
    const timer = setInterval(checkSyncUpdates, 30000)
    
    return () => clearInterval(timer)
  }, [role])

  // Apply theme and font size from localStorage on app load
  useEffect(() => {
    const savedTheme = localStorage.getItem('theme') || 'dark'
    document.documentElement.setAttribute('data-theme', savedTheme)

    const fontZoomMap: Record<string, number> = { small: 0.9, medium: 1, large: 1.1, xlarge: 1.2 }
    const savedFont = localStorage.getItem('font_size') || 'medium'
    document.documentElement.style.zoom = String(fontZoomMap[savedFont] || 1)
  }, [])

  useEffect(() => {
    // Set role and username
    const storedRole = sessionStorage.getItem('role')
    if (storedRole) setRole(storedRole)
    const storedUser = sessionStorage.getItem('username')
    if (storedUser) setUsername(storedUser)
    
    const onHash = () => setRoute(window.location.hash)
    window.addEventListener('hashchange', onHash)
    
    // If backend is freshly installed (DB empty), force setup flow by clearing any stale session
    ;(async () => {
      try {
        const resp = await fetch('/api/auth/setup-state')
        if (resp.ok) {
          const data = await resp.json()
          if (data.setup_required) {
            sessionStorage.removeItem('token')
            sessionStorage.removeItem('role')
            setRole(null)
          }
        }
      } catch {}
    })()
    
    // Force Settings route when user must change password
    try {
      if (sessionStorage.getItem('must_change') === '1') {
        // Redirect to admin page if admin, otherwise show password change modal
        const userRole = sessionStorage.getItem('role')
        if (userRole === 'admin') {
          window.location.hash = '#/admin'
        }
      }
    } catch {}
    
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  // Load security thresholds from backend
  useEffect(() => {
    const loadSecurityThresholds = async () => {
      try {
        const resp = await fetch('/api/settings/security-thresholds')
        if (resp.ok) {
          const data = await resp.json()
          setSecurityThresholds(data)
        }
      } catch (e) {
        console.error('Failed to load security thresholds:', e)
      }
    }
    loadSecurityThresholds()
  }, [])

  // Load resources when role is set and on pages that need them
  useEffect(() => {
    if (!role) return
    const isResourcesPage = !route || route === '' || route === '#/' || route === '#'
    const isLandscapePage = route === '#/landscape'
    if ((isResourcesPage || isLandscapePage) && resources.length === 0) {
      void load()
    }
  }, [role, route])

  // Keep username in sync with login/logout
  useEffect(() => {
    try {
      setUsername(sessionStorage.getItem('username'))
    } catch {}
  }, [role])

  // Load backends for sync selection
  const loadBackends = async () => {
    try {
      const token = sessionStorage.getItem('token')
      if (!token) {
        console.warn('No token available for loading backends')
        return
      }
      const resp = await fetch('/api/backend-endpoints', { 
        headers: { Authorization: `Bearer ${token}` } 
      })
      if (resp.ok) {
        const data = await resp.json()
        // Ensure data is an array
        if (!Array.isArray(data)) {
          console.error('Backend data is not an array:', data)
          setBackends([])
          return
        }
        setBackends(data)
        // Pre-select all approved backends
        const approved = data.filter((b: any) => b && b.approved).map((b: any) => b.name)
        setSelectedBackends(new Set(approved))
      } else {
        console.error('Failed to load backends, status:', resp.status)
        setBackends([])
      }
    } catch (e) {
      console.error('Failed to load backends:', e)
      setBackends([])
    }
  }

  useEffect(() => { if (role === 'admin') void loadBackends() }, [role])

  const downloadCSV = () => {
    // Generate CSV from current resources (includes all backends)
    // Column order: platform, product_name first
    const header = [
      "platform","product_name","namespace","name","kind","container","image","current","latest","diff",
      "note","upgrade_plan","eol",
      "twistlock_critical","twistlock_high","twistlock_medium","twistlock_low","twistlock_total","twistlock_risk_factors",
      "checked"
    ]
    
    const rows = [header]
    resources.forEach(r => {
      const containers = (r as any).security_info?.containers || []
      if (containers.length > 0) {
        containers.forEach((c: any) => {
          const tw = c.twistlock || {}
          const dist = tw.vulnerabilityDistribution || {}
          let currentVer = c.current_version
          if (!currentVer && c.image) {
            const parts = c.image.split(':')
            currentVer = parts.length > 1 ? parts[parts.length - 1] : ''
          }
          const containerProduct = detectProductFromImage(c.image || '', managedProductNames) || ''
          rows.push([
            r.platform, 
            containerProduct,
            r.namespace, 
            r.resource_name, 
            r.kind,
            c.name || '', 
            c.image || '', 
            currentVer || '',
            c.latest_version || '', 
            c.version_diff ?? '',
            (r as any).note || '', 
            (r as any).planned_upgrade_at || '', 
            c.eol_date || '',
            dist.critical ?? 0, 
            dist.high ?? 0, 
            dist.medium ?? 0, 
            dist.low ?? 0, 
            dist.total ?? 0,
            tw.riskFactorCount ?? 0,
            r.last_checked_at || ''
          ])
        })
      } else {
        const tw = (r.security_info as any)?.twistlock || {}
        const dist = tw.vulnerabilityDistribution || {}
        let currentVer = r.current_version
        if (!currentVer && r.image) {
          const parts = r.image.split(':')
          currentVer = parts.length > 1 ? parts[parts.length - 1] : ''
        }
        rows.push([
          r.platform, 
          r.product_name || '',
          r.namespace, 
          r.resource_name, 
          r.kind,
          '', 
          r.image || '', 
          currentVer || '',
          r.latest_version || '', 
          r.version_diff ?? '',
          (r as any).note || '', 
          (r as any).planned_upgrade_at || '', 
          r.eol_date || '',
          dist.critical ?? 0, 
          dist.high ?? 0, 
          dist.medium ?? 0, 
          dist.low ?? 0, 
          dist.total ?? 0,
          tw.riskFactorCount ?? 0,
          r.last_checked_at || ''
        ])
      }
    })
    
    const csv = rows.map(row => row.map(cell => 
      typeof cell === 'string' && (cell.includes(',') || cell.includes('"') || cell.includes('\n'))
        ? `"${cell.replace(/"/g, '""')}"`
        : cell
    ).join(',')).join('\n')
    
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `resources_all_backends_${new Date().toISOString().split('T')[0]}.csv`
    link.click()
  }
  
  // Download filtered results as CSV (data is already expanded to per-container rows)
  const downloadFilteredCSV = (data: any[]) => {
    const header = [
      "platform","product_name","namespace","name","kind","replicas","container","image","current","latest","diff",
      "note","upgrade_plan","eol",
      "twistlock_critical","twistlock_high","twistlock_medium","twistlock_low","twistlock_total","twistlock_risk_factors"
    ]
    
    const rows = [header]
    data.forEach(r => {
      const tw = (r as any).security_info?.twistlock || {}
      const dist = tw.vulnerabilityDistribution || {}
      rows.push([
        r.platform || '',
        r.product_name || '',
        r.namespace || '',
        r.resource_name || '',
        r.kind || '',
        r.replicas ?? '',
        (r as any).container_name || '',
        r.image || '',
        r.current_version || '',
        r.latest_version || '',
        r.version_diff ?? '',
        r.note || '',
        r.planned_upgrade_at || '',
        eolExportText(r),
        dist.critical ?? 0, 
        dist.high ?? 0, 
        dist.medium ?? 0, 
        dist.low ?? 0, 
        dist.total ?? 0,
        tw.riskFactorCount ?? 0
      ])
    })
    
    const csv = rows.map(row => row.map(cell => 
      typeof cell === 'string' && (cell.includes(',') || cell.includes('"') || cell.includes('\n'))
        ? `"${cell.replace(/"/g, '""')}"`
        : cell
    ).join(',')).join('\n')
    
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `resources_filtered_${new Date().toISOString().split('T')[0]}.csv`
    link.click()
  }
  
  // Download filtered results as PDF
  const downloadFilteredPDF = (data: any[]) => {
    // Create a new window with printable content
    const printWindow = window.open('', '_blank')
    if (!printWindow) {
      alert('Please allow popups to download PDF')
      return
    }
    
    // Data is already expanded to per-container rows — extract fields for PDF
    const expandedRows: any[] = []
    let criticalCount = 0
    data.forEach(r => {
      const tw = (r as any).security_info?.twistlock || {}
      const dist = tw.vulnerabilityDistribution || {}
      const critical = dist.critical ?? 0
      if (critical > 0) criticalCount++
      expandedRows.push({
        platform: r.platform || '',
        product_name: r.product_name || '',
        namespace: r.namespace || '',
        resource_name: r.resource_name || '',
        kind: r.kind || '',
        replicas: r.replicas ?? '-',
        image: r.image || '',
        current_version: r.current_version || '',
        latest_version: r.latest_version || '',
        version_diff: r.version_diff,
        eol: eolExportText(r),
        critical, high: dist.high ?? 0, medium: dist.medium ?? 0
      })
    })
    
    const tableRows = expandedRows.map(r => `
      <tr>
        <td>${r.platform}</td>
        <td>${r.product_name}</td>
        <td>${r.namespace}</td>
        <td>${r.resource_name}</td>
        <td>${r.kind}</td>
        <td style="text-align:center;${r.replicas === 0 ? 'color:#ef4444;font-weight:bold' : ''}">${r.replicas}</td>
        <td style="max-width:200px;word-break:break-all;font-size:9px">${r.image}</td>
        <td>${r.current_version}</td>
        <td>${r.latest_version}</td>
        <td style="color:${(r.version_diff ?? 0) > 0 ? '#f59e0b' : '#22c55e'}">${r.version_diff ?? ''}</td>
        <td style="color:${r.critical > 0 ? '#ef4444' : r.high > 0 ? '#f59e0b' : '#666'}">${r.critical}/${r.high}/${r.medium}</td>
        <td style="max-width:220px;word-break:break-word;font-size:9px">${r.eol}</td>
      </tr>
    `).join('')
    
    const html = `
      <!DOCTYPE html>
      <html>
      <head>
        <title>Resources Report - ${new Date().toLocaleDateString()}</title>
        <style>
          body { font-family: Arial, sans-serif; margin: 20px; font-size: 11px; }
          h1 { color: #1e3a5f; font-size: 18px; margin-bottom: 5px; }
          .subtitle { color: #666; font-size: 12px; margin-bottom: 20px; }
          .stats { display: flex; gap: 20px; margin-bottom: 15px; font-size: 11px; }
          .stat { background: #f5f5f5; padding: 8px 12px; border-radius: 4px; }
          table { width: 100%; border-collapse: collapse; font-size: 10px; }
          th { background: #1e3a5f; color: white; padding: 8px 4px; text-align: left; font-weight: 600; }
          td { padding: 6px 4px; border-bottom: 1px solid #ddd; vertical-align: top; }
          tr:nth-child(even) { background: #f9f9f9; }
          tr:hover { background: #f0f0f0; }
          .footer { margin-top: 20px; font-size: 10px; color: #666; text-align: center; }
          @media print {
            body { margin: 10px; }
            .no-print { display: none; }
          }
        </style>
      </head>
      <body>
        <h1>K8s ThirdParty Version Tracker Report</h1>
        <div class="subtitle">Generated: ${new Date().toLocaleString()} | Total: ${expandedRows.length} resources</div>
        <div class="stats">
          <div class="stat"><strong>${expandedRows.filter(r => (r.version_diff ?? 0) > 0).length}</strong> Outdated</div>
          <div class="stat"><strong>${criticalCount}</strong> Critical Vulns</div>
          <div class="stat"><strong>${expandedRows.filter(r => r.replicas === 0).length}</strong> Zero Replicas</div>
          <div class="stat"><strong>${new Set(expandedRows.map(r => r.namespace)).size}</strong> Namespaces</div>
        </div>
        <table>
          <thead>
            <tr>
              <th>Platform</th>
              <th>Product</th>
              <th>Namespace</th>
              <th>Name</th>
              <th>Kind</th>
              <th>Replicas</th>
              <th>Image</th>
              <th>Current</th>
              <th>Latest</th>
              <th>Diff</th>
              <th>C/H/M</th>
              <th>EOL</th>
            </tr>
          </thead>
          <tbody>
            ${tableRows}
          </tbody>
        </table>
        <div class="footer">PatchMgmt - Open Source Resource Tracker</div>
        <div class="no-print" style="margin-top:20px;text-align:center">
          <button onclick="window.print()" style="padding:10px 20px;font-size:14px;cursor:pointer;background:#1e3a5f;color:white;border:none;border-radius:4px">
            📄 Print / Save as PDF
          </button>
        </div>
      </body>
      </html>
    `
    
    printWindow.document.write(html)
    printWindow.document.close()
  }

  const onRefresh = async (backendNames: string[]) => {
    // Validate input
    if (!Array.isArray(backendNames)) {
      console.error('backendNames is not an array:', backendNames)
      setError('Invalid backend selection')
      return
    }
    
    if (backendNames.length === 0) {
      setError('Please select at least one backend to sync')
      return
    }
    
    setLoading(false) // Don't block UI
    setError(null)
    setShowSyncModal(false)
    setSyncProgress(`Sync started on ${backendNames.length} backend(s). Check SYNC page for status.`)
    
    try {
      // Trigger refresh on selected backends (returns immediately)
      const token = sessionStorage.getItem('token')
      if (!token) throw new Error('Not authenticated')
      
      const results = await Promise.allSettled(
        backendNames.map(async (backendName) => {
          const resp = await fetch('/api/federation/trigger-sync', {
            method: 'POST',
            headers: {
              Authorization: `Bearer ${token}`,
              'Content-Type': 'application/json'
            },
            body: JSON.stringify({ backend_name: backendName }),
            signal: AbortSignal.timeout(30000)
          })
          
          if (!resp.ok) {
            throw new Error(`Failed to start sync on ${backendName}: ${resp.statusText}`)
          }
          const data = await resp.json()
          return { backend: backendName, ...data }
        })
      )
      
      // Check for failures
      const failures = results.filter(r => r.status === 'rejected')
      const successes = results.filter(r => r.status === 'fulfilled')
      
      if (failures.length > 0) {
        const errors = failures.map((f: any) => f.reason.message).join('; ')
        setSyncProgress(`Sync started on ${successes.length}/${backendNames.length} backends. Errors: ${errors}`)
        // Auto-dismiss error messages after 15 seconds
        setTimeout(() => setSyncProgress(''), 15000)
      } else {
        const syncIds = successes.map((s: any) => s.value?.sync_log_id).filter(Boolean)
        setSyncProgress(
          `✓ Sync started on ${successes.length} backend(s). ` +
          `View progress in SYNC page. ` +
          `Sync IDs: ${syncIds.join(', ')}`
        )
        // Auto-dismiss success message after 10 seconds
        setTimeout(() => setSyncProgress(''), 10000)
      }
      
    } catch (e: any) {
      setSyncProgress('')
      setError(e.message || String(e))
    }
  }

  // Collect all known managed product names for container-level product detection
  const managedProductNames = useMemo(() => {
    const names = new Set<string>()
    for (const r of resources) {
      const pn = (r.product_name || '').trim()
      if (pn) names.add(pn)
    }
    return Array.from(names)
  }, [resources])

  // Expand each resource into per-container rows if containers are present
  const expandedResources: Resource[] = useMemo(() => {
    const out: any[] = []
    const seen = new Set<string>()
    
    for (const r of resources) {
      const platformValue = (r as any)._platform || r.platform || 'unknown'
      
      const containers: any[] = ((r as any).containers || (r as any).security_info?.containers || []) as any[]
      if (Array.isArray(containers) && containers.length > 0) {
        for (const c of containers) {
          const img = String(c?.image || r.image || '')
          const tag = img && img.includes(':') ? img.split(':').pop() as string : 'latest'
          // Derive product_name from this container's own image
          const containerProduct = detectProductFromImage(img, managedProductNames) || ''
          const cloned: any = { ...r, image: img, current_version: tag, container_name: c?.name || '', platform: platformValue, product_name: containerProduct }
          
          // Create unique key: platform + namespace + name + kind + container
          const uniqueKey = `${platformValue}|${r.namespace || ''}|${r.resource_name || ''}|${r.kind || ''}|${c?.name || ''}`
          
          // Skip if already seen (duplicate from another backend)
          if (seen.has(uniqueKey)) continue
          seen.add(uniqueKey)
          
          // If per-container version data exists, use it
          if (c.latest_version !== undefined) cloned.latest_version = c.latest_version
          if (c.version_diff !== undefined) cloned.version_diff = c.version_diff
          // If per-container EOL exists (in eol_info.eol), use it; otherwise fall back to resource level
          if (c.eol_info && c.eol_info.eol) {
            cloned.eol_date = c.eol_info.eol
          } else if (c.eol_date !== undefined) {
            cloned.eol_date = c.eol_date
          } else {
            cloned.eol_date = r.eol_date || null
          }
          const parentSI: any = (r as any).security_info || {}
          const mergedSI: any = { ...parentSI }
          if (c && c.twistlock) mergedSI.twistlock = c.twistlock
          if (c && c.trivy) mergedSI.trivy = c.trivy
          cloned.security_info = mergedSI
          // Sync advice with container-level version_diff (keep LLM advice if cached)
          if (c.version_diff !== undefined && !mergedSI.llm_advice) {
            const d = c.version_diff
            cloned.advice = d === 'same' ? 'Up to date.'
              : d === 'major' ? 'Plan a major upgrade with testing.'
              : d === 'minor' ? 'Apply patch upgrades during maintenance.'
              : 'Review available versions.'
          }
          out.push(cloned)
        }
      } else {
        // Create unique key for resources without containers
        const uniqueKey = `${platformValue}|${r.namespace || ''}|${r.resource_name || ''}|${r.kind || ''}|`
        
        // Skip if already seen
        if (seen.has(uniqueKey)) continue
        seen.add(uniqueKey)
        
        // Ensure platform is set correctly
        const resourceWithPlatform: any = { ...r, platform: platformValue }
        // Sync advice with version_diff (keep LLM advice if cached)
        const si = (r as any).security_info || {}
        if (r.version_diff && !si.llm_advice) {
          const d = r.version_diff
          resourceWithPlatform.advice = d === 'same' ? 'Up to date.'
            : d === 'major' ? 'Plan a major upgrade with testing.'
            : d === 'minor' ? 'Apply patch upgrades during maintenance.'
            : 'Review available versions.'
        }
        out.push(resourceWithPlatform)
      }
    }
    return out as Resource[]
  }, [resources, managedProductNames])

  // Build filter option lists - dynamically based on current filters (cascading)
  // Helper function to get filtered resources for building dropdown options
  const getFilteredForOptions = (excludeFilter: string) => {
    const riskThreshold = riskMin ? parseInt(riskMin, 10) : null
    const eolCutoff = eolBefore ? new Date(eolBefore) : null
    return expandedResources.filter(r => {
      // Multi-select filters (array-based)
      if (excludeFilter !== 'namespace' && nsFilter.length > 0 && !nsFilter.includes(r.namespace || '')) return false
      if (excludeFilter !== 'product' && productFilter.length > 0) {
        if (!productFilter.includes(r.product_name || '')) return false
      }
      if (excludeFilter !== 'platform' && platformFilter.length > 0 && !platformFilter.includes(r.platform || '')) return false
      if (excludeFilter !== 'kind' && kindFilter.length > 0 && !kindFilter.includes(r.kind || '')) return false
      if (excludeFilter !== 'registry' && registryFilter.length > 0) {
        const img = r.image || ''
        if (!img) return false
        const parts = img.split('/')
        const registry = parts.length === 1 ? 'docker.io' : (parts[0].includes('.') || parts[0].includes(':')) ? parts[0] : 'docker.io'
        if (!registryFilter.includes(registry)) return false
      }
      if (excludeFilter !== 'container' && containerFilter.length > 0) {
        const cn = (r as any).container_name || ''
        if (cn) {
          if (!containerFilter.includes(cn)) return false
        } else {
          const img = (r.image || '').toLowerCase()
          if (!containerFilter.some(cf => img.includes(cf.toLowerCase()))) return false
        }
      }
      if (excludeFilter !== 'versionDiff' && versionDiffFilter.length > 0 && !versionDiffFilter.includes(r.version_diff || '')) return false
      if (excludeFilter !== 'helm' && helmFilter.length > 0 && !helmFilter.includes(r.helm_status || 'none')) return false
      if (excludeFilter !== 'eolStatus' && eolStatusFilter.length > 0 && !eolStatusFilter.includes(eolStatusKey(r))) return false
      if (excludeFilter !== 'current' && currentFilter && !(r.current_version || '').toLowerCase().includes(currentFilter.toLowerCase())) return false
      if (excludeFilter !== 'hasNote' && hasNoteFilter === 'yes' && !r.note) return false
      if (excludeFilter !== 'hasNote' && hasNoteFilter === 'no' && r.note) return false
      // Consider plan as "active" only if date is in the future
      const hasFuturePlan = r.planned_upgrade_at && new Date(r.planned_upgrade_at).getTime() > Date.now()
      if (excludeFilter !== 'hasPlan' && hasPlanFilter === 'yes' && !hasFuturePlan) return false
      if (excludeFilter !== 'hasPlan' && hasPlanFilter === 'no' && hasFuturePlan) return false
      if (excludeFilter !== 'imageRepo' && imageRepoFilter) {
        const img = r.image || ''
        const parts = img.split('/')
        if (parts.length === 1) {
          const imageName = img.split(':')[0]
          if (!imageName.toLowerCase().includes(imageRepoFilter.toLowerCase())) return false
        } else {
          const lastPart = parts[parts.length - 1].split(':')[0]
          const repoPath = parts.slice(1, -1).concat(lastPart).join('/')
          if (!repoPath.toLowerCase().includes(imageRepoFilter.toLowerCase())) return false
        }
      }
      if (excludeFilter !== 'risk' && riskThreshold !== null) {
        const rf = (() => {
          try {
            const tw: any = (r as any).security_info?.twistlock
            return (typeof tw?.riskFactorCount === 'number') ? tw.riskFactorCount : 0
          } catch { return 0 }
        })()
        if (rf < riskThreshold) return false
      }
      if (excludeFilter !== 'eol' && eolCutoff) {
        const eolVal = r.eol_date ? new Date(r.eol_date) : null
        if (!eolVal || eolVal.getTime() > eolCutoff.getTime()) return false
      }
      if (excludeFilter !== 'replicas' && replicasFilter !== '') {
        const target = parseInt(replicasFilter, 10)
        if (!isNaN(target)) {
          if (target >= 3) { if ((r.replicas ?? 0) < 3) return false }
          else { if (r.replicas !== target) return false }
        }
      }
      return true
    })
  }

  const namespaces = useMemo(() => Array.from(new Set(getFilteredForOptions('namespace').map(r => r.namespace).filter(Boolean))).sort(), [expandedResources, productFilter, platformFilter, kindFilter, registryFilter, containerFilter, versionDiffFilter, currentFilter, hasNoteFilter, hasPlanFilter, riskMin, eolBefore, imageRepoFilter, replicasFilter, helmFilter, eolStatusFilter])
  
  // Products: only use explicit product_name field from resources
  // This shows only products that were configured as "managed products" in Admin
  // (product_name is set during sync based on managed products configuration)
  const products = useMemo(() => {
    const productSet = new Set<string>()
    const filtered = getFilteredForOptions('product')
    
    for (const r of filtered) {
      // Only add explicit product_name (from managed products)
      const pn = (r.product_name || '').trim()
      if (pn) productSet.add(pn)
    }
    return Array.from(productSet).sort()
  }, [expandedResources, nsFilter, platformFilter, kindFilter, registryFilter, containerFilter, versionDiffFilter, currentFilter, hasNoteFilter, hasPlanFilter, riskMin, eolBefore, imageRepoFilter, replicasFilter, helmFilter, eolStatusFilter])
  const platforms = useMemo(() => Array.from(new Set(getFilteredForOptions('platform').map(r => r.platform).filter(Boolean))).sort(), [expandedResources, nsFilter, productFilter, kindFilter, registryFilter, containerFilter, versionDiffFilter, currentFilter, hasNoteFilter, hasPlanFilter, riskMin, eolBefore, imageRepoFilter, replicasFilter, helmFilter, eolStatusFilter])
  const kinds = useMemo(() => Array.from(new Set(getFilteredForOptions('kind').map(r => r.kind).filter(Boolean))).sort(), [expandedResources, nsFilter, productFilter, platformFilter, registryFilter, containerFilter, versionDiffFilter, currentFilter, hasNoteFilter, hasPlanFilter, riskMin, eolBefore, imageRepoFilter, replicasFilter, helmFilter, eolStatusFilter])
  const registries = useMemo(() => {
    const regs = getFilteredForOptions('registry').map(r => {
      const img = r.image || ''
      if (!img) return ''
      const parts = img.split('/')
      if (parts.length === 1) return 'docker.io'
      const hasRegistry = parts[0].includes('.') || parts[0].includes(':')
      return hasRegistry ? parts[0] : 'docker.io'
    }).filter(Boolean)
    return Array.from(new Set(regs)).sort()
  }, [expandedResources, nsFilter, productFilter, platformFilter, kindFilter, containerFilter, versionDiffFilter, currentFilter, hasNoteFilter, hasPlanFilter, riskMin, eolBefore, imageRepoFilter, replicasFilter, helmFilter, eolStatusFilter])
  const containers = useMemo(() => Array.from(new Set(getFilteredForOptions('container').map(r => (r as any).container_name).filter(Boolean))).sort(), [expandedResources, nsFilter, productFilter, platformFilter, kindFilter, registryFilter, versionDiffFilter, currentFilter, hasNoteFilter, hasPlanFilter, riskMin, eolBefore, imageRepoFilter, replicasFilter, helmFilter, eolStatusFilter])
  const versionDiffs = useMemo(() => Array.from(new Set(getFilteredForOptions('versionDiff').map(r => r.version_diff).filter(Boolean))).sort(), [expandedResources, nsFilter, productFilter, platformFilter, kindFilter, registryFilter, containerFilter, currentFilter, hasNoteFilter, hasPlanFilter, riskMin, eolBefore, imageRepoFilter, replicasFilter, helmFilter, eolStatusFilter])
  // Helm status options: normalize null -> 'none' to match the badge + filter logic
  const helmStatuses = useMemo(() => Array.from(new Set(getFilteredForOptions('helm').map(r => r.helm_status || 'none'))).sort(), [expandedResources, nsFilter, productFilter, platformFilter, kindFilter, registryFilter, containerFilter, versionDiffFilter, currentFilter, hasNoteFilter, hasPlanFilter, riskMin, eolBefore, imageRepoFilter, replicasFilter, eolStatusFilter])
  const eolStatuses = useMemo(() => Array.from(new Set(getFilteredForOptions('eolStatus').map(eolStatusKey))).sort(), [expandedResources, nsFilter, productFilter, platformFilter, kindFilter, registryFilter, containerFilter, versionDiffFilter, currentFilter, hasNoteFilter, hasPlanFilter, riskMin, eolBefore, imageRepoFilter, replicasFilter, helmFilter])

  // Apply filters
  const filteredResources = useMemo(() => {
    const riskThreshold = riskMin ? parseInt(riskMin, 10) : null
    const eolCutoff = eolBefore ? new Date(eolBefore) : null
    return expandedResources.filter(r => {
      // Multi-select filters (array-based)
      if (nsFilter.length > 0 && !nsFilter.includes(r.namespace || '')) return false
      if (productFilter.length > 0) {
        if (!productFilter.includes(r.product_name || '')) return false
      }
      if (platformFilter.length > 0 && !platformFilter.includes(r.platform || '')) return false
      if (kindFilter.length > 0 && !kindFilter.includes(r.kind || '')) return false
      if (registryFilter.length > 0) {
        const img = r.image || ''
        if (!img) return false
        const parts = img.split('/')
        const registry = parts.length === 1 ? 'docker.io' : (parts[0].includes('.') || parts[0].includes(':')) ? parts[0] : 'docker.io'
        if (!registryFilter.includes(registry)) return false
      }
      if (containerFilter.length > 0) {
        const cn = (r as any).container_name || ''
        if (cn) {
          if (!containerFilter.includes(cn)) return false
        } else {
          const img = (r.image || '').toLowerCase()
          if (!containerFilter.some(cf => img.includes(cf.toLowerCase()))) return false
        }
      }
      if (versionDiffFilter.length > 0 && !versionDiffFilter.includes(r.version_diff || '')) return false
      if (helmFilter.length > 0 && !helmFilter.includes(r.helm_status || 'none')) return false
      if (eolStatusFilter.length > 0 && !eolStatusFilter.includes(eolStatusKey(r))) return false
      if (currentFilter && !(r.current_version || '').toLowerCase().includes(currentFilter.toLowerCase())) return false
      if (hasNoteFilter === 'yes' && !r.note) return false
      if (hasNoteFilter === 'no' && r.note) return false
      // Consider plan as "active" only if date is in the future
      const hasFuturePlanForFilter = r.planned_upgrade_at && new Date(r.planned_upgrade_at).getTime() > Date.now()
      if (hasPlanFilter === 'yes' && !hasFuturePlanForFilter) return false
      if (hasPlanFilter === 'no' && hasFuturePlanForFilter) return false
      if (imageRepoFilter) {
        const img = r.image || ''
        // Extract repo part: everything after registry, before :tag
        // Examples:
        //   docker.io/bitnami/redis:7.0 -> bitnami/redis
        //   ghcr.io/dapr/placement:1.15 -> dapr/placement
        //   registry.example.com/docker-proxy/bitnami/kafka:3.6 -> docker-proxy/bitnami/kafka
        const parts = img.split('/')
        if (parts.length === 1) {
          // No slashes, just image:tag (e.g., centos:7)
          const imageName = img.split(':')[0]
          if (!imageName.toLowerCase().includes(imageRepoFilter.toLowerCase())) return false
        } else {
          // Has registry/repo structure
          // Take everything except first part (registry) and last part's tag
          const lastPart = parts[parts.length - 1].split(':')[0] // Remove tag
          const repoPath = parts.slice(1, -1).concat(lastPart).join('/')
          if (!repoPath.toLowerCase().includes(imageRepoFilter.toLowerCase())) return false
        }
      }
      if (riskThreshold !== null) {
        const rf = (() => {
          try {
            const tw: any = (r as any).security_info?.twistlock
            return (typeof tw?.riskFactorCount === 'number') ? tw.riskFactorCount : 0
          } catch { return 0 }
        })()
        if (rf < riskThreshold) return false
      }
      if (eolCutoff) {
        const eolVal = r.eol_date ? new Date(r.eol_date) : null
        if (!eolVal || eolVal.getTime() > eolCutoff.getTime()) return false
      }
      if (replicasFilter !== '') {
        const target = parseInt(replicasFilter, 10)
        if (!isNaN(target)) {
          if (target >= 3) { if ((r.replicas ?? 0) < 3) return false }
          else { if (r.replicas !== target) return false }
        }
      }
      return true
    })
  }, [expandedResources, nsFilter, productFilter, platformFilter, kindFilter, registryFilter, containerFilter, versionDiffFilter, currentFilter, hasNoteFilter, hasPlanFilter, riskMin, eolBefore, imageRepoFilter, replicasFilter, helmFilter, eolStatusFilter])

  // Apply sorting
  const sortedResources = useMemo(() => {
    if (!sortColumn) return filteredResources
    const sorted = [...filteredResources].sort((a, b) => {
      const aVal = (a as any)[sortColumn]
      const bVal = (b as any)[sortColumn]
      if (aVal === bVal) return 0
      if (aVal == null) return 1
      if (bVal == null) return -1
      const comparison = String(aVal).localeCompare(String(bVal))
      return sortDirection === 'asc' ? comparison : -comparison
    })
    return sorted
  }, [filteredResources, sortColumn, sortDirection])

  // Apply pagination
  const paginatedResources = useMemo(() => {
    const start = (currentPage - 1) * pageSize
    const end = start + pageSize
    return sortedResources.slice(start, end)
  }, [sortedResources, currentPage, pageSize])

  const totalPages = Math.ceil(sortedResources.length / pageSize)

  const handleSort = (column: string) => {
    if (sortColumn === column) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc')
    } else {
      setSortColumn(column)
      setSortDirection('asc')
    }
    setCurrentPage(1)  // Reset to first page when sorting
  }

  const toggleColumn = (column: string) => {
    const newHidden = new Set(hiddenColumns)
    if (newHidden.has(column)) {
      newHidden.delete(column)
    } else {
      newHidden.add(column)
    }
    setHiddenColumns(newHidden)
  }

  const headerCell: React.CSSProperties = { textAlign: 'left', borderBottom: '1px solid var(--border)', padding: 10, background: 'var(--th-bg)', position: 'sticky', top: 0, zIndex: 1, color: 'var(--muted)' }
  const cell: React.CSSProperties = { padding: 10, borderBottom: '1px solid var(--border)', verticalAlign: 'top', wordBreak: 'break-word', whiteSpace: 'normal' }
  const pill = (text: string) => {
    const cls = text === 'major' ? 'pill major' : text === 'minor' ? 'pill minor' : text === 'same' ? 'pill same' : 'pill unknown'
    return <span className={cls}>{diffLabel(text)}</span>
  }
  
  const SortableHeader = ({column, label}: {column: string; label: string}) => (
    <th style={{...headerCell,cursor:'pointer',userSelect:'none'}} onClick={() => handleSort(column)} title={`Sort by ${label}`}>
      {label} {sortColumn === column && <span style={{fontSize:9}}>{sortDirection === 'asc' ? '▲' : '▼'}</span>}
    </th>
  )

  if (!role) {
    return (
      <div className="loginWrap">
        <div className="orb orb1" />
        <div className="orb orb2" />
        <div className="card loginCard">
          <img src={logo5} alt="Logo" className="logoImg" />
          <h2 className="loginTitle">Sign in</h2>
          <p className="loginSubtitle">Authenticate to manage tracked resources</p>
          <div style={{ marginTop: 12 }}>
            <Login setRole={setRole} />
          </div>
        </div>
      </div>
    )
  }

  // Admin route view
  if (role === 'admin' && route === '#/admin') {
    return (
      <div className="layout">
        <Sidebar role={role} username={username} setRole={setRole} />
        <main className="main">
          <div className="container">
            <div className="hero">
              <h1>Admin</h1>
              <p className="muted">Manage users, roles and system settings</p>
            </div>
            <div className="card" style={{ marginBottom: 16 }}>
              <h2 style={{ marginTop: 0 }}>Change Password</h2>
              <ChangePasswordCard />
            </div>
            <div className="card">
              <Admin />
            </div>
          </div>
        </main>
      </div>
    )
  }

  // Dashboard route view (all roles)
  if (route === '#/dashboard') {
    return (
      <div className="layout">
        <Sidebar role={role} username={username} setRole={setRole} />
        <main className="main">
          <div className="container">
            <div className="hero">
              <h1>Dashboard</h1>
              <p className="muted">Visual analytics and insights</p>
            </div>
            <Dashboard sharedResources={resources} />
          </div>
        </main>
      </div>
    )
  }

  // Image Update route view (admin only)
  if (role === 'admin' && route === '#/image-update') {
    return (
      <div className="layout">
        <Sidebar role={role} username={username} setRole={setRole} />
        <main className="main">
          <div className="container">
            <ImageUpdate onResourcesChanged={load} availablePlatformNames={platforms} />
          </div>
        </main>
      </div>
    )
  }

  // SYNC page route (all roles)
  if (route === '#/sync') {
    return (
      <>
        <div className="layout">
          <Sidebar role={role} username={username} setRole={setRole} />
          <main className="main">
            <div className="container">
              <div className="hero">
                <h1>Sync</h1>
                <p className="muted">Synchronize resources and view sync history</p>
              </div>
              
              {/* SYNC Action Card */}
              {role === 'admin' && (
                <div className="card" style={{ marginBottom: 24, padding: 20 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 16 }}>
                    <div>
                      <h3 style={{ margin: 0, marginBottom: 4 }}>Sync Resources</h3>
                      <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                        Press SYNC button to sync all resources for selected platforms
                      </p>
                    </div>
                    <button 
                      className="btn" 
                      onClick={async () => { await loadBackends(); setShowSyncModal(true); }} 
                      style={{ minWidth: 120 }}
                    >
                      🔄 SYNC
                    </button>
                  </div>
                  {syncProgress && (
                    <div style={{ marginTop: 12, padding: 10, background: 'rgba(34,197,94,0.1)', borderRadius: 6, fontSize: 13, color: '#22c55e' }}>
                      {syncProgress}
                    </div>
                  )}
                </div>
              )}
              
              <div className="card">
                <h3 style={{ marginTop: 0, marginBottom: 16 }}>Sync Logs</h3>
                <SyncLogsTable />
              </div>
            </div>
          </main>
        </div>
        
        {/* SYNC Modal - must be included here for this route */}
        {showSyncModal && (
          <SyncModal 
            backends={backends}
            selectedBackends={selectedBackends}
            onSelect={(name, checked) => {
              const newSet = new Set(selectedBackends)
              if (checked) {
                newSet.add(name)
              } else {
                newSet.delete(name)
              }
              setSelectedBackends(newSet)
            }}
            onSync={() => { onRefresh([...selectedBackends]); setShowSyncModal(false) }}
            onClose={() => setShowSyncModal(false)}
          />
        )}
      </>
    )
  }


  // Download page route (all roles)
  if (route === '#/download') {
    // Get unique platforms from resources
    const availablePlatforms = [...new Set(resources.map(r => r.platform))].filter(Boolean).sort()
    
    return (
      <div className="layout">
        <Sidebar role={role} username={username} setRole={setRole} />
        <main className="main">
          <div className="container">
            <div className="hero">
              <h1>Download</h1>
              <p className="muted">Export resources data for selected platforms</p>
            </div>
            
            <DownloadCard resources={resources} availablePlatforms={availablePlatforms} />
          </div>
        </main>
      </div>
    )
  }

  // Compare page route (all roles)
  if (route === '#/compare') {
    return (
      <div className="layout">
        <Sidebar role={role} username={username} setRole={setRole} />
        <main className="main">
          <div className="container">
            <Compare />
          </div>
        </main>
      </div>
    )
  }

  // Landscape page route (all roles)
  if (route === '#/landscape') {
    return (
      <div className="layout">
        <Sidebar role={role} username={username} setRole={setRole} />
        <main className="main">
          <div className="container">
            <Landscape resources={resources} role={role} loading={loading} />
          </div>
        </main>
      </div>
    )
  }

  // Settings page route (all roles)
  if (route === '#/settings') {
    return (
      <div className="layout">
        <Sidebar role={role} username={username} setRole={setRole} />
        <main className="main">
          <Settings />
        </main>
      </div>
    )
  }

  return (
    <div className="layout">
      <Sidebar role={role} username={username} setRole={setRole} />
      <main className="main">
        <div className="container">
          <div className="hero" style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',flexWrap:'wrap',gap:16}}>
            <div>
              <h1>K8s ThirdParty Version Tracker</h1>
              <p className="muted">Track images, versions, and vulnerabilities across your Kubernetes clusters.</p>
            </div>
            <div style={{display:'flex',flexDirection:'column',alignItems:'flex-end',gap:4}}>
              <div style={{display:'flex',alignItems:'center',gap:8}}>
                <span className="muted" style={{fontSize:11}}>Auto-refresh:</span>
                <select 
                  className="input" 
                  value={refreshInterval} 
                  onChange={(e) => setRefreshInterval(Number(e.target.value))}
                  style={{fontSize:11,padding:'4px 8px',minWidth:100}}
                >
                  <option value="0">Off</option>
                  <option value="30">30 seconds</option>
                  <option value="60">1 minute</option>
                  <option value="120">2 minutes</option>
                  <option value="300">5 minutes</option>
                  <option value="600">10 minutes</option>
                </select>
                <button 
                  className="btn secondary" 
                  onClick={() => void load()} 
                  disabled={loading}
                  style={{fontSize:11,padding:'4px 12px',minWidth:80}}
                  title="Refresh now"
                >
                  {loading ? '⟳ ...' : '🔄 Refresh'}
                </button>
              </div>
              <div style={{display:'flex', flexDirection:'column', gap:3}}>
                {lastRefreshTime && (
                  <span className="muted" style={{fontSize:10}}>
                    Last updated: {lastRefreshTime.toLocaleTimeString()}
                  </span>
                )}
                {lastSyncsByBackend.size > 0 && (
                  <div style={{display:'flex', flexDirection:'column', gap:2}}>
                    <span className="muted" style={{fontSize:10}}>Last sync:</span>
                    {Array.from(lastSyncsByBackend.entries()).map(([backend, time]) => (
                      <span 
                        key={backend}
                        className="muted" 
                        style={{fontSize:10, fontWeight:500, paddingLeft:10}} 
                        title={`Last successful sync by ${backend} completed at: ${time.toLocaleString()}`}
                      >
                        {backend}: {time.toLocaleString()}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
          {/* Password change required banner for non-admin users */}
          {sessionStorage.getItem('must_change') === '1' && role !== 'admin' && (
            <div className="card" style={{ marginBottom: 12, background: 'rgba(245, 158, 11, 0.1)', border: '1px solid rgba(245, 158, 11, 0.5)' }}>
              <h3 style={{margin:'0 0 10px 0',fontSize:14,color:'#f59e0b'}}>⚠️ Password Change Required</h3>
              <ChangePasswordCard />
            </div>
          )}
          <div className="card" style={{ marginBottom: 12 }}>
            <h3 style={{margin:'0 0 10px 0',fontSize:14}}>Filters</h3>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10, marginBottom: 10 }}>
              <MultiSelectFilter label="Platform" options={platforms} selected={platformFilter} onChange={setPlatformFilter} searchable />
              <MultiSelectFilter label="Namespace" options={namespaces} selected={nsFilter} onChange={setNsFilter} searchable />
              <MultiSelectFilter label="Product" options={products} selected={productFilter} onChange={setProductFilter} searchable />
              <MultiSelectFilter label="Kind" options={kinds} selected={kindFilter} onChange={setKindFilter} />
              <MultiSelectFilter label="Registry" options={registries} selected={registryFilter} onChange={setRegistryFilter} searchable />
              <MultiSelectFilter label="Container" options={containers} selected={containerFilter} onChange={setContainerFilter} searchable />
              <MultiSelectFilter label="Diff" options={versionDiffs} selected={versionDiffFilter} onChange={setVersionDiffFilter} labelFn={diffLabel} />
              <MultiSelectFilter label="Helm" options={helmStatuses} selected={helmFilter} onChange={setHelmFilter} labelFn={helmLabel} />
              <MultiSelectFilter label="EOL Status" options={eolStatuses} selected={eolStatusFilter} onChange={setEolStatusFilter} labelFn={eolStatusLabel} />
              <div>
                <div className="muted" style={{ fontSize: 10 }}>Replicas</div>
                <select className="input" value={replicasFilter} onChange={(e) => setReplicasFilter(e.target.value)} style={{fontSize:10,padding:'4px 6px',height:26}}>
                  <option value="">All</option>
                  <option value="0">0 (Down)</option>
                  <option value="1">1</option>
                  <option value="2">2</option>
                  <option value="3">3+</option>
                </select>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10 }}>Current</div>
                <input className="input" value={currentFilter} onChange={(e) => setCurrentFilter(e.target.value)} placeholder="1.15" style={{fontSize:10,padding:'4px 6px',height:26}} />
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10 }}>Risk ≥</div>
                <input className="input" type="number" min="0" value={riskMin} onChange={(e) => setRiskMin(e.target.value)} placeholder="10" style={{fontSize:10,padding:'4px 6px',height:26}} />
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10 }}>EOL ≤</div>
                <input className="input" type="date" value={eolBefore} onChange={(e) => setEolBefore(e.target.value)} style={{fontSize:10,padding:'4px 6px',height:26}} />
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10 }}>Note</div>
                <select className="input" value={hasNoteFilter} onChange={(e) => setHasNoteFilter(e.target.value)} style={{fontSize:10,padding:'4px 6px',height:26}}>
                  <option value="">All</option>
                  <option value="yes">Yes</option>
                  <option value="no">No</option>
                </select>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10 }}>Plan</div>
                <select className="input" value={hasPlanFilter} onChange={(e) => setHasPlanFilter(e.target.value)} style={{fontSize:10,padding:'4px 6px',height:26}}>
                  <option value="">All</option>
                  <option value="yes">Yes</option>
                  <option value="no">No</option>
                </select>
              </div>
            </div>
            <div style={{display:'flex',gap:8,alignItems:'center',paddingTop:8,borderTop:'1px solid var(--border)',flexWrap:'wrap'}}>
              <button className="btn secondary" onClick={() => { 
                setNsFilter([]); setProductFilter([]); setPlatformFilter([]); setKindFilter([]); 
                setRegistryFilter(''); setContainerFilter([]); setVersionDiffFilter([]); setHelmFilter([]); setEolStatusFilter([]); setCurrentFilter('');
                setRiskMin(''); setEolBefore(''); setHasNoteFilter(''); setHasPlanFilter(''); setImageRepoFilter(''); setReplicasFilter('');
              }} style={{fontSize:10,padding:'3px 8px'}}>Clear Filters</button>
              <div style={{display:'flex',alignItems:'center',gap:6,flexWrap:'wrap'}}>
                <span style={{fontSize:10,opacity:0.6}}>Columns:</span>
                {['Platform','Namespace','Product','Helm','Name','Kind','Replicas','Container','Image','Current','Latest','Diff','History','Note','Upgrade Plan','EOL','Security'].map(col => (
                  <label key={col} style={{display:'flex',alignItems:'center',gap:3,cursor:'pointer',fontSize:10,opacity: hiddenColumns.has(col) ? 0.45 : 1}}>
                    <input type="checkbox" checked={!hiddenColumns.has(col)} onChange={() => toggleColumn(col)} style={{width:12,height:12,margin:0}} />
                    <span>{col}</span>
                  </label>
                ))}
                <button className="btn secondary" style={{fontSize:9,padding:'1px 5px',marginLeft:4}} onClick={() => setHiddenColumns(new Set())}>All</button>
                <button className="btn secondary" style={{fontSize:9,padding:'1px 5px'}} onClick={() => setHiddenColumns(new Set(['Kind','Replicas','Container','Diff','EOL']))}>Reset</button>
              </div>
              
              {/* Export Filtered Results */}
              <div style={{marginLeft:'auto',display:'flex',gap:6,alignItems:'center'}}>
                <span className="muted" style={{fontSize:10}}>Export filtered:</span>
                <button 
                  className="btn secondary" 
                  onClick={() => downloadFilteredCSV(sortedResources)} 
                  style={{fontSize:10,padding:'3px 8px',display:'flex',alignItems:'center',gap:4}}
                  title="Download filtered results as CSV"
                >
                  📊 CSV
                </button>
                <button 
                  className="btn secondary" 
                  onClick={() => downloadFilteredPDF(sortedResources)} 
                  style={{fontSize:10,padding:'3px 8px',display:'flex',alignItems:'center',gap:4}}
                  title="Download filtered results as PDF"
                >
                  📄 PDF
                </button>
              </div>
              
              <span className="muted" style={{fontSize:10,marginLeft:8}}>Showing <strong>{paginatedResources.length}</strong> of {sortedResources.length} items</span>
            </div>
          </div>
          <div className="card" style={{ marginBottom: 12 }}>
            <h3 style={{margin:'0 0 10px 0',fontSize:14}}>Image Search</h3>
            <div style={{display:'flex',gap:12,alignItems:'flex-end',flexWrap:'wrap'}}>
              <div style={{flex:'1 1 300px',minWidth:200}}>
                <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>Search by Image Repository</div>
                <input 
                  className="input" 
                  value={imageRepoFilter} 
                  onChange={(e) => setImageRepoFilter(e.target.value)} 
                  placeholder="e.g., bitnami, docker-proxy, apache" 
                  style={{fontSize:12,padding:'6px 10px',width:'100%'}} 
                />
                <div className="muted" style={{ fontSize: 10, marginTop: 4 }}>
                  Searches in image repository names (e.g., docker.io/<strong>bitnami</strong>/redis, registry.example.com/<strong>docker-proxy</strong>/kafka)
                </div>
              </div>
              {imageRepoFilter && (
                <button 
                  className="btn secondary" 
                  onClick={() => setImageRepoFilter('')} 
                  style={{fontSize:11,padding:'6px 12px'}}
                >
                  Clear Search
        </button>
              )}
            </div>
          </div>
          {error && <div className="card" style={{ borderColor: 'rgba(239,68,68,.4)', color: '#fecaca', marginBottom: 16 }}>{error}</div>}
          {syncProgress && (
            <div className="card" style={{ marginBottom: 16, background: 'rgba(59,130,246,.15)', borderColor: 'rgba(59,130,246,.3)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, marginBottom: 6, color: 'var(--brand)' }}>{syncProgress}</div>
                  <div style={{ height: 6, background: 'var(--card)', borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{ height: '100%', background: 'var(--brand)', width: syncProgress.includes('%') ? syncProgress.match(/\d+/)?.[0] + '%' : '100%', transition: 'width 0.3s ease' }} />
                  </div>
                </div>
                <button 
                  className="btn secondary" 
                  onClick={() => setSyncProgress('')}
                  title="Dismiss sync notification"
                  style={{ padding: '6px 12px', fontSize: 11, minWidth: 'auto' }}>
                  ✕ Dismiss
                </button>
              </div>
            </div>
          )}
          {loading && resources.length === 0 ? (
            <div className="loading-overlay">
              <div className="loading-spinner" />
              <div className="loading-text">Loading Resources from Platforms</div>
              <div className="loading-sub">Fetching data from all backend endpoints...</div>
            </div>
          ) : (
          <div className="tableWrap">
          <table className="compactTable">
        <thead>
          <tr>
            {!hiddenColumns.has('Platform') && <SortableHeader column="platform" label="Platform" />}
            {!hiddenColumns.has('Namespace') && <SortableHeader column="namespace" label="Namespace" />}
            {!hiddenColumns.has('Product') && <SortableHeader column="product_name" label="Product" />}
            {!hiddenColumns.has('Helm') && <th style={headerCell}>Helm</th>}
            {!hiddenColumns.has('Name') && <SortableHeader column="resource_name" label="Name" />}
            {!hiddenColumns.has('Kind') && <SortableHeader column="kind" label="Kind" />}
            {!hiddenColumns.has('Replicas') && <SortableHeader column="replicas" label="Replicas" />}
            {!hiddenColumns.has('Container') && <SortableHeader column="container_name" label="Container" />}
            {!hiddenColumns.has('Image') && <SortableHeader column="image" label="Image" />}
            {!hiddenColumns.has('Current') && <SortableHeader column="current_version" label="Current" />}
            {!hiddenColumns.has('Latest') && <SortableHeader column="latest_version" label="Latest" />}
            {!hiddenColumns.has('Diff') && <th style={headerCell}>Diff</th>}
            {!hiddenColumns.has('History') && <th style={headerCell}>History</th>}
            {!hiddenColumns.has('Note') && <th style={headerCell}>Note</th>}
            {!hiddenColumns.has('Upgrade Plan') && <th style={headerCell}>Upgrade Plan</th>}
            {!hiddenColumns.has('EOL') && <th style={headerCell}>EOL</th>}
            {!hiddenColumns.has('Security') && <th style={headerCell}>Security</th>}
            <th style={headerCell}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {paginatedResources.map((r) => (
            <tr key={`${r.id}-${(r as any).container_name || 'main'}-${r.image || ''}`} style={{ background: (r.advice && r.advice.includes('missing in cluster')) ? 'rgba(239,68,68,.1)' : undefined }}>
              {!hiddenColumns.has('Platform') && (
                <td 
                  style={{ 
                    ...cell, 
                    maxWidth: '200px',
                    minWidth: '120px',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis'
                  }} 
                  title={r.platform}
                >
                  {r.platform}
                </td>
              )}
              {!hiddenColumns.has('Namespace') && <td style={{ ...cell, whiteSpace: 'nowrap' }} title={r.namespace}>{r.namespace}</td>}
              {!hiddenColumns.has('Product') && <td style={{ ...cell, whiteSpace: 'nowrap' }} title={r.product_name || ''}>{r.product_name || ''}</td>}
              {!hiddenColumns.has('Helm') && <td style={cell}>
                {(() => {
                  const hs = r.helm_status || 'none'
                  const rel = r.helm_release_name ? `Helm release: ${r.helm_release_name}` : ''
                  if (hs === 'drift') return <span className="pill helm-drift" title={`${rel}${rel ? ' — ' : ''}click to see what drifted`} onClick={() => setHelmDriftModal(r)}>Drift</span>
                  if (hs === 'ok') return <span className="pill helm-ok" title={`${rel}${rel ? ' — ' : ''}no drift in patchable fields`}>OK</span>
                  if (hs === 'unknown') return <span className="pill helm-unknown" title="Helm status unknown (not yet computed or release unreadable)">?</span>
                  return <span className="pill helm-none" title="Not Helm-managed">None</span>
                })()}
              </td>}
              {!hiddenColumns.has('Name') && (
                <td 
                  style={{ 
                    ...cell, 
                    maxWidth: '250px',
                    minWidth: '150px',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    cursor: 'help'
                  }} 
                  title={`📋 ${r.resource_name}\n(Click to copy)`}
                  onClick={(e) => {
                    navigator.clipboard.writeText(r.resource_name);
                    const target = e.currentTarget;
                    const original = target.style.background;
                    target.style.background = 'rgba(34, 197, 94, 0.2)';
                    setTimeout(() => { target.style.background = original; }, 200);
                  }}
                >
                  {r.resource_name}
                </td>
              )}
              {!hiddenColumns.has('Kind') && (
                <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                  {r.kind}
                </td>
              )}
              {!hiddenColumns.has('Replicas') && (
                <td 
                  style={{ ...cell, whiteSpace: 'nowrap', textAlign: 'center' }} 
                  className={r.replicas === 0 ? 'replica-zero' : ''}
                  title={r.replicas === 0 ? '⚠️ ZERO REPLICAS - No pods running!' : (r.replicas != null ? `${r.replicas} replica(s)` : 'N/A')}
                >
                  {r.replicas != null ? r.replicas : '-'}
                </td>
              )}
              {!hiddenColumns.has('Container') && <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                {(r as any).container_name
                  ? (r as any).container_name
                  : (Array.isArray((r as any).security_info?.containers)
                      ? ((r as any).security_info.containers.map((c: any) => c?.name || '').filter(Boolean).join(', '))
                      : '')}
              </td>}
              {!hiddenColumns.has('Image') && <td style={{ ...cell, whiteSpace: 'pre-wrap' }}>
                {(r as any).container_name ? (
                  <span><span style={{ color: 'var(--muted)' }}>{(r as any).container_name}: </span>{r.image || ''}</span>
                ) : (
                  Array.isArray((r as any).security_info?.containers) && (r as any).security_info.containers.length > 0 ? (
                    <div>
                      {(r as any).security_info.containers.map((c: any, idx: number) => (
                        <div key={idx}>
                          {c.name ? (<span style={{ color: 'var(--muted)' }}>{c.name}: </span>) : null}{c.image}
                        </div>
                      ))}
                    </div>
                  ) : (
                    r.image || ''
                  )
                )}
              </td>}
              {!hiddenColumns.has('Current') && <td style={cell}>{r.current_version || ''}</td>}
              {!hiddenColumns.has('Latest') && <td style={cell}>{r.latest_version || ''}</td>}
              {!hiddenColumns.has('Diff') && <td style={cell}>{r.version_diff ? pill(r.version_diff) : ''}</td>}
              {!hiddenColumns.has('History') && <td style={cell}>
                {(() => { const histCount = r.history_count ?? 0; return (
                <button
                  className="btn secondary"
                  onClick={() => setHistoryModal(r)}
                  title={histCount > 0
                    ? 'View change history (image/version changes only)'
                    : 'No changes detected yet'}
                  style={{fontSize:9.5,padding:'2px 5px'}}>
                  {histCount > 0 ? `View (${histCount})` : 'None'}
                </button>
                ) })()}
              </td>}
              {!hiddenColumns.has('Note') && <td style={cell}>
                {r.note || role === 'admin' ? (
                  <button 
                    className={r.note ? "btn alert" : "btn secondary"} 
                    onClick={() => setNoteModal(r)} 
                    title={r.note || 'Click to add notes'}
                    style={{fontSize:9.5,padding:'2px 5px',whiteSpace:'nowrap'}}>
                    {r.note ? 'View' : 'Add'}
                  </button>
                ) : (
                  <span style={{fontSize:10,color:'var(--muted)'}}>None</span>
                )}
              </td>}
              {!hiddenColumns.has('Upgrade Plan') && <td style={cell}>
                {(() => {
                  // Check if planned date exists and is in the future
                  const hasFuturePlan = r.planned_upgrade_at && new Date(r.planned_upgrade_at).getTime() > Date.now()
                  
                  if (hasFuturePlan || role === 'admin') {
                    return (
                      <button 
                        className={hasFuturePlan ? "btn alert" : "btn secondary"} 
                        onClick={() => setPlanModal(r)} 
                        title={hasFuturePlan 
                          ? `Planned upgrade: ${new Date(r.planned_upgrade_at!).toLocaleString()}` 
                          : 'Click to set upgrade plan date'}
                        style={{fontSize:9.5,padding:'2px 5px',whiteSpace:'nowrap'}}>
                        {hasFuturePlan ? 'View' : 'Set'}
                      </button>
                    )
                  }
                  return <span style={{fontSize:10,color:'var(--muted)'}}>None</span>
                })()}
              </td>}
              {!hiddenColumns.has('EOL') && <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                {r.eol_date ? (
                  (() => {
                    const eolDate = new Date(r.eol_date)
                    const now = new Date()
                    const isExpired = eolDate.getTime() < now.getTime()
                    return (
                      <span className={isExpired ? 'pill alert' : ''} style={isExpired ? {display:'inline-block',padding:'2px 6px'} : {}}>
                        {r.eol_date}
                      </span>
                    )
                  })()
                ) : r.eol_support_status ? (
                  (() => {
                    const st = r.eol_support_status
                    const label = st === 'supported' ? 'Supported (analyzed by AI)' : st === 'eol' ? 'Outdated (analyzed by AI)' : 'Unknown (analyzed by AI)'
                    const cls = st === 'supported' ? 'pill helm-ok' : st === 'eol' ? 'pill helm-drift' : 'pill helm-unknown'
                    const tip = `${r.eol_support_note || 'No public EOL data.'}\n(AI estimate — click for details; not on endoflife.date.)`
                    return <span className={cls} title={tip} style={{cursor:'pointer'}} onClick={() => setEolAiModal(r)}>{label}</span>
                  })()
                ) : ''}
              </td>}
              {!hiddenColumns.has('Security') && <td style={cell}>
                {(() => {
                  const tw: any = (r as any).security_info?.twistlock
                  const trv: any = (r as any).security_info?.trivy
                  const rfCount = (() => { try { return (typeof tw?.riskFactorCount === 'number') ? tw.riskFactorCount : 0; } catch { return 0; } })()
                  const twDist = tw?.vulnerabilityDistribution
                  const trvDist = trv?.vulnerabilityDistribution
                  const twCrit = twDist?.critical || 0
                  const trvCrit = trvDist?.critical || 0
                  const criticalCount = tw && trv ? Math.max(twCrit, trvCrit) : trv ? trvCrit : twCrit
                  const highCount = (twDist?.high || 0)
                  const mediumCount = (twDist?.medium || 0)
                  
                  const tooltipParts: string[] = []
                  if (twDist) tooltipParts.push(`Twistlock — C:${twCrit} H:${twDist.high||0} M:${twDist.medium||0} L:${twDist.low||0} RF:${rfCount}`)
                  if (trvDist) tooltipParts.push(`Trivy — C:${trvCrit} H:${trvDist.high||0} M:${trvDist.medium||0} L:${trvDist.low||0}`)
                  if (r.advice) tooltipParts.push(`Advice: ${r.advice}`)
                  const tooltipText = tooltipParts.length > 0 ? tooltipParts.join('\n') : 'Click to view security details'
                  
                  const { red, orange, green } = securityThresholds
                  let buttonStyle: React.CSSProperties = {fontSize:9.5,padding:'2px 5px'}
                  
                  if (!tw && !trv) {
                    buttonStyle = {...buttonStyle, background:'#6b7280', color:'#fff', border:'1px solid #6b7280'}
                  } else if (rfCount > red.riskFactor || criticalCount > red.critical) {
                    buttonStyle = {...buttonStyle, background:'#dc2626', color:'#fff', border:'1px solid #dc2626'}
                  } else if (rfCount >= orange.riskFactorMin && rfCount <= orange.riskFactorMax && criticalCount <= orange.critical) {
                    buttonStyle = {...buttonStyle, background:'#d97706', color:'#fff', border:'1px solid #d97706'}
                  } else {
                    buttonStyle = {...buttonStyle, background:'#16a34a', color:'#fff', border:'1px solid #16a34a'}
                  }
                  
                  const isRed = rfCount > red.riskFactor || criticalCount > red.critical
                  return (
                    <button 
                      className="btn"
                      onClick={() => setSecurityModal(r)} 
                      title={tooltipText}
                      style={buttonStyle}>
                      {isRed ? `⚠ C:${criticalCount}` : 'Details'}
                    </button>
                  )
                })()}
              </td>}
              <td style={{...cell, whiteSpace:'nowrap'}}>
                <div style={{display:'flex',gap:2,alignItems:'center'}}>
                  {role === 'admin' && (
                    <button 
                      className="btn secondary"
                      onClick={() => syncSingleResource(r)}
                      disabled={syncingResourceId === r.id}
                      title="Sync this resource now"
                      style={{fontSize:9,padding:'1px 4px',lineHeight:1.2,minWidth:0}}>
                      {syncingResourceId === r.id ? '⏳' : '🔄'}
                    </button>
                  )}
                  {(role === 'admin' || role === 'editor') && r.latest_version && (
                    <button
                      className="btn"
                      onClick={() => askAI(r)}
                      title="Ask AI for upgrade advice"
                      style={{fontSize:9,padding:'1px 4px',lineHeight:1.2,minWidth:0,background:'rgba(99,102,241,0.12)',color:'#818cf8',border:'1px solid rgba(99,102,241,0.25)',borderRadius:4}}>
                      🤖
                    </button>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
        {/* Pagination Controls */}
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'12px 16px',borderTop:'1px solid var(--border)',flexWrap:'wrap',gap:12}}>
          <div style={{display:'flex',gap:8,alignItems:'center'}}>
            <button 
              className="btn secondary" 
              onClick={() => setCurrentPage(p => Math.max(1, p - 1))} 
              disabled={currentPage === 1}
              style={{fontSize:10,padding:'4px 12px'}}>
              Previous
            </button>
            <span style={{fontSize:11,color:'var(--muted)',whiteSpace:'nowrap'}}>
              Page <strong>{currentPage}</strong> of <strong>{totalPages}</strong>
            </span>
            <button 
              className="btn secondary" 
              onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))} 
              disabled={currentPage === totalPages}
              style={{fontSize:10,padding:'4px 12px'}}>
              Next
            </button>
          </div>
          <div style={{display:'flex',gap:8,alignItems:'center'}}>
            <span style={{fontSize:11,color:'var(--muted)'}}>Rows per page:</span>
            <select 
              className="input" 
              value={pageSize} 
              onChange={(e) => { setPageSize(Number(e.target.value)); setCurrentPage(1); }}
              style={{fontSize:10,padding:'3px 8px',width:'auto',minWidth:60}}>
              <option value="20">20</option>
              <option value="50">50</option>
              <option value="100">100</option>
              <option value="200">200</option>
            </select>
          </div>
        </div>
          </div>
          )}
        </div>
      </main>
      {securityModal && <SecurityModal resource={securityModal} onClose={() => setSecurityModal(null)} thresholds={securityThresholds} />}
      {helmDriftModal && <HelmDriftModal resource={helmDriftModal} onClose={() => setHelmDriftModal(null)} />}
      {eolAiModal && <EolAiModal resource={eolAiModal} onClose={() => setEolAiModal(null)} />}
      {aiModal && (
        <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.85)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:999}} onClick={() => setAiModal(null)}>
          <div className="card" style={{maxWidth:'700px',width:'90%',maxHeight:'80vh',overflow:'auto',background:'var(--panel)',boxShadow:'0 20px 60px rgba(0,0,0,.5)'}} onClick={e => e.stopPropagation()}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
              <h2 style={{margin:0}}>
                AI Upgrade Advice
                <span style={{fontSize:10,marginLeft:8,padding:'2px 6px',borderRadius:4,background:'rgba(99,102,241,0.15)',color:'#818cf8'}}>AI</span>
                {aiModal.cached && <span style={{fontSize:10,marginLeft:6,padding:'2px 6px',borderRadius:4,background:'rgba(255,255,255,0.06)',color:'var(--muted)'}}>cached</span>}
              </h2>
              <button className="btn secondary" onClick={() => setAiModal(null)}>Close</button>
            </div>
            <div style={{fontSize:12,color:'var(--muted)',marginBottom:12}}>
              <strong>{aiModal.resource.product_name || aiModal.resource.resource_name}</strong>
              {' — '}{aiModal.resource.image}
              {' — '}{aiModal.resource.current_version} → {aiModal.resource.latest_version || '?'}
            </div>
            {aiModal.loading ? (
              <div style={{textAlign:'center',padding:'40px 0',color:'var(--muted)'}}>
                <div style={{fontSize:24,marginBottom:8}}>⏳</div>
                <div>Asking AI for upgrade advice...</div>
              </div>
            ) : (
              <div style={{fontSize:13,lineHeight:1.7,color:'var(--muted)',whiteSpace:'pre-wrap'}}
                dangerouslySetInnerHTML={{__html: (aiModal.advice || '')
                  .replace(/^## (.+)$/gm, '<strong style="font-size:14px;color:var(--fg)">$1</strong>')
                  .replace(/^### (.+)$/gm, '<strong style="font-size:13px;color:var(--fg)">$1</strong>')
                  .replace(/\*\*(.+?)\*\*/g, '<strong style="color:var(--fg)">$1</strong>')
                  .replace(/`([^`]+)`/g, '<code style="background:rgba(255,255,255,0.06);padding:1px 4px;border-radius:3px;font-size:12px">$1</code>')
                  .replace(/^- (.+)$/gm, '• $1')
                  .replace(/^\d+\. /gm, (m) => m)
                }}
              />
            )}
            {!aiModal.loading && (
              <div style={{marginTop:16,display:'flex',gap:8}}>
                <button className="btn" onClick={() => askAI(aiModal.resource, true)} style={{fontSize:11}}>Refresh (ask again)</button>
              </div>
            )}
          </div>
        </div>
      )}
      {historyModal && <HistoryModal resource={historyModal} onClose={() => setHistoryModal(null)} onJobClick={(jobId) => {
        setHistoryModal(null)
        window.location.hash = '#/image-update'
        setTimeout(() => window.dispatchEvent(new CustomEvent('open-job-detail', { detail: { jobId } })), 100)
      }} />}
      {showSyncModal && (
        <SyncModal 
          backends={backends}
          selectedBackends={selectedBackends}
          onSelect={(name, checked) => {
            const newSet = new Set(selectedBackends)
            if (checked) newSet.add(name)
            else newSet.delete(name)
            setSelectedBackends(newSet)
          }}
          onSync={() => onRefresh(Array.from(selectedBackends))}
          onClose={() => setShowSyncModal(false)}
        />
      )}
      {noteModal && <NoteModal resource={noteModal} onClose={() => { setNoteModal(null); load(); }} readOnly={role !== 'admin'} />}
      {planModal && <PlanModal resource={planModal} onClose={() => { setPlanModal(null); load(); }} readOnly={role !== 'admin'} />}
    </div>
  )
}

function InlineNote({ resourceId, initial, onSaved, readOnly }: { resourceId: number; initial: string; onSaved: () => void; readOnly?: boolean }): JSX.Element {
  const [value, setValue] = useState<string>(initial)
  const [saving, setSaving] = useState<boolean>(false)
  const [status, setStatus] = useState<string>("")
  const hasExisting = initial && initial.length > 0
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const autoResize = () => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }
  useEffect(() => { autoResize() }, [value])
  const save = async () => {
    setSaving(true)
    try {
      const resp = await fetch(`/api/resources/${resourceId}/plan`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note: value }) })
      if (!resp.ok) throw new Error('Save failed')
      const data = await resp.json()
      setValue(data.note || '')
      setStatus('Saved successfully')
      onSaved()
    } catch (e) {
      setStatus('Save failed')
    } finally {
      setSaving(false)
      setTimeout(() => setStatus(''), 2000)
    }
  }
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', whiteSpace: 'nowrap' }}>
      <textarea
        ref={taRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onInput={autoResize}
        placeholder="Add note"
        rows={2}
        wrap="soft"
        style={{ width: 300, resize: 'none', whiteSpace: 'pre-wrap', overflowWrap: 'break-word', overflow: 'hidden' }}
        readOnly={!!readOnly}
      />
      {!readOnly && (
        <button onClick={save} disabled={saving} style={{ background: '#2563eb', color: '#fff', border: 'none', borderRadius: 6, padding: '6px 10px', minWidth: 72, cursor: 'pointer', whiteSpace: 'nowrap' }}>{hasExisting ? (saving ? 'Saving…' : 'Update') : (saving ? 'Saving…' : 'Save')}</button>
      )}
      {status && <span style={{ color: status.includes('failed') ? '#991b1b' : '#166534', fontSize: 12 }}>{status}</span>}
    </div>
  )
}

function InlinePlanDate({ resourceId, initial, onSaved, readOnly }: { resourceId: number; initial: string; onSaved: () => void; readOnly?: boolean }): JSX.Element {
  const [value, setValue] = useState<string>(initial ? initial.slice(0, 16) : '') // YYYY-MM-DDTHH:mm
  const [saving, setSaving] = useState<boolean>(false)
  const [status, setStatus] = useState<string>("")
  const save = async () => {
    setSaving(true)
    try {
      const resp = await fetch(`/api/resources/${resourceId}/plan`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ planned_upgrade_at: value }) })
      if (!resp.ok) throw new Error('Save failed')
      const data = await resp.json()
      setValue(data.planned_upgrade_at ? String(data.planned_upgrade_at).slice(0, 16) : '')
      setStatus('Saved successfully')
      onSaved()
    } catch (e) {
      setStatus('Save failed')
    } finally {
      setSaving(false)
      setTimeout(() => setStatus(''), 2000)
    }
  }
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', whiteSpace: 'nowrap' }}>
      <input type="datetime-local" value={value} onChange={(e) => setValue(e.target.value)} disabled={!!readOnly} />
      {!readOnly && (
        <button onClick={save} disabled={saving} style={{ background: '#2563eb', color: '#fff', border: 'none', borderRadius: 6, padding: '6px 10px', minWidth: 72, cursor: 'pointer', whiteSpace: 'nowrap' }}>{value ? (saving ? 'Saving…' : 'Update') : (saving ? 'Saving…' : 'Save')}</button>
      )}
      {status && <span style={{ color: status.includes('failed') ? '#991b1b' : '#166534', fontSize: 12 }}>{status}</span>}
    </div>
  )
}

function ChangePasswordCard(): JSX.Element {
  const [pw1, setPw1] = useState('')
  const [pw2, setPw2] = useState('')
  const [msg, setMsg] = useState('')
  const save = async () => {
    setMsg('')
    if (pw1 !== pw2) { setMsg('Passwords do not match'); return }
    try {
      const token = sessionStorage.getItem('token') || ''
      const body = new URLSearchParams()
      body.set('new_password', pw1)
      body.set('confirm_password', pw2)
      const resp = await fetch('/api/auth/reset-password', { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' }, body })
      if (!resp.ok) throw new Error('Save failed')
      setMsg('Password updated')
      sessionStorage.removeItem('must_change')
      setPw1(''); setPw2('')
    } catch (e: any) {
      setMsg(e.message || 'Save failed')
    }
  }
  return (
    <div className="inputs" style={{ flexWrap: 'wrap' }}>
      <input className="input" type="password" placeholder="new password" value={pw1} onChange={(e) => setPw1(e.target.value)} />
      <input className="input" type="password" placeholder="confirm password" value={pw2} onChange={(e) => setPw2(e.target.value)} />
      <button className="btn" onClick={save}>Save</button>
      {msg && <span className="muted" style={{ fontSize: 12 }}>{msg}</span>}
    </div>
  )
}

// Multi-select filter dropdown component
function MultiSelectFilter({ 
  label, 
  options, 
  selected, 
  onChange,
  searchable = false,
  labelFn
}: { 
  label: string
  options: string[]
  selected: string[]
  onChange: (values: string[]) => void
  searchable?: boolean
  labelFn?: (v: string) => string
}): JSX.Element {
  const [isOpen, setIsOpen] = useState(false)
  const [searchTerm, setSearchTerm] = useState('')
  const dropdownRef = React.useRef<HTMLDivElement>(null)
  const searchInputRef = React.useRef<HTMLInputElement>(null)
  
  // Close dropdown when clicking outside
  React.useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
        setSearchTerm('')
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])
  
  // Focus search input when dropdown opens
  React.useEffect(() => {
    if (isOpen && searchable && searchInputRef.current) {
      searchInputRef.current.focus()
    }
  }, [isOpen, searchable])
  
  const toggleOption = (option: string) => {
    if (selected.includes(option)) {
      onChange(selected.filter(s => s !== option))
    } else {
      onChange([...selected, option])
    }
  }
  
  const fmt = (v: string) => labelFn ? labelFn(v) : v
  const displayText = selected.length === 0 
    ? 'All' 
    : selected.length === 1 
      ? fmt(selected[0]) 
      : `${selected.length} selected`
  
  // Filter options based on search term
  const filteredOptions = searchable && searchTerm
    ? options.filter(opt => opt.toLowerCase().includes(searchTerm.toLowerCase()))
    : options
  
  return (
    <div ref={dropdownRef} style={{ position: 'relative' }}>
      <div className="muted" style={{ fontSize: 10 }}>{label}</div>
      <button
        className="input"
        onClick={() => setIsOpen(!isOpen)}
        style={{
          fontSize: 10,
          padding: '4px 6px',
          height: 26,
          width: '100%',
          textAlign: 'left',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          cursor: 'pointer',
          background: selected.length > 0 ? 'rgba(139, 92, 246, 0.2)' : undefined,
          borderColor: selected.length > 0 ? 'rgba(139, 92, 246, 0.5)' : undefined,
        }}
      >
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {displayText}
        </span>
        <span style={{ marginLeft: 4 }}>{isOpen ? '▲' : '▼'}</span>
      </button>
      {isOpen && options.length > 0 && (
        <div style={{
          position: 'absolute',
          top: '100%',
          left: 0,
          right: 0,
          minWidth: searchable ? 200 : undefined,
          background: 'var(--panel)',
          border: '1px solid var(--border)',
          borderRadius: 4,
          maxHeight: searchable ? 280 : 200,
          overflow: 'hidden',
          zIndex: 100,
          marginTop: 2,
          display: 'flex',
          flexDirection: 'column',
        }}>
          {/* Search input for searchable filters */}
          {searchable && (
            <div style={{ padding: '6px 8px', borderBottom: '1px solid var(--border)' }}>
              <input
                ref={searchInputRef}
                type="text"
                placeholder="🔍 Search..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                className="input"
                style={{
                  width: '100%',
                  fontSize: 10,
                  padding: '4px 8px',
                  height: 24,
                  background: 'rgba(139, 92, 246, 0.05)',
                  border: '1px solid rgba(139, 92, 246, 0.2)',
                }}
              />
            </div>
          )}
          
          {/* Clear all button */}
          <div 
            style={{ 
              padding: '4px 8px', 
              fontSize: 10, 
              borderBottom: '1px solid var(--border)', 
              cursor: 'pointer', 
              color: 'var(--accent)',
              flexShrink: 0,
            }}
            onClick={() => { onChange([]); setSearchTerm(''); setIsOpen(false) }}
          >
            ✕ Clear all {selected.length > 0 && `(${selected.length})`}
          </div>
          
          {/* Options list */}
          <div style={{ overflow: 'auto', flexGrow: 1 }}>
            {filteredOptions.length === 0 ? (
              <div style={{ padding: '8px', fontSize: 10, color: 'var(--muted)', textAlign: 'center' }}>
                No matches for "{searchTerm}"
              </div>
            ) : (
              filteredOptions.map(opt => (
                <label
                  key={opt}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    padding: '4px 8px',
                    fontSize: 10,
                    cursor: 'pointer',
                    background: selected.includes(opt) ? 'rgba(139, 92, 246, 0.2)' : undefined,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(opt)}
                    onChange={() => toggleOption(opt)}
                    style={{ margin: 0 }}
                  />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {fmt(opt)}
                  </span>
                </label>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}

interface SecurityThresholds {
  red: { riskFactor: number; critical: number };
  orange: { riskFactorMin: number; riskFactorMax: number; critical: number };
  green: { riskFactor: number; critical: number };
}

// EOL value for reports/exports: the real EOL date if known, otherwise the LLM-derived
// support status + note (clearly marked "(AI)") when there is no public EOL data.
function eolExportText(r: any): string {
  if (r && r.eol_date) return r.eol_date
  if (r && r.eol_support_status) {
    return `${r.eol_support_status} (AI)${r.eol_support_note ? ': ' + r.eol_support_note : ''}`
  }
  return ''
}

function HelmDriftModal({ resource, onClose }: { resource: Resource; onClose: () => void }): JSX.Element {
  const [detail, setDetail] = React.useState<any[] | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [releaseName, setReleaseName] = React.useState<string | null>(resource.helm_release_name || null)

  React.useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const token = sessionStorage.getItem('token')
        const backendName = (resource as any)._backend || ''
        const resp = await fetch(`/api/federation/resource-detail?resource_id=${resource.id}&backend_name=${encodeURIComponent(backendName)}`, {
          headers: { Authorization: `Bearer ${token}` }
        })
        if (resp.ok && !cancelled) {
          const data = await resp.json()
          setDetail(Array.isArray(data.helm_drift_detail) ? data.helm_drift_detail : [])
          if (data.helm_release_name) setReleaseName(data.helm_release_name)
        }
      } catch (e) {
        console.error('Failed to load Helm drift detail:', e)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [resource.id])

  const fmt = (v: any) => v === '<absent>' ? '(absent)' : (typeof v === 'string' ? v : JSON.stringify(v))

  return (
    <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.85)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:999}} onClick={onClose}>
      <div className="card" style={{maxWidth:'720px',width:'90%',maxHeight:'80vh',overflow:'auto',background:'var(--panel)',boxShadow:'0 20px 60px rgba(0,0,0,.5)'}} onClick={(e)=>e.stopPropagation()}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
          <h2 style={{margin:0}}>Helm Drift</h2>
          <button className="btn secondary" onClick={onClose}>Close</button>
        </div>
        <p style={{margin:'0 0 8px 0',color:'var(--muted)',fontSize:13}}>
          {resource.resource_name} ({resource.namespace}){releaseName ? ` · release: ${releaseName}` : ''}
        </p>
        <p style={{margin:'0 0 12px 0',color:'var(--muted)',fontSize:12,lineHeight:1.6}}>
          The live manifest differs from what Helm last rendered, in these patchable fields. A
          <code> helm upgrade </code> would reset them to the chart values. (Only patchable fields are
          compared; env values are masked.)
        </p>
        {loading ? (
          <p style={{color:'var(--muted)'}}>Loading…</p>
        ) : (detail && detail.length > 0) ? (
          <table style={{width:'100%',fontSize:12,borderCollapse:'collapse'}}>
            <thead>
              <tr style={{borderBottom:'1px solid var(--border)'}}>
                <th style={{textAlign:'left',padding:4}}>Field</th>
                <th style={{textAlign:'left',padding:4}}>Helm (chart)</th>
                <th style={{textAlign:'left',padding:4}}>Live</th>
              </tr>
            </thead>
            <tbody>
              {detail.map((d: any, i: number) => {
                // Override the global `td{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}`
                // rule so the full field path / values wrap instead of being truncated with "…".
                const wrapCell: React.CSSProperties = {padding:4,whiteSpace:'normal',overflow:'visible',textOverflow:'clip',wordBreak:'break-all',verticalAlign:'top'}
                return (
                <tr key={i} style={{borderBottom:'1px solid var(--border)'}}>
                  <td style={{...wrapCell,fontFamily:'monospace',width:'45%'}}>{d.path}</td>
                  <td style={{...wrapCell,color:'#bbf7d0'}}>{fmt(d.helm)}</td>
                  <td style={{...wrapCell,color:'#fda4af'}}>{fmt(d.live)}</td>
                </tr>
              )})}
            </tbody>
          </table>
        ) : (
          <p style={{color:'var(--muted)'}}>No drift detail available (it may have been resolved since the last refresh).</p>
        )}
      </div>
    </div>
  )
}

function EolAiModal({ resource, onClose }: { resource: Resource, onClose: () => void }): JSX.Element {
  const st = resource.eol_support_status || 'unknown'
  const statusLabel = st === 'supported' ? 'Supported' : st === 'eol' ? 'Outdated' : 'Unknown'
  const cls = st === 'supported' ? 'pill helm-ok' : st === 'eol' ? 'pill helm-drift' : 'pill helm-unknown'
  return (
    <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.85)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:999}} onClick={onClose}>
      <div className="card" style={{maxWidth:'560px',width:'90%',maxHeight:'80vh',overflow:'auto',background:'var(--panel)',boxShadow:'0 20px 60px rgba(0,0,0,.5)'}} onClick={(e)=>e.stopPropagation()}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
          <h2 style={{margin:0}}>End-of-Life — AI analysis</h2>
          <button className="btn secondary" onClick={onClose}>Close</button>
        </div>
        <p style={{margin:'0 0 10px 0',color:'var(--muted)',fontSize:13}}>
          {resource.product_name || resource.resource_name} · {resource.current_version || '—'}
        </p>
        <div style={{margin:'8px 0'}}>
          <span className={cls} style={{display:'inline-block',padding:'3px 10px'}}>{statusLabel}</span>
        </div>
        <p style={{fontSize:13,lineHeight:1.6,whiteSpace:'normal'}}>
          {resource.eol_support_note || 'The model could not determine a clear support status for this version.'}
        </p>
        <p style={{margin:'14px 0 0 0',color:'var(--muted)',fontSize:11,lineHeight:1.5,borderTop:'1px solid var(--border)',paddingTop:10}}>
          This status was inferred by an LLM because the product has no entry on endoflife.date.
          It is an estimate and may be outdated — verify upstream before acting on it.
        </p>
      </div>
    </div>
  )
}


function SecurityModal({ resource, onClose, thresholds }: { resource: Resource; onClose: () => void; thresholds: SecurityThresholds }): JSX.Element {
  const [fullSI, setFullSI] = useState<any>(null)
  const [loadingDetail, setLoadingDetail] = useState(true)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const token = sessionStorage.getItem('token')
        const backendName = (resource as any)._backend || ''
        const resp = await fetch(`/api/federation/resource-detail?resource_id=${resource.id}&backend_name=${encodeURIComponent(backendName)}`, {
          headers: { Authorization: `Bearer ${token}` }
        })
        if (resp.ok && !cancelled) {
          const data = await resp.json()
          setFullSI(data.security_info)
        }
      } catch (e) {
        console.error('Failed to load resource detail:', e)
      } finally {
        if (!cancelled) setLoadingDetail(false)
      }
    })()
    return () => { cancelled = true }
  }, [resource.id])

  const si = fullSI || (resource as any).security_info || {}
  const tw: any = si.twistlock
  const dist = (tw && tw.vulnerabilityDistribution) || {}
  const twCrit = dist.critical ?? 0
  const high = dist.high ?? 0
  const med = dist.medium ?? 0
  const low = dist.low ?? 0
  const total = dist.total ?? (twCrit + high + med + low)
  const vulnText = (tw && (tw.vulnerabilities === null || typeof tw.vulnerabilities === 'undefined')) ? 'null' : (Array.isArray(tw?.vulnerabilities) ? `${tw.vulnerabilities.length} items` : 'present')
  const rfCount = tw?.riskFactorCount ?? 0

  const trv: any = si.trivy
  const trvDist = trv?.vulnerabilityDistribution || {}
  const trvCrit = trvDist.critical ?? 0

  const hasTw = !!tw
  const hasTrv = !!trv
  const crit = hasTw && hasTrv ? Math.max(twCrit, trvCrit) : hasTrv ? trvCrit : twCrit
  const secSource = hasTw && hasTrv ? 'Twistlock + Trivy' : hasTrv ? 'Trivy' : hasTw ? 'Twistlock' : 'None'

  const { red, orange, green } = thresholds
  
  return (
    <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.85)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:999}} onClick={onClose}>
      <div className="card" style={{maxWidth:'700px',width:'90%',maxHeight:'80vh',overflow:'auto',background:'var(--panel)',boxShadow:'0 20px 60px rgba(0,0,0,.5)'}} onClick={(e)=>e.stopPropagation()}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
          <h2 style={{margin:0}}>Security Details</h2>
          <button className="btn secondary" onClick={onClose}>Close</button>
        </div>
        <p style={{margin:'0 0 16px 0',color:'var(--muted)',fontSize:13}}>
          {resource.image ? `Image: ${resource.image}` : `Resource: ${resource.resource_name} (${resource.namespace})`}
        </p>
        <div style={{marginBottom:16}}>
          <h3 style={{margin:'0 0 8px 0',fontSize:14}}>
            Advice
            {(resource as any).security_info?.llm_advice && (
              <span style={{fontSize:10,marginLeft:8,padding:'2px 6px',borderRadius:4,background:'rgba(99,102,241,0.15)',color:'#818cf8'}}>AI</span>
            )}
          </h3>
          {resource.advice ? (
            <div style={{margin:0,color:'var(--muted)',fontSize:13,lineHeight:1.7,whiteSpace:'pre-wrap'}}
              dangerouslySetInnerHTML={{__html: (resource.advice || '')
                .replace(/^## (.+)$/gm, '<strong style="font-size:14px;color:var(--fg)">$1</strong>')
                .replace(/\*\*(.+?)\*\*/g, '<strong style="color:var(--fg)">$1</strong>')
                .replace(/`([^`]+)`/g, '<code style="background:rgba(255,255,255,0.06);padding:1px 4px;border-radius:3px;font-size:12px">$1</code>')
                .replace(/^- (.+)$/gm, '• $1')
              }}
            />
          ) : (
            <p style={{margin:0,color:'var(--muted)'}}>No advice available</p>
          )}
        </div>
        <div style={{marginBottom:16}}>
          <h3 style={{margin:'0 0 8px 0',fontSize:14}}>Twistlock Report</h3>
          {tw ? (
            <div style={{fontSize:12,lineHeight:1.5}}>
              <div><strong>Vulnerabilities:</strong> {vulnText}</div>
              <div><strong>Distribution:</strong> Critical:{twCrit}, High:{high}, Medium:{med}, Low:{low}, Total:{total}</div>
              <div><strong>Risk Factor Count:</strong> {rfCount}</div>
            </div>
          ) : (
            <p style={{margin:0,color:'var(--muted)'}}>No Twistlock data</p>
          )}
        </div>

        {/* Trivy Report */}
        <div style={{marginBottom:16}}>
          <h3 style={{margin:'0 0 8px 0',fontSize:14}}>Trivy Report</h3>
          {trv ? (
            ((trvDist.total ?? 0) === 0) ? (
              <p style={{margin:0,color:'#16a34a'}}>No vulnerabilities found</p>
            ) : (
            <div style={{fontSize:12,lineHeight:1.5}}>
              <div><strong>Distribution:</strong> Critical:{trvDist.critical??0}, High:{trvDist.high??0}, Medium:{trvDist.medium??0}, Low:{trvDist.low??0}, Total:{trvDist.total??0}</div>
              {loadingDetail && !Array.isArray(trv?.vulnerabilities) && (
                <p style={{margin:'8px 0 0',color:'var(--muted)',fontSize:11}}>Loading CVE details...</p>
              )}
              {Array.isArray(trv.vulnerabilities) && trv.vulnerabilities.length > 0 && (
                <details style={{marginTop:8}}>
                  <summary style={{cursor:'pointer',color:'var(--brand)'}}>Show {trv.vulnerabilities.length} vulnerabilities</summary>
                  <div style={{maxHeight:300,overflow:'auto',marginTop:8}}>
                    <table style={{width:'100%',fontSize:11,borderCollapse:'collapse'}}>
                      <thead>
                        <tr style={{borderBottom:'1px solid var(--border)'}}>
                          <th style={{textAlign:'left',padding:4}}>ID</th>
                          <th style={{textAlign:'left',padding:4}}>Severity</th>
                          <th style={{textAlign:'left',padding:4}}>Package</th>
                          <th style={{textAlign:'left',padding:4}}>Installed</th>
                          <th style={{textAlign:'left',padding:4}}>Fixed</th>
                        </tr>
                      </thead>
                      <tbody>
                        {trv.vulnerabilities.slice(0, 100).map((v: any, i: number) => (
                          <tr key={i} style={{borderBottom:'1px solid var(--border)'}}>
                            <td style={{padding:4}}>
                              {v.primaryURL ? <a href={v.primaryURL} target="_blank" rel="noreferrer" style={{color:'var(--brand)',textDecoration:'none'}}>{v.id}</a> : v.id}
                            </td>
                            <td style={{padding:4}}>
                              <span style={{
                                color: v.severity === 'CRITICAL' ? '#dc2626' : v.severity === 'HIGH' ? '#d97706' : v.severity === 'MEDIUM' ? '#eab308' : 'var(--muted)',
                                fontWeight: v.severity === 'CRITICAL' ? 'bold' : 'normal'
                              }}>{v.severity}</span>
                            </td>
                            <td style={{padding:4}}>{v.pkgName}</td>
                            <td style={{padding:4}}>{v.installedVersion}</td>
                            <td style={{padding:4}}>{v.fixedVersion || '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {trv.vulnerabilities.length > 100 && <p style={{fontSize:11,color:'var(--muted)'}}>...and {trv.vulnerabilities.length - 100} more</p>}
                  </div>
                </details>
              )}
            </div>
            )
          ) : (
            <p style={{margin:0,color:'var(--muted)'}}>No Trivy data</p>
          )}
        </div>
        
        {/* Security Status Indicator */}
        <div style={{marginBottom:16}}>
          <h3 style={{margin:'0 0 8px 0',fontSize:14}}>Security Status</h3>
          {(() => {
            let statusColor = '#16a34a' // GREEN
            let statusText = '✓ OK'
            let statusDesc = `Risk Factor < ${green.riskFactor} AND Critical = 0`
            
            if (!hasTw && !hasTrv) {
              statusColor = '#6b7280' // GRAY
              statusText = '— No Data'
              statusDesc = 'No security scan data available'
            } else if (rfCount > red.riskFactor || crit > red.critical) {
              statusColor = '#dc2626' // RED
              statusText = '⚠ CRITICAL'
              statusDesc = `Risk Factor (${rfCount}) > ${red.riskFactor} OR Critical (${crit}) > ${red.critical}`
            } else if (rfCount >= orange.riskFactorMin && rfCount <= orange.riskFactorMax && crit <= orange.critical) {
              statusColor = '#d97706' // ORANGE
              statusText = '⚠ WARNING'
              statusDesc = `Risk Factor (${rfCount}) between ${orange.riskFactorMin}-${orange.riskFactorMax} AND Critical = 0`
            }
            
            return (
              <div style={{
                padding: '12px 16px',
                borderRadius: 8,
                background: statusColor,
                color: '#fff',
                fontSize: 13
              }}>
                <div style={{fontWeight:'bold',fontSize:15,marginBottom:4}}>{statusText}</div>
                <div style={{opacity:0.9}}>{statusDesc}</div>
                <div style={{opacity:0.7,fontSize:11,marginTop:4}}>Source: {secSource}</div>
              </div>
            )
          })()}
          <div style={{marginTop:12,fontSize:11,color:'var(--muted)',lineHeight:1.6}}>
            <div><strong>Color Legend:</strong></div>
            <div style={{display:'flex',alignItems:'center',gap:8,marginTop:4}}>
              <span style={{width:12,height:12,borderRadius:3,background:'#dc2626',display:'inline-block'}}></span>
              <span>RED: Risk Factor &gt; {red.riskFactor} OR Critical &gt; {red.critical}</span>
            </div>
            <div style={{display:'flex',alignItems:'center',gap:8,marginTop:2}}>
              <span style={{width:12,height:12,borderRadius:3,background:'#d97706',display:'inline-block'}}></span>
              <span>ORANGE: Risk Factor {orange.riskFactorMin}-{orange.riskFactorMax} AND Critical = 0</span>
            </div>
            <div style={{display:'flex',alignItems:'center',gap:8,marginTop:2}}>
              <span style={{width:12,height:12,borderRadius:3,background:'#16a34a',display:'inline-block'}}></span>
              <span>GREEN: Risk Factor &lt; {green.riskFactor} AND Critical = 0</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function HistoryModal({ resource, onClose, onJobClick }: { resource: Resource; onClose: () => void; onJobClick?: (jobId: number) => void }): JSX.Element {
  type Hist = NonNullable<Resource['update_history']>
  // History is lazy-loaded on open (kept out of the initial grid payload for speed).
  const [history, setHistory] = React.useState<Hist>(resource.update_history || [])
  const [loading, setLoading] = React.useState<boolean>(true)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    const token = sessionStorage.getItem('token') || ''
    fetch(makeApiUrl(resource, `resources/${resource.id}/history`), {
      headers: { Authorization: `Bearer ${token}` }, cache: 'no-store',
    })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(data => { if (!cancelled) setHistory(Array.isArray(data) ? data : []) })
      .catch(() => { if (!cancelled) setError('Failed to load change history') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [resource.id, resource._backend])

  // Group history by container
  const containerGroups = React.useMemo(() => {
    const groups: Record<string, Hist> = {}
    for (const h of history) {
      const key = h.container_name || '_main'
      if (!groups[key]) groups[key] = []
      groups[key].push(h)
    }
    return groups
  }, [history])

  const containers = Object.keys(containerGroups).sort()
  const [selectedContainer, setSelectedContainer] = React.useState<string>('')
  // Fall back to the first available container once history loads (selection may
  // have been initialised before the async fetch returned).
  const effectiveContainer = (selectedContainer && containerGroups[selectedContainer])
    ? selectedContainer : (containers[0] || '_main')

  const filteredHistory = containerGroups[effectiveContainer] || []

  return (
    <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.85)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:999}} onClick={onClose}>
      <div className="card" style={{maxWidth:'700px',width:'90%',maxHeight:'80vh',overflow:'auto',background:'var(--panel)',boxShadow:'0 20px 60px rgba(0,0,0,.5)'}} onClick={(e)=>e.stopPropagation()}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
          <h2 style={{margin:0}}>Change History</h2>
          <button className="btn secondary" onClick={onClose}>Close</button>
        </div>
        <div>
          <p style={{margin:'0 0 12px 0',color:'var(--muted)',fontSize:13}}><strong>{resource.resource_name}</strong> ({resource.namespace})</p>
          <p style={{margin:'0 0 12px 0',color:'var(--muted)',fontSize:11,fontStyle:'italic'}}>Shows only when image or version actually changed for each container</p>

          {loading ? (
            <p style={{margin:0,color:'var(--muted)'}}>Loading history…</p>
          ) : error ? (
            <p style={{margin:0,color:'#ef4444'}}>{error}</p>
          ) : (<>
          {/* Container tabs */}
          {containers.length > 1 && (
            <div style={{display:'flex',gap:8,marginBottom:16,flexWrap:'wrap'}}>
              {containers.map(c => (
                <button
                  key={c}
                  className={effectiveContainer === c ? "btn" : "btn secondary"}
                  onClick={() => setSelectedContainer(c)}
                  style={{fontSize:10,padding:'4px 10px'}}
                >
                  {c === '_main' ? '(main)' : c}
                  <span style={{marginLeft:6,opacity:0.6}}>({containerGroups[c]?.length || 0})</span>
                </button>
              ))}
            </div>
          )}
          
          {/* Single container label */}
          {containers.length === 1 && containers[0] !== '_main' && (
            <p style={{margin:'0 0 12px 0',color:'var(--accent)',fontSize:11}}>
              Container: <strong>{containers[0]}</strong>
            </p>
          )}
          
          {filteredHistory.length > 0 ? (
            <table style={{width:'100%',fontSize:11}}>
              <thead>
                <tr style={{borderBottom:'1px solid var(--border)'}}>
                  <th style={{textAlign:'left',padding:'8px 4px'}}>Version</th>
                  <th style={{textAlign:'left',padding:'8px 4px'}}>Latest</th>
                  <th style={{textAlign:'left',padding:'8px 4px'}}>Diff</th>
                  <th style={{textAlign:'left',padding:'8px 4px'}}>Source</th>
                  <th style={{textAlign:'left',padding:'8px 4px'}}>Changed At</th>
                </tr>
              </thead>
              <tbody>
                {filteredHistory.map((h, idx) => (
                  <tr key={idx} style={{borderBottom:'1px solid var(--border)'}}>
                    <td style={{padding:'8px 4px',fontFamily:'monospace'}}>{h.version || 'N/A'}</td>
                    <td style={{padding:'8px 4px',fontFamily:'monospace',color:'var(--muted)'}}>{h.latest_version || '-'}</td>
                    <td style={{padding:'8px 4px'}}>
                      {h.version_diff && (
                        <span style={{
                          padding:'2px 6px',
                          borderRadius:4,
                          fontSize:9,
                          background: h.version_diff === 'same' ? 'rgba(34, 197, 94, 0.3)' : 
                                     h.version_diff === 'patch' ? 'rgba(59, 130, 246, 0.3)' :
                                     h.version_diff === 'minor' ? 'rgba(249, 115, 22, 0.3)' :
                                     h.version_diff === 'major' ? 'rgba(239, 68, 68, 0.3)' : 'rgba(107, 114, 128, 0.3)'
                        }}>
                          {diffLabel(h.version_diff!)}
                        </span>
                      )}
                    </td>
                    <td style={{padding:'8px 4px',fontSize:10}}>
                      {h.source === 'job' ? (
                        h.job_id && onJobClick ? (
                          <span
                            style={{color:'#a855f7',cursor:'pointer',textDecoration:'underline'}}
                            onClick={() => onJobClick(h.job_id!)}
                            title={`Open Job #${h.job_id} details`}
                          >
                            Job #{h.job_id}
                          </span>
                        ) : (
                          <span style={{color:'#a855f7'}}>
                            Job {h.job_id ? `#${h.job_id}` : ''}
                          </span>
                        )
                      ) : h.source === 'watch' ? (
                        <span style={{color:'#22c55e'}} title="Detected in real-time by the Kubernetes watch">Watch</span>
                      ) : h.source === 'cron' ? (
                        <span style={{color:'var(--muted)'}} title="Nightly scheduled sync">Cron</span>
                      ) : h.source === 'sync' ? (
                        <span style={{color:'var(--muted)'}}>Sync</span>
                      ) : h.source === 'sync-backfill' ? (
                        <span
                          style={{color:'#3b82f6'}}
                          title="Auto-detected gap and filled retroactively by sync"
                        >
                          Sync (backfill)
                        </span>
                      ) : h.source === 'manual' ? (
                        <span style={{color:'#f59e0b'}}>Manual</span>
                      ) : (
                        <span style={{color:'var(--muted)'}}>-</span>
                      )}
                    </td>
                    <td style={{padding:'8px 4px',fontSize:10,color:'var(--muted)'}}>{h.checked_at ? new Date(h.checked_at).toLocaleString() : 'N/A'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p style={{margin:0,color:'var(--muted)'}}>No history available for this container</p>
          )}
          </>)}
        </div>
      </div>
    </div>
  )
}

function NoteModal({ resource, onClose, readOnly }: { resource: Resource; onClose: () => void; readOnly?: boolean }): JSX.Element {
  const [value, setValue] = useState<string>(resource.note || '')
  const [saving, setSaving] = useState<boolean>(false)
  const [status, setStatus] = useState<string>('')
  const [applyToAll, setApplyToAll] = useState<boolean>(false)

  const containerImages: string[] = useMemo(() => {
    const imgs: string[] = []
    const containers = (resource.security_info as any)?.containers
    if (Array.isArray(containers)) {
      for (const c of containers) {
        if (c.image && !imgs.includes(c.image)) imgs.push(c.image)
      }
    }
    if (!imgs.length && resource.image) imgs.push(resource.image)
    return imgs
  }, [resource])

  const [matchImage, setMatchImage] = useState<string>(containerImages[0] || resource.image || '')
  
  const save = async () => {
    setSaving(true)
    setStatus('')
    try {
      const token = sessionStorage.getItem('token') || ''
      const payload: any = { note: value }
      if (applyToAll) {
        payload.apply_to_all = true
        payload.match_image = matchImage
      }
      const resp = await fetch(makeApiUrl(resource, `resources/${resource.id}/plan`), { method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` }, body: JSON.stringify(payload) })
      if (!resp.ok) throw new Error('Save failed')
      const data = await resp.json()
      const msg = data.updated_count ? `Saved to ${data.updated_count} resources` : 'Saved successfully'
      setStatus(msg)
      setTimeout(() => onClose(), 1500)
    } catch (e) {
      setStatus('Save failed')
    } finally {
      setSaving(false)
    }
  }
  
  return (
    <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.85)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:999}} onClick={onClose}>
      <div className="card" style={{maxWidth:'600px',width:'90%',maxHeight:'80vh',overflow:'auto',background:'var(--panel)',boxShadow:'0 20px 60px rgba(0,0,0,.5)'}} onClick={(e)=>e.stopPropagation()}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
          <h2 style={{margin:0}}>Note</h2>
          <button className="btn secondary" onClick={onClose}>Close</button>
        </div>
        <div>
          <p style={{margin:'0 0 12px 0',color:'var(--muted)',fontSize:13}}><strong>{resource.resource_name}</strong> ({resource.namespace})</p>
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="Add a note..."
            rows={8}
            style={{width:'100%',background:'var(--input-bg)',border:'1px solid var(--border)',color:'var(--text)',borderRadius:8,padding:'8px 10px',outline:'none',resize:'vertical',minHeight:120}}
            readOnly={!!readOnly}
          />
          {!readOnly && (
            <>
              <div style={{marginTop:10}}>
                <label style={{display:'flex',alignItems:'center',gap:6,fontSize:12,color:'var(--muted)',cursor:'pointer'}}>
                  <input type="checkbox" checked={applyToAll} onChange={(e) => setApplyToAll(e.target.checked)} style={{width:14,height:14}} />
                  Apply to all resources with same image on <strong style={{color:'var(--text)'}}>{resource.platform}</strong>
                </label>
                {applyToAll && (
                  <div style={{marginTop:6,marginLeft:20}}>
                    {containerImages.length > 1 ? (
                      <select value={matchImage} onChange={(e) => setMatchImage(e.target.value)} style={{width:'100%',background:'var(--input-bg)',border:'1px solid var(--border)',color:'var(--text)',borderRadius:6,padding:'4px 8px',fontSize:11,fontFamily:'monospace'}}>
                        {containerImages.map(img => <option key={img} value={img}>{img}</option>)}
                      </select>
                    ) : (
                      <span style={{fontFamily:'monospace',fontSize:11,color:'var(--muted)'}}>{matchImage}</span>
                    )}
                  </div>
                )}
              </div>
              <div style={{marginTop:10,display:'flex',gap:8,alignItems:'center'}}>
                <button className="btn" onClick={save} disabled={saving}>{saving ? 'Saving...' : 'Save'}</button>
                {status && <span style={{color:status.includes('failed')?'#991b1b':'#166534',fontSize:12}}>{status}</span>}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function PlanModal({ resource, onClose, readOnly }: { resource: Resource; onClose: () => void; readOnly?: boolean }): JSX.Element {
  // Helper: Convert UTC date string to local datetime-local format (YYYY-MM-DDTHH:MM)
  const utcToLocalInput = (utcStr: string): string => {
    const date = new Date(utcStr)
    // Get local date/time components
    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    const hours = String(date.getHours()).padStart(2, '0')
    const minutes = String(date.getMinutes()).padStart(2, '0')
    return `${year}-${month}-${day}T${hours}:${minutes}`
  }
  
  // Helper: Convert local datetime-local value to ISO string for backend
  const localInputToIso = (localStr: string): string => {
    if (!localStr) return ''
    // datetime-local input gives us local time, create Date and convert to ISO
    const date = new Date(localStr)
    return date.toISOString()
  }
  
  // Only show existing value if date is in the future; past dates are treated as cleared
  // Convert UTC to local time for display
  const initialValue = resource.planned_upgrade_at && new Date(resource.planned_upgrade_at).getTime() > Date.now()
    ? utcToLocalInput(resource.planned_upgrade_at) 
    : ''
  const [value, setValue] = useState<string>(initialValue)
  const [saving, setSaving] = useState<boolean>(false)
  const [status, setStatus] = useState<string>('')
  const [applyToAll, setApplyToAll] = useState<boolean>(false)

  const containerImages: string[] = useMemo(() => {
    const imgs: string[] = []
    const containers = (resource.security_info as any)?.containers
    if (Array.isArray(containers)) {
      for (const c of containers) {
        if (c.image && !imgs.includes(c.image)) imgs.push(c.image)
      }
    }
    if (!imgs.length && resource.image) imgs.push(resource.image)
    return imgs
  }, [resource])

  const [matchImage, setMatchImage] = useState<string>(containerImages[0] || resource.image || '')

  const save = async () => {
    setSaving(true)
    setStatus('')
    try {
      const isoValue = localInputToIso(value)
      
      const token = sessionStorage.getItem('token') || ''
      const payload: any = { planned_upgrade_at: isoValue }
      if (applyToAll) {
        payload.apply_to_all = true
        payload.match_image = matchImage
      }
      const resp = await fetch(makeApiUrl(resource, `resources/${resource.id}/plan`), { 
        method: 'POST', 
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` }, 
        body: JSON.stringify(payload) 
      })
      if (!resp.ok) throw new Error('Save failed')
      const data = await resp.json()
      const msg = data.updated_count ? `Saved to ${data.updated_count} resources` : 'Saved successfully'
      setStatus(msg)
      setTimeout(() => onClose(), 1500)
    } catch (e) {
      setStatus('Save failed')
    } finally {
      setSaving(false)
    }
  }
  
  return (
    <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.85)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:999}} onClick={onClose}>
      <div className="card" style={{maxWidth:'500px',width:'90%',maxHeight:'80vh',overflow:'auto',background:'var(--panel)',boxShadow:'0 20px 60px rgba(0,0,0,.5)'}} onClick={(e)=>e.stopPropagation()}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
          <h2 style={{margin:0}}>Upgrade Plan</h2>
          <button className="btn secondary" onClick={onClose}>Close</button>
        </div>
        <div>
          <div style={{marginBottom:16}}>
            <div style={{fontSize:14,marginBottom:4}}><strong>{resource.resource_name}</strong></div>
            <div style={{fontSize:12,color:'var(--muted)',marginBottom:4}}>
              Namespace: <strong>{resource.namespace}</strong>
              <span style={{marginLeft:12}}>Kind: <strong>{resource.kind}</strong></span>
            </div>
            {(() => {
              const containers = (resource.security_info as any)?.containers || []
              if (containers.length > 1) {
                return (
                  <div style={{fontSize:11,marginBottom:6}}>
                    <div style={{color:'var(--muted)',marginBottom:4}}>Containers:</div>
                    {containers.map((c: any, idx: number) => {
                      const matchesProduct = resource.product_name && 
                        (c.image || '').toLowerCase().includes(resource.product_name.toLowerCase())
                      return (
                        <div key={idx} style={{
                          padding:'4px 8px',
                          marginBottom:2,
                          background: matchesProduct ? 'rgba(245,158,11,.15)' : 'rgba(255,255,255,.03)',
                          borderRadius:4,
                          border: matchesProduct ? '1px solid rgba(245,158,11,.4)' : '1px solid transparent'
                        }}>
                          <span style={{color:'#9ca3af'}}>{c.name}: </span>
                          <span style={{fontFamily:'monospace',color: matchesProduct ? '#f59e0b' : '#3b82f6'}}>
                            {c.image || 'N/A'}
                          </span>
                          {matchesProduct && <span style={{fontSize:9,marginLeft:6,color:'#f59e0b'}}>★ matches product</span>}
                        </div>
                      )
                    })}
                  </div>
                )
              } else if (resource.image) {
                return (
                  <div style={{fontSize:11,color:'var(--muted)',marginBottom:4}}>
                    Image: <span style={{fontFamily:'monospace',color:'#3b82f6'}}>{resource.image}</span>
                  </div>
                )
              }
              return null
            })()}
            {resource.product_name && (
              <div style={{fontSize:11,color:'var(--muted)'}}>
                Tagged Product: <strong style={{color:'#f59e0b'}}>{resource.product_name}</strong>
                <span style={{fontSize:10,marginLeft:8,opacity:0.7}}>(database tag)</span>
              </div>
            )}
          </div>
          <div style={{marginBottom:12}}>
            <label style={{display:'block',marginBottom:6,fontSize:13,color:'var(--muted)'}}>Planned Upgrade Date & Time</label>
            <input
              type="datetime-local"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              style={{width:'100%',background:'var(--input-bg)',border:'1px solid var(--border)',color:'var(--text)',borderRadius:8,padding:'8px 10px',outline:'none'}}
              disabled={!!readOnly}
            />
            <div style={{fontSize:10,color:'var(--muted)',marginTop:6}}>
              Enter time in your local timezone ({Intl.DateTimeFormat().resolvedOptions().timeZone})
            </div>
            {value && (
              <div style={{fontSize:11,marginTop:6,padding:'6px 8px',background:'rgba(59,130,246,.1)',borderRadius:4}}>
                <span style={{color:'var(--muted)'}}>Scheduled: </span>
                <span style={{color:'#3b82f6'}}>{new Date(value).toLocaleString()} (local)</span>
                <span style={{color:'var(--muted)'}}> = </span>
                <span style={{color:'#22c55e'}}>{new Date(value).toISOString().replace('T', ' ').slice(0, 19)} UTC</span>
              </div>
            )}
          </div>
          {!readOnly && (
            <>
              <div style={{marginTop:4,marginBottom:10}}>
                <label style={{display:'flex',alignItems:'center',gap:6,fontSize:12,color:'var(--muted)',cursor:'pointer'}}>
                  <input type="checkbox" checked={applyToAll} onChange={(e) => setApplyToAll(e.target.checked)} style={{width:14,height:14}} />
                  Apply to all resources with same image on <strong style={{color:'var(--text)'}}>{resource.platform}</strong>
                </label>
                {applyToAll && (
                  <div style={{marginTop:6,marginLeft:20}}>
                    {containerImages.length > 1 ? (
                      <select value={matchImage} onChange={(e) => setMatchImage(e.target.value)} style={{width:'100%',background:'var(--input-bg)',border:'1px solid var(--border)',color:'var(--text)',borderRadius:6,padding:'4px 8px',fontSize:11,fontFamily:'monospace'}}>
                        {containerImages.map(img => <option key={img} value={img}>{img}</option>)}
                      </select>
                    ) : (
                      <span style={{fontFamily:'monospace',fontSize:11,color:'var(--muted)'}}>{matchImage}</span>
                    )}
                  </div>
                )}
              </div>
              <div style={{display:'flex',gap:8,alignItems:'center'}}>
                <button className="btn" onClick={save} disabled={saving}>{saving ? 'Saving...' : 'Save'}</button>
                {status && <span style={{color:status.includes('failed')?'#991b1b':'#166534',fontSize:12}}>{status}</span>}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// Download Card Component for Download Page
function DownloadCard({ resources, availablePlatforms }: { resources: any[], availablePlatforms: string[] }): JSX.Element {
  const [selectedPlatforms, setSelectedPlatforms] = useState<Set<string>>(new Set())
  const [selectAll, setSelectAll] = useState<boolean>(false)
  const [format, setFormat] = useState<'csv' | 'pdf'>('csv')
  const [downloading, setDownloading] = useState<boolean>(false)
  
  const togglePlatform = (platform: string) => {
    const newSet = new Set(selectedPlatforms)
    if (newSet.has(platform)) {
      newSet.delete(platform)
    } else {
      newSet.add(platform)
    }
    setSelectedPlatforms(newSet)
    setSelectAll(newSet.size === availablePlatforms.length)
  }
  
  const toggleSelectAll = () => {
    if (selectAll) {
      setSelectedPlatforms(new Set())
      setSelectAll(false)
    } else {
      setSelectedPlatforms(new Set(availablePlatforms))
      setSelectAll(true)
    }
  }
  
  // Collect managed product names for image-based product detection
  const dlManagedProducts = useMemo(() => {
    const names = new Set<string>()
    for (const r of resources) {
      const pn = (r.product_name || '').trim()
      if (pn) names.add(pn)
    }
    return Array.from(names)
  }, [resources])

  // Filter resources by selected platforms
  const getFilteredResources = () => {
    if (selectedPlatforms.size === 0) return []
    
    const expanded: any[] = []
    resources.forEach(r => {
      if (!selectedPlatforms.has(r.platform)) return
      
      const containers = r.security_info?.containers || []
      if (containers.length > 0) {
        containers.forEach((c: any) => {
          let currentVer = c.current_version
          if (!currentVer && c.image) {
            const parts = c.image.split(':')
            currentVer = parts.length > 1 ? parts[parts.length - 1] : ''
          }
          const containerProduct = detectProductFromImage(c.image || '', dlManagedProducts) || ''
          
          expanded.push({
            platform: r.platform,
            namespace: r.namespace,
            resource_name: r.resource_name,
            kind: r.kind,
            container_name: c.name,
            image: c.image,
            current_version: currentVer,
            latest_version: c.latest_version,
            version_diff: c.version_diff,
            note: r.note,
            planned_upgrade_at: r.planned_upgrade_at,
            eol_date: c.eol_date,
            product_name: containerProduct,
            twistlock: c.twistlock,
            trivy: c.trivy,
          })
        })
      } else {
        // Extract current version from image if not set
        let currentVer = r.current_version
        if (!currentVer && r.image) {
          const parts = r.image.split(':')
          currentVer = parts.length > 1 ? parts[parts.length - 1] : ''
        }
        
        expanded.push({
          platform: r.platform,
          namespace: r.namespace,
          resource_name: r.resource_name,
          kind: r.kind,
          container_name: '',
          image: r.image,
          current_version: currentVer,
          latest_version: r.latest_version,
          version_diff: r.version_diff,
          note: r.note,
          planned_upgrade_at: r.planned_upgrade_at,
          eol_date: r.eol_date,
          product_name: r.product_name,
          twistlock: r.security_info?.twistlock,
          trivy: r.security_info?.trivy,
        })
      }
    })
    return expanded
  }
  
  const downloadCSV = (data: any[]) => {
    // Column order: platform, product_name first, then rest
    const header = [
      "platform","product_name","namespace","name","kind","container","image","current","latest","diff",
      "note","upgrade_plan","eol",
      "twistlock_critical","twistlock_high","twistlock_medium","twistlock_low","twistlock_total",
      "trivy_critical","trivy_high","trivy_medium","trivy_low","trivy_total"
    ]
    
    const rows = [header]
    data.forEach(r => {
      const tw = r.twistlock || {}
      const dist = tw.vulnerabilityDistribution || {}
      const trv = r.trivy || {}
      const td = trv.vulnerabilityDistribution || {}
      rows.push([
        r.platform || '', 
        r.product_name || '',
        r.namespace || '', 
        r.resource_name || '', 
        r.kind || '',
        r.container_name || '', 
        r.image || '', 
        r.current_version || '',
        r.latest_version || '', 
        r.version_diff ?? '',
        r.note || '',
        r.planned_upgrade_at || '',
        eolExportText(r),
        dist.critical ?? 0,
        dist.high ?? 0,
        dist.medium ?? 0,
        dist.low ?? 0,
        dist.total ?? 0,
        td.critical ?? 0,
        td.high ?? 0,
        td.medium ?? 0,
        td.low ?? 0,
        td.total ?? 0
      ])
    })
    
    const csv = rows.map(row => row.map(cell => 
      typeof cell === 'string' && (cell.includes(',') || cell.includes('"') || cell.includes('\n'))
        ? `"${cell.replace(/"/g, '""')}"`
        : cell
    ).join(',')).join('\n')
    
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    const platforms = [...selectedPlatforms].join('_')
    link.download = `resources_${platforms}_${new Date().toISOString().split('T')[0]}.csv`
    link.click()
  }
  
  const downloadPDF = (data: any[]) => {
    const printWindow = window.open('', '_blank')
    if (!printWindow) {
      alert('Please allow popups to download PDF')
      return
    }
    
    const tableRows = data.map(r => {
      const tw = r.twistlock || {}
      const dist = tw.vulnerabilityDistribution || {}
      const critical = dist.critical ?? 0
      const high = dist.high ?? 0
      const medium = dist.medium ?? 0
      return `
        <tr>
          <td>${r.platform || ''}</td>
          <td>${r.product_name || ''}</td>
          <td>${r.namespace || ''}</td>
          <td>${r.resource_name || ''}</td>
          <td>${r.kind || ''}</td>
          <td style="text-align:center;${(r.replicas ?? 0) === 0 ? 'color:#ef4444;font-weight:bold' : ''}">${r.replicas ?? '-'}</td>
          <td style="max-width:200px;word-break:break-all;font-size:9px">${r.image || ''}</td>
          <td>${r.current_version || ''}</td>
          <td>${r.latest_version || ''}</td>
          <td style="color:${(r.version_diff ?? 0) > 0 ? '#f59e0b' : '#22c55e'}">${r.version_diff ?? ''}</td>
          <td style="color:${critical > 0 ? '#ef4444' : high > 0 ? '#f59e0b' : '#666'}">${critical}/${high}/${medium}</td>
          <td style="max-width:220px;word-break:break-word;font-size:9px">${eolExportText(r)}</td>
        </tr>
      `
    }).join('')
    
    const platformList = [...selectedPlatforms].join(', ')
    
    const html = `
      <!DOCTYPE html>
      <html>
      <head>
        <title>Resources Report - ${new Date().toLocaleDateString()}</title>
        <style>
          body { font-family: Arial, sans-serif; margin: 20px; font-size: 11px; }
          h1 { color: #1e3a5f; font-size: 18px; margin-bottom: 5px; }
          .subtitle { color: #666; font-size: 12px; margin-bottom: 20px; }
          .stats { display: flex; gap: 20px; margin-bottom: 15px; font-size: 11px; flex-wrap: wrap; }
          .stat { background: #f5f5f5; padding: 8px 12px; border-radius: 4px; }
          table { width: 100%; border-collapse: collapse; font-size: 10px; }
          th { background: #1e3a5f; color: white; padding: 8px 4px; text-align: left; font-weight: 600; }
          td { padding: 6px 4px; border-bottom: 1px solid #ddd; vertical-align: top; }
          tr:nth-child(even) { background: #f9f9f9; }
          tr:hover { background: #f0f0f0; }
          .footer { margin-top: 20px; font-size: 10px; color: #666; text-align: center; }
          @media print {
            body { margin: 10px; }
            .no-print { display: none; }
          }
        </style>
      </head>
      <body>
        <h1>K8s ThirdParty Version Tracker Report</h1>
        <div class="subtitle">
          Generated: ${new Date().toLocaleString()}<br/>
          Platforms: ${platformList}<br/>
          Total: ${data.length} resources
        </div>
        <div class="stats">
          <div class="stat"><strong>${data.filter(r => (r.version_diff ?? 0) > 0).length}</strong> Outdated</div>
          <div class="stat"><strong>${data.filter(r => (r.twistlock?.vulnerabilityDistribution?.critical ?? 0) > 0).length}</strong> Critical Vulns</div>
          <div class="stat"><strong>${new Set(data.map(r => r.namespace)).size}</strong> Namespaces</div>
          <div class="stat"><strong>${selectedPlatforms.size}</strong> Platforms</div>
        </div>
        <table>
          <thead>
            <tr>
              <th>Platform</th>
              <th>Product</th>
              <th>Namespace</th>
              <th>Name</th>
              <th>Kind</th>
              <th>Replicas</th>
              <th>Image</th>
              <th>Current</th>
              <th>Latest</th>
              <th>Diff</th>
              <th>C/H/M</th>
              <th>EOL</th>
            </tr>
          </thead>
          <tbody>
            ${tableRows}
          </tbody>
        </table>
        <div class="footer">PatchMgmt - Open Source Resource Tracker</div>
        <div class="no-print" style="margin-top:20px;text-align:center">
          <button onclick="window.print()" style="padding:10px 20px;font-size:14px;cursor:pointer;background:#1e3a5f;color:white;border:none;border-radius:4px">
            📄 Print / Save as PDF
          </button>
        </div>
      </body>
      </html>
    `
    
    printWindow.document.write(html)
    printWindow.document.close()
  }
  
  const handleDownload = () => {
    const data = getFilteredResources()
    if (data.length === 0) {
      alert('No resources to download. Please select at least one platform.')
      return
    }
    
    setDownloading(true)
    try {
      if (format === 'csv') {
        downloadCSV(data)
      } else {
        downloadPDF(data)
      }
    } finally {
      setDownloading(false)
    }
  }
  
  const filteredCount = getFilteredResources().length
  
  return (
    <div className="card" style={{ padding: 24 }}>
      <div style={{ marginBottom: 24 }}>
        <h3 style={{ margin: 0, marginBottom: 8 }}>Download Resources</h3>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Press Download button to download resources for selected platforms
        </p>
      </div>
      
      {/* Platform Selection */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontWeight: 600, marginBottom: 12, fontSize: 14 }}>1. Select Platforms</div>
        
        {availablePlatforms.length === 0 ? (
          <p className="muted">No platforms available. Please sync resources first.</p>
        ) : (
          <>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, padding: '8px 12px', background: 'var(--bg)', borderRadius: 6, cursor: 'pointer' }}>
              <input 
                type="checkbox" 
                checked={selectAll} 
                onChange={toggleSelectAll}
                style={{ width: 18, height: 18 }}
              />
              <span style={{ fontWeight: 600 }}>Select All Platforms</span>
              <span className="muted" style={{ fontSize: 12 }}>({availablePlatforms.length} platforms)</span>
            </label>
            
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8 }}>
              {availablePlatforms.map(platform => (
                <label 
                  key={platform} 
                  style={{ 
                    display: 'flex', 
                    alignItems: 'center', 
                    gap: 8, 
                    padding: '8px 12px', 
                    background: selectedPlatforms.has(platform) ? 'rgba(59, 130, 246, 0.15)' : 'var(--bg)', 
                    borderRadius: 6, 
                    cursor: 'pointer',
                    border: selectedPlatforms.has(platform) ? '1px solid rgba(59, 130, 246, 0.5)' : '1px solid transparent',
                  }}
                >
                  <input 
                    type="checkbox" 
                    checked={selectedPlatforms.has(platform)} 
                    onChange={() => togglePlatform(platform)}
                    style={{ width: 16, height: 16 }}
                  />
                  <span style={{ fontSize: 13 }}>{platform}</span>
                </label>
              ))}
            </div>
          </>
        )}
      </div>
      
      {/* Format Selection */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontWeight: 600, marginBottom: 12, fontSize: 14 }}>2. Select Format</div>
        <div style={{ display: 'flex', gap: 12 }}>
          <label style={{ 
            display: 'flex', 
            alignItems: 'center', 
            gap: 8, 
            padding: '12px 20px', 
            background: format === 'csv' ? 'rgba(34, 197, 94, 0.15)' : 'var(--bg)', 
            borderRadius: 8, 
            cursor: 'pointer',
            border: format === 'csv' ? '2px solid rgba(34, 197, 94, 0.5)' : '2px solid transparent',
          }}>
            <input 
              type="radio" 
              name="format" 
              value="csv" 
              checked={format === 'csv'} 
              onChange={() => setFormat('csv')}
            />
            <span style={{ fontSize: 20 }}>📊</span>
            <div>
              <div style={{ fontWeight: 600 }}>CSV</div>
              <div className="muted" style={{ fontSize: 11 }}>Spreadsheet format</div>
            </div>
          </label>
          
          <label style={{ 
            display: 'flex', 
            alignItems: 'center', 
            gap: 8, 
            padding: '12px 20px', 
            background: format === 'pdf' ? 'rgba(59, 130, 246, 0.15)' : 'var(--bg)', 
            borderRadius: 8, 
            cursor: 'pointer',
            border: format === 'pdf' ? '2px solid rgba(59, 130, 246, 0.5)' : '2px solid transparent',
          }}>
            <input 
              type="radio" 
              name="format" 
              value="pdf" 
              checked={format === 'pdf'} 
              onChange={() => setFormat('pdf')}
            />
            <span style={{ fontSize: 20 }}>📄</span>
            <div>
              <div style={{ fontWeight: 600 }}>PDF</div>
              <div className="muted" style={{ fontSize: 11 }}>Print-ready report</div>
            </div>
          </label>
        </div>
      </div>
      
      {/* Download Button */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
        <button 
          className="btn" 
          onClick={handleDownload}
          disabled={selectedPlatforms.size === 0 || downloading}
          style={{ minWidth: 150, padding: '12px 24px', fontSize: 14 }}
        >
          {downloading ? 'Downloading...' : `⬇️ Download ${format.toUpperCase()}`}
        </button>
        
        {selectedPlatforms.size > 0 && (
          <span className="muted" style={{ fontSize: 12 }}>
            {selectedPlatforms.size} platform(s) selected • {filteredCount} resources
          </span>
        )}
      </div>
    </div>
  )
}

function SyncLogsTable(): JSX.Element {
  const [logs, setLogs] = useState<any[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [errorModal, setErrorModal] = useState<any>(null)
  const [cancellingId, setCancellingId] = useState<number | null>(null)
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date())
  
  // Filter states
  const [filterBackend, setFilterBackend] = useState<string>('')
  const [filterPlatform, setFilterPlatform] = useState<string>('')
  const [filterStatus, setFilterStatus] = useState<string>('')
  const [filterTriggeredBy, setFilterTriggeredBy] = useState<string>('')
  const [filterDateFrom, setFilterDateFrom] = useState<string>('')
  const [filterDateTo, setFilterDateTo] = useState<string>('')
  
  const loadLogs = async (showLoading = true) => {
    if (showLoading) setLoading(true)
    try {
      const token = sessionStorage.getItem('token')
      if (token) {
        // Fetch sync logs from ALL backends
        const allLogs = await fetchSyncLogs(token)
        // Sort by started_at descending (newest first)
        allLogs.sort((a: any, b: any) => {
          const dateA = new Date(a.started_at || 0).getTime()
          const dateB = new Date(b.started_at || 0).getTime()
          return dateB - dateA
        })
        setLogs(allLogs)
        setLastRefresh(new Date())
      }
    } catch (e) {
      console.error('Failed to load sync logs:', e)
    }
    if (showLoading) setLoading(false)
  }
  
  // Initial load
  useEffect(() => {
    void loadLogs()
  }, [])
  
  // Periodic check every 15 seconds to detect new syncs (even if no running jobs)
  useEffect(() => {
    const periodicCheck = setInterval(() => {
      void loadLogs(false)
    }, 15000) // Check every 15 seconds for new syncs
    
    return () => clearInterval(periodicCheck)
  }, [])
  
  // Fast auto-refresh every 5 seconds when there are running jobs
  useEffect(() => {
    const hasRunningJobs = logs.some(l => l.status === 'running')
    if (!hasRunningJobs) return
    
    const interval = setInterval(() => {
      void loadLogs(false) // Don't show loading indicator for auto-refresh
    }, 5000)
    
    return () => clearInterval(interval)
  }, [logs])
  
  // Get unique values for filter dropdowns
  const uniqueBackends = useMemo(() => 
    [...new Set(logs.map(l => l.triggered_by_backend || l._backend || 'default'))].sort(),
    [logs]
  )
  const uniquePlatforms = useMemo(() => 
    [...new Set(logs.map(l => l.platform).filter(Boolean))].sort(),
    [logs]
  )
  const uniqueStatuses = useMemo(() => 
    [...new Set(logs.map(l => l.status).filter(Boolean))].sort(),
    [logs]
  )
  const uniqueTriggeredBy = useMemo(() => 
    [...new Set(logs.map(l => l.triggered_by || 'auto').filter(Boolean))].sort(),
    [logs]
  )
  
  // Apply filters
  const filteredLogs = useMemo(() => {
    return logs.filter(log => {
      // Backend filter
      if (filterBackend) {
        const backendName = log.triggered_by_backend || log._backend || 'default'
        if (backendName !== filterBackend) return false
      }
      // Platform filter
      if (filterPlatform && log.platform !== filterPlatform) return false
      // Status filter
      if (filterStatus && log.status !== filterStatus) return false
      // Triggered By filter
      if (filterTriggeredBy) {
        const triggeredBy = log.triggered_by || 'auto'
        if (triggeredBy !== filterTriggeredBy) return false
      }
      // Date From filter
      if (filterDateFrom) {
        const logDate = new Date(log.started_at)
        const fromDate = new Date(filterDateFrom)
        if (logDate < fromDate) return false
      }
      // Date To filter
      if (filterDateTo) {
        const logDate = new Date(log.started_at)
        const toDate = new Date(filterDateTo)
        toDate.setHours(23, 59, 59, 999)
        if (logDate > toDate) return false
      }
      return true
    })
  }, [logs, filterBackend, filterPlatform, filterStatus, filterTriggeredBy, filterDateFrom, filterDateTo])
  
  const hasActiveFilters = filterBackend || filterPlatform || filterStatus || filterTriggeredBy || filterDateFrom || filterDateTo
  
  const clearFilters = () => {
    setFilterBackend('')
    setFilterPlatform('')
    setFilterStatus('')
    setFilterTriggeredBy('')
    setFilterDateFrom('')
    setFilterDateTo('')
  }
  
  const cancelSync = async (log: any) => {
    const token = sessionStorage.getItem('token')
    if (!token) return
    
    if (!confirm(`Cancel sync job #${log.id} on ${log.triggered_by_backend || log._backend || 'default'}?`)) {
      return
    }
    
    setCancellingId(log.id)
    try {
      const backendName = log.backend_name || log._backend || 'default-backend'
      
      const resp = await fetch('/api/federation/cancel-sync', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          backend_name: backendName,
          sync_log_id: log.id
        })
      })
      
      if (resp.ok) {
        // Update the log in state
        setLogs(prev => prev.map(l => 
          (l.id === log.id && (l._backend || 'default') === (log._backend || 'default'))
            ? { ...l, status: 'cancelled', error_message: 'Cancelled by user', finished_at: new Date().toISOString() }
            : l
        ))
      } else {
        const err = await resp.json()
        alert(`Failed to cancel: ${err.detail || 'Unknown error'}`)
      }
    } catch (e: any) {
      alert(`Failed to cancel: ${e.message}`)
    } finally {
      setCancellingId(null)
    }
  }
  
  if (loading) return <p className="muted">Loading...</p>
  if (logs.length === 0) return <p className="muted">No sync operations recorded yet.</p>
  
  return (
    <>
      {/* Filter Bar */}
      <div style={{ 
        display: 'flex', 
        gap: 12, 
        marginBottom: 16, 
        padding: 12, 
        background: 'rgba(30, 41, 59, 0.5)', 
        borderRadius: 8,
        flexWrap: 'wrap',
        alignItems: 'flex-end'
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, color: 'var(--muted)' }}>Backend</label>
          <select 
            className="input" 
            value={filterBackend} 
            onChange={e => setFilterBackend(e.target.value)}
            style={{ minWidth: 140, fontSize: 11, padding: '6px 8px' }}
          >
            <option value="">All Backends</option>
            {uniqueBackends.map(b => <option key={b} value={b}>{b}</option>)}
          </select>
        </div>
        
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, color: 'var(--muted)' }}>Platform</label>
          <select 
            className="input" 
            value={filterPlatform} 
            onChange={e => setFilterPlatform(e.target.value)}
            style={{ minWidth: 140, fontSize: 11, padding: '6px 8px' }}
          >
            <option value="">All Platforms</option>
            {uniquePlatforms.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, color: 'var(--muted)' }}>Status</label>
          <select 
            className="input" 
            value={filterStatus} 
            onChange={e => setFilterStatus(e.target.value)}
            style={{ minWidth: 100, fontSize: 11, padding: '6px 8px' }}
          >
            <option value="">All Status</option>
            {uniqueStatuses.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, color: 'var(--muted)' }}>Triggered By</label>
          <select 
            className="input" 
            value={filterTriggeredBy} 
            onChange={e => setFilterTriggeredBy(e.target.value)}
            style={{ minWidth: 100, fontSize: 11, padding: '6px 8px' }}
          >
            <option value="">All</option>
            {uniqueTriggeredBy.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, color: 'var(--muted)' }}>Date From</label>
          <input 
            type="date" 
            className="input"
            value={filterDateFrom}
            onChange={e => setFilterDateFrom(e.target.value)}
            style={{ fontSize: 11, padding: '6px 8px', colorScheme: 'dark' }}
          />
        </div>
        
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, color: 'var(--muted)' }}>Date To</label>
          <input 
            type="date" 
            className="input"
            value={filterDateTo}
            onChange={e => setFilterDateTo(e.target.value)}
            style={{ fontSize: 11, padding: '6px 8px', colorScheme: 'dark' }}
          />
        </div>
        
        {hasActiveFilters && (
          <button 
            className="btn secondary" 
            onClick={clearFilters}
            style={{ fontSize: 11, padding: '6px 12px' }}
          >
            ✕ Clear
          </button>
        )}
        
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>
            Showing {filteredLogs.length} of {logs.length} logs
          </span>
          {logs.some(l => l.status === 'running') && (
            <span style={{ fontSize: 10, color: '#22c55e', display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#22c55e', animation: 'pulse 1.5s infinite' }}></span>
              Auto-refreshing
            </span>
          )}
          <span style={{ fontSize: 10, color: 'var(--muted)' }}>
            Updated: {lastRefresh.toLocaleTimeString()}
          </span>
          <button 
            className="btn secondary" 
            onClick={() => loadLogs(true)}
            style={{ fontSize: 11, padding: '4px 10px' }}
            title="Refresh sync logs"
          >
            🔄 Refresh
          </button>
        </div>
      </div>
      
      <div className="tableWrap">
        <table style={{width:'100%',fontSize:12}}>
          <thead>
            <tr style={{borderBottom:'1px solid var(--border)'}}>
              <th style={{textAlign:'left',padding:'8px'}}>Backend</th>
              <th style={{textAlign:'left',padding:'8px'}}>Platform</th>
              <th style={{textAlign:'left',padding:'8px'}}>Started</th>
              <th style={{textAlign:'left',padding:'8px'}}>Finished</th>
              <th style={{textAlign:'left',padding:'8px'}}>Triggered By</th>
              <th style={{textAlign:'left',padding:'8px'}}>Status</th>
              <th style={{textAlign:'right',padding:'8px'}}>Resources</th>
              <th style={{textAlign:'right',padding:'8px'}}>Errors</th>
              <th style={{textAlign:'right',padding:'8px'}}>Duration</th>
              <th style={{textAlign:'center',padding:'8px'}}>Details</th>
              <th style={{textAlign:'center',padding:'8px'}}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredLogs.map(log => (
              <tr key={`${log._backend || 'default'}-${log.id}`} style={{borderBottom:'1px solid var(--border)'}}>
                <td style={{padding:'8px'}}>
                  <div style={{fontWeight:500}}>{log.triggered_by_backend || log._backend || 'default'}</div>
                  {log._backend && log._backend !== 'default' && (
                    <div style={{fontSize:10,color:'var(--muted)'}}>{log._platform || log.platform}</div>
                  )}
                </td>
                <td style={{padding:'8px',color:'var(--muted)'}}>{log.platform || '-'}</td>
                <td style={{padding:'8px'}}>{new Date(log.started_at).toLocaleString()}</td>
                <td style={{padding:'8px'}}>{log.finished_at ? new Date(log.finished_at).toLocaleString() : '-'}</td>
                <td style={{padding:'8px'}}>{log.triggered_by || 'auto'}</td>
                <td style={{padding:'8px'}}>
                  <span className={`pill ${
                    log.status === 'success' ? 'same' : 
                    log.status === 'error' || log.status === 'timeout' ? 'major' : 
                    log.status === 'cancelled' ? 'minor' :
                    'unknown'
                  }`}>
                    {log.status}
                  </span>
                </td>
                <td style={{padding:'8px',textAlign:'right'}}>{log.resources_refreshed ?? '-'}</td>
                <td style={{padding:'8px',textAlign:'right'}}>{log.errors ?? '0'}</td>
                <td style={{padding:'8px',textAlign:'right'}}>
                  {log.duration_seconds ? `${Math.round(log.duration_seconds)}s` : '-'}
                </td>
                <td style={{padding:'8px',textAlign:'center'}}>
                  {(log.error_message || (log.status === 'error' || log.status === 'timeout' || log.status === 'cancelled')) ? (
                    <button className="btn secondary" onClick={() => setErrorModal(log)} style={{fontSize:10,padding:'2px 6px'}}>
                      View
                    </button>
                  ) : (
                    <span style={{color:'var(--muted)',fontSize:10}}>-</span>
                  )}
                </td>
                <td style={{padding:'8px',textAlign:'center'}}>
                  {log.status === 'running' ? (
                    <button 
                      className="btn" 
                      onClick={() => cancelSync(log)} 
                      disabled={cancellingId === log.id}
                      style={{
                        fontSize:10,
                        padding:'2px 8px',
                        background:'#dc2626',
                        border:'none',
                        color:'white',
                        cursor: cancellingId === log.id ? 'wait' : 'pointer',
                        opacity: cancellingId === log.id ? 0.6 : 1
                      }}
                    >
                      {cancellingId === log.id ? '...' : '✕ Cancel'}
                    </button>
                  ) : (
                    <span style={{color:'var(--muted)',fontSize:10}}>-</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {errorModal && (
        <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.85)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:999}} onClick={() => setErrorModal(null)}>
          <div className="card" style={{maxWidth:'700px',width:'90%',maxHeight:'80vh',overflow:'auto',background:'var(--panel)',boxShadow:'0 20px 60px rgba(0,0,0,.5)'}} onClick={(e)=>e.stopPropagation()}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
              <h2 style={{margin:0}}>Sync Error Details</h2>
              <button className="btn secondary" onClick={() => setErrorModal(null)}>Close</button>
            </div>
            <div style={{marginBottom:12}}>
              <div style={{fontSize:13,color:'var(--muted)',marginBottom:8}}>
                <strong>Started:</strong> {new Date(errorModal.started_at).toLocaleString()}
              </div>
              <div style={{fontSize:13,color:'var(--muted)',marginBottom:8}}>
                <strong>Status:</strong> <span className={`pill ${errorModal.status === 'error' || errorModal.status === 'timeout' ? 'major' : 'unknown'}`}>{errorModal.status}</span>
              </div>
              <div style={{fontSize:13,color:'var(--muted)',marginBottom:8}}>
                <strong>Errors:</strong> {errorModal.errors ?? 0} / {errorModal.resources_refreshed ?? 0} resources
              </div>
            </div>
            <div style={{marginTop:16}}>
              <h3 style={{margin:'0 0 8px 0',fontSize:14}}>Error Message</h3>
              <pre style={{background:'var(--card)',padding:12,borderRadius:8,fontSize:11,lineHeight:1.4,overflow:'auto',maxHeight:300,color:'var(--danger)'}}>
                {errorModal.error_message || 'No error message recorded'}
              </pre>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function SyncModal({ backends, selectedBackends, onSelect, onSync, onClose }: {
  backends: any[]
  selectedBackends: Set<string>
  onSelect: (name: string, checked: boolean) => void
  onSync: () => void
  onClose: () => void
}): JSX.Element {
  // Safety check: ensure backends is an array
  const backendsList = Array.isArray(backends) ? backends : []
  const approvedBackends = backendsList.filter(b => b && b.approved)
  const allSelected = approvedBackends.length > 0 && approvedBackends.every(b => selectedBackends.has(b.name))
  
  return (
    <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.92)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:1000}}>
      <div style={{background:'var(--panel)',padding:24,borderRadius:8,minWidth:500,maxWidth:'90vw',boxShadow:'0 8px 32px rgba(0,0,0,0.5)'}}>
        <h2 style={{marginTop:0,marginBottom:16}}>Select Backends to SYNC</h2>
        <p className="muted" style={{fontSize:12,marginBottom:16}}>
          Choose which backends to trigger a SYNC operation on. Each backend will refresh its own resource data.
        </p>
        
        {approvedBackends.length === 0 ? (
          <p style={{color:'var(--muted)',fontSize:13}}>No approved backends available.</p>
        ) : (
          <>
            <div style={{marginBottom:12}}>
              <label style={{display:'flex',alignItems:'center',gap:8,padding:8,background:'var(--bg)',borderRadius:4,cursor:'pointer'}}>
                <input 
                  type="checkbox" 
                  checked={allSelected}
                  onChange={(e) => {
                    approvedBackends.forEach(b => onSelect(b.name, e.target.checked))
                  }}
                />
                <strong>Select All ({approvedBackends.length})</strong>
              </label>
            </div>
            
            <div style={{maxHeight:300,overflowY:'auto',border:'1px solid var(--border)',borderRadius:4,padding:8}}>
              {approvedBackends.map(b => (
                <label key={b.name} style={{display:'flex',alignItems:'center',gap:8,padding:8,cursor:'pointer',borderBottom:'1px solid var(--border)'}}>
                  <input 
                    type="checkbox" 
                    checked={selectedBackends.has(b.name)}
                    onChange={(e) => onSelect(b.name, e.target.checked)}
                  />
                  <div style={{flex:1}}>
                    <div style={{fontWeight:500}}>
                      {b.name} 
                      {b.is_default && <span className="pill same" style={{fontSize:9,marginLeft:6}}>default</span>}
                    </div>
                    <div style={{fontSize:11,color:'var(--muted)'}}>{b.platform} • {b.api_url}</div>
                  </div>
                </label>
              ))}
            </div>
          </>
        )}
        
        <div style={{marginTop:20,display:'flex',gap:8,justifyContent:'flex-end'}}>
          <button className="btn secondary" onClick={onClose}>Cancel</button>
          <button 
            className="btn" 
            onClick={onSync}
            disabled={selectedBackends.size === 0}
          >
            SYNC {selectedBackends.size > 0 ? `(${selectedBackends.size})` : ''}
          </button>
        </div>
      </div>
    </div>
  )
}
