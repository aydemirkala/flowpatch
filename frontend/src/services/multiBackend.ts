// Multi-backend API client
// Uses per-backend streaming proxy: /api/proxy/{backend_name}/api/...
// Each backend is called in parallel via nginx → Python streaming proxy.
// No JSON accumulation on primary backend — minimal memory usage..
import { isTokenExpired, handleTokenExpiration } from '../ui/auth'

type BackendConfig = {
  id: number
  name: string
  platform: string
  api_url: string
  enabled: boolean
  is_default: boolean
  skip_tls_verify: boolean
}

let _proxyTimeoutMs = 45000
let _proxyTimeoutFetched = false

async function ensureProxyTimeout(token: string) {
  if (_proxyTimeoutFetched) return
  try {
    const resp = await fetch('/api/proxy-config', {
      headers: { Authorization: `Bearer ${token}` }
    })
    if (resp.ok) {
      const data = await resp.json()
      if (data.proxy_timeout_ms > 0) _proxyTimeoutMs = data.proxy_timeout_ms
    }
  } catch { /* use default */ }
  _proxyTimeoutFetched = true
}

export function resetProxyTimeoutCache() { _proxyTimeoutFetched = false }

function handleUnauthorized(response: Response) {
  if (response.status === 401) {
    console.error('Token expired or invalid (401 Unauthorized)')
    handleTokenExpiration()
    throw new Error('Token expired')
  }
}

export async function getBackendConfigs(token: string): Promise<BackendConfig[]> {
  if (isTokenExpired(token)) {
    handleTokenExpiration()
    return []
  }
  try {
    const resp = await fetch('/api/backend-endpoints', {
      headers: { Authorization: `Bearer ${token}`, 'Cache-Control': 'no-cache' },
      cache: 'no-store'
    })
    handleUnauthorized(resp)
    if (resp.ok) return await resp.json()
  } catch (e) {
    console.error('Failed to load backend configs:', e)
  }
  return []
}

async function fetchFromBackend(backendName: string, path: string, token: string, timeout = 15000): Promise<any[]> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const resp = await fetch(`/api/proxy/${encodeURIComponent(backendName)}/${path}`, {
      headers: { Authorization: `Bearer ${token}`, 'Cache-Control': 'no-cache' },
      cache: 'no-store',
      signal: controller.signal
    })
    clearTimeout(timer)
    handleUnauthorized(resp)
    if (resp.ok) {
      const data = await resp.json()
      return Array.isArray(data) ? data : []
    }
    console.warn(`Proxy ${backendName}/${path} returned ${resp.status}`)
  } catch (e: any) {
    clearTimeout(timer)
    if (e.name === 'AbortError') {
      console.warn(`Proxy ${backendName}/${path} timed out`)
    } else {
      console.error(`Proxy ${backendName}/${path} error:`, e)
    }
  }
  return []
}

export type BackendFetchResult = {
  resources: any[]
  failedBackends: string[]
}

async function fetchFromBackendChecked(
  backendName: string, path: string, token: string, timeout: number
): Promise<{ items: any[], ok: boolean }> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const resp = await fetch(`/api/proxy/${encodeURIComponent(backendName)}/${path}`, {
      headers: { Authorization: `Bearer ${token}`, 'Cache-Control': 'no-cache' },
      cache: 'no-store',
      signal: controller.signal
    })
    clearTimeout(timer)
    handleUnauthorized(resp)
    if (resp.ok) {
      const data = await resp.json()
      return { items: Array.isArray(data) ? data : [], ok: true }
    }
    console.warn(`Proxy ${backendName}/${path} returned ${resp.status}`)
    return { items: [], ok: false }
  } catch (e: any) {
    clearTimeout(timer)
    if (e.name === 'AbortError') console.warn(`Proxy ${backendName}/${path} timed out`)
    else console.error(`Proxy ${backendName}/${path} error:`, e)
    return { items: [], ok: false }
  }
}

export async function fetchResourcesWithStatus(token: string): Promise<BackendFetchResult> {
  if (isTokenExpired(token)) { handleTokenExpiration(); return { resources: [], failedBackends: [] } }

  await ensureProxyTimeout(token)
  const backends = await getBackendConfigs(token)
  const enabled = backends.filter(b => b.enabled)
  if (!enabled.length) return { resources: [], failedBackends: [] }

  const results = await Promise.allSettled(
    enabled.map(b => fetchFromBackendChecked(b.name, 'api/resources?slim=true', token, _proxyTimeoutMs))
  )

  const all: any[] = []
  const failedBackends: string[] = []
  for (let i = 0; i < results.length; i++) {
    const r = results[i]
    const name = enabled[i].name
    if (r.status === 'fulfilled' && r.value.ok) {
      all.push(...r.value.items.map(item => ({ ...item, _backend: name })))
    } else {
      failedBackends.push(name)
    }
  }
  console.log(`Multi-backend fetch: ${all.length} resources, ${failedBackends.length} failed backends`)
  return { resources: all, failedBackends }
}

export async function fetchResources(token: string): Promise<any[]> {
  const result = await fetchResourcesWithStatus(token)
  return result.resources
}

export async function fetchSyncLogs(token: string): Promise<any[]> {
  if (isTokenExpired(token)) { handleTokenExpiration(); return [] }

  const backends = await getBackendConfigs(token)
  if (!backends.length) return []

  const results = await Promise.allSettled(
    backends
      .filter(b => b.enabled)
      .map(async (b) => {
        const items = await fetchFromBackend(b.name, 'api/sync-logs?limit=50', token, _proxyTimeoutMs)
        return items.map(item => ({ ...item, _backend: b.name }))
      })
  )

  const all: any[] = []
  for (const r of results) {
    if (r.status === 'fulfilled') all.push(...r.value)
  }
  return all
}

export async function fetchFromAllBackends<T>(
  endpoint: string,
  token: string
): Promise<T[]> {
  if (endpoint === '/api/resources') return fetchResources(token) as Promise<T[]>
  if (endpoint === '/api/sync-logs') return fetchSyncLogs(token) as Promise<T[]>

  if (isTokenExpired(token)) { handleTokenExpiration(); return [] }
  try {
    const resp = await fetch(endpoint, {
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store'
    })
    handleUnauthorized(resp)
    if (resp.ok) {
      const data = await resp.json()
      return Array.isArray(data) ? data : []
    }
  } catch (e) {
    console.error('Fetch error:', e)
  }
  return []
}
