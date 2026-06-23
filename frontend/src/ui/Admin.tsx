import React, { useEffect, useState } from 'react'
import { getAuthToken } from './auth'

type Row = { username: string; role: string }

type EolConfig = {
  url?: string | null
  ttl_days?: number
}

async function fetchEolConfig(): Promise<EolConfig> {
  const token = getAuthToken()
  if (!token) throw new Error('Not authenticated')
  const resp = await fetch('/api/admin/eol', { headers: { Authorization: `Bearer ${token}` } })
  if (!resp.ok) throw new Error('Failed to fetch EOL config')
  return await resp.json()
}

async function saveEolConfig(url: string, ttlDays: number): Promise<void> {
  const token = getAuthToken()
  if (!token) throw new Error('Not authenticated')
  const body = new URLSearchParams()
  body.set('url', url)
  body.set('ttl_days', String(ttlDays))
  const resp = await fetch('/api/admin/eol', { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' }, body })
  if (!resp.ok) throw new Error('Failed to save EOL config')
}

export default function Admin(): JSX.Element {
  const [rows, setRows] = useState<Row[]>([])
  const [error, setError] = useState<string | null>(null)
  const [roles, setRoles] = useState<string[]>([])
  const [newUser, setNewUser] = useState<string>('')
  const [newRole, setNewRole] = useState<string>('read-only')
  const [generated, setGenerated] = useState<string>('')
  const [newRoleFor, setNewRoleFor] = useState<string>('')
  const [roleChoice, setRoleChoice] = useState<string>('read-only')
  const [resetFor, setResetFor] = useState<string>('')
  const [eolUrl, setEolUrl] = useState<string>('')
  const [eolTtlDays, setEolTtlDays] = useState<number>(3)
  const [savingEol, setSavingEol] = useState<boolean>(false)
  const [eolLoaded, setEolLoaded] = useState<boolean>(false)
  const [autoSyncInterval, setAutoSyncInterval] = useState<string>('')
  const [selectedBackendForSync, setSelectedBackendForSync] = useState<string>('')
  const [savingAutoSync, setSavingAutoSync] = useState<boolean>(false)
  const [autoSyncLoaded, setAutoSyncLoaded] = useState<boolean>(false)
  const [skipAutoLoad, setSkipAutoLoad] = useState<boolean>(false)
  const [showScheduleModal, setShowScheduleModal] = useState<boolean>(false)
  const [modalBackendName, setModalBackendName] = useState<string>('')
  const [modalSchedule, setModalSchedule] = useState<string>('')
  const [syncForce, setSyncForce] = useState<boolean>(false)
  const [savingSyncForce, setSavingSyncForce] = useState<boolean>(false)
  const [cleanupForce, setCleanupForce] = useState<boolean>(false)
  const [savingCleanupForce, setSavingCleanupForce] = useState<boolean>(false)
  const [backends, setBackends] = useState<any[]>([])
  const [newBackendName, setNewBackendName] = useState<string>('')
  const [newBackendPlatform, setNewBackendPlatform] = useState<string>('')
  const [newBackendUrl, setNewBackendUrl] = useState<string>('')
  const [newBackendSkipTls, setNewBackendSkipTls] = useState<boolean>(false)
  // Product management states
  const [allProducts, setAllProducts] = useState<any[]>([])
  const [managedProducts, setManagedProducts] = useState<any[]>([])
  const [manualProductName, setManualProductName] = useState<string>('')
  const [refreshingProducts, setRefreshingProducts] = useState<boolean>(false)
  const [productSearch, setProductSearch] = useState<string>('')
  const [dynamicMode, setDynamicMode] = useState<boolean>(false)
  const [discovering, setDiscovering] = useState<boolean>(false)
  const [discoveryResult, setDiscoveryResult] = useState<any>(null)
  // SMTP Config
  const [smtpServer, setSmtpServer] = useState<string>('')
  const [smtpPort, setSmtpPort] = useState<number>(587)
  const [smtpFromEmail, setSmtpFromEmail] = useState<string>('')
  const [smtpUsername, setSmtpUsername] = useState<string>('')
  const [smtpPassword, setSmtpPassword] = useState<string>('')
  const [smtpUseTls, setSmtpUseTls] = useState<boolean>(true)
  const [smtpSchedule, setSmtpSchedule] = useState<string>('')
  const [smtpTesting, setSmtpTesting] = useState<boolean>(false)
  const [smtpSaving, setSmtpSaving] = useState<boolean>(false)
  // Email Recipients
  const [recipients, setRecipients] = useState<any[]>([])
  const [newRecipientEmail, setNewRecipientEmail] = useState<string>('')
  const [newRecipientName, setNewRecipientName] = useState<string>('')
  // Email Sending
  const [emailSubject, setEmailSubject] = useState<string>('Patch Management Resources Report')
  const [emailBody, setEmailBody] = useState<string>('Please find attached the latest resources report.')
  const [selectedRecipients, setSelectedRecipients] = useState<Set<number>>(new Set())
  const [sendingEmail, setSendingEmail] = useState<boolean>(false)
  
  // Compare Settings
  const [maxComparisonReports, setMaxComparisonReports] = useState<number>(20)
  const [savingCompareSettings, setSavingCompareSettings] = useState<boolean>(false)

  // Proxy Settings
  const [proxyTimeout, setProxyTimeout] = useState<number>(60)
  const [stuckJobTimeout, setStuckJobTimeout] = useState<number>(60)
  const [savingProxy, setSavingProxy] = useState<boolean>(false)

  // Smart Watch Settings
  const [stuckDetection, setStuckDetection] = useState<number>(300)
  const [crashTolerance, setCrashTolerance] = useState<number>(120)
  const [patchBatchSize, setPatchBatchSize] = useState<number>(10)
  const [patchBatchPause, setPatchBatchPause] = useState<number>(2)

  // LDAP/AD Settings
  const [ldapEnabled, setLdapEnabled] = useState<boolean>(false)
  const [ldapServerUrl, setLdapServerUrl] = useState<string>('')
  const [ldapPort, setLdapPort] = useState<number>(636)
  const [ldapUseSsl, setLdapUseSsl] = useState<boolean>(true)
  const [ldapCaCert, setLdapCaCert] = useState<string>('')
  const [ldapSkipCertVerify, setLdapSkipCertVerify] = useState<boolean>(false)
  const [ldapBindDn, setLdapBindDn] = useState<string>('')
  const [ldapBindPassword, setLdapBindPassword] = useState<string>('')
  const [ldapBaseDn, setLdapBaseDn] = useState<string>('')
  const [ldapUserSearchFilter, setLdapUserSearchFilter] = useState<string>('(&(objectClass=user)(sAMAccountName={username}))')
  const [ldapUserSearchBase, setLdapUserSearchBase] = useState<string>('')
  const [ldapUsernameAttr, setLdapUsernameAttr] = useState<string>('sAMAccountName')
  const [ldapEmailAttr, setLdapEmailAttr] = useState<string>('mail')
  const [ldapDisplayNameAttr, setLdapDisplayNameAttr] = useState<string>('displayName')
  const [ldapGroupSearchBase, setLdapGroupSearchBase] = useState<string>('')
  const [ldapGroupSearchFilter, setLdapGroupSearchFilter] = useState<string>('')
  const [ldapAdminGroupDn, setLdapAdminGroupDn] = useState<string>('')
  const [ldapAnalystGroupDn, setLdapAnalystGroupDn] = useState<string>('')
  const [ldapReadonlyGroupDn, setLdapReadonlyGroupDn] = useState<string>('')
  const [ldapTab, setLdapTab] = useState<'server'|'users'|'groups'|'test'>('server')
  const [ldapTesting, setLdapTesting] = useState<boolean>(false)
  const [ldapTestResult, setLdapTestResult] = useState<{success: boolean, message: string}|null>(null)
  const [ldapSaving, setLdapSaving] = useState<boolean>(false)
  const [ldapDetecting, setLdapDetecting] = useState<boolean>(false)
  const [ldapTestUsername, setLdapTestUsername] = useState<string>('')
  const [ldapTestPassword, setLdapTestPassword] = useState<string>('')
  const [ldapTestUserResult, setLdapTestUserResult] = useState<any>(null)

  const load = async () => {
    setError(null)
    try {
      const token = getAuthToken()
      if (!token) {
        setError('Not authenticated - please login again')
        return
      }
      const resp = await fetch('/api/admin/users', { headers: { Authorization: `Bearer ${token}` } })
      if (!resp.ok) {
        if (resp.status === 401) {
          setError('Session expired - please login again')
          sessionStorage.clear()
          window.location.hash = '#/'
          return
        }
        throw new Error('Failed to load users')
      }
      setRows(await resp.json())
    } catch (e: any) {
      setError(e.message || String(e))
    }
  }

  useEffect(() => { void load() }, [])
  useEffect(() => { void loadRoles() }, [])
  useEffect(() => { void loadBackends() }, [])
  useEffect(() => { void loadSmtpConfig() }, [])
  useEffect(() => { void loadRecipients() }, [])
  useEffect(() => { void loadProducts() }, [])
  useEffect(() => { void loadManagedProducts() }, [])
  useEffect(() => { void loadDynamicMode() }, [])
  useEffect(() => { void loadCompareSettings() }, [])
  useEffect(() => { void loadProxySettings() }, [])
  useEffect(() => { void loadLdapConfig() }, [])
  
  // Auto-refresh backends every 10 seconds to update approval status
  useEffect(() => {
    const interval = setInterval(() => {
      void loadBackends()
    }, 10000) // 10 seconds
    return () => clearInterval(interval)
  }, [])
  useEffect(() => {
    fetchEolConfig().then(cfg => {
      setEolUrl(cfg.url || '')
      setEolTtlDays(cfg.ttl_days ?? 3)
      setEolLoaded(true)
    }).catch(() => setEolLoaded(true))
  }, [])
  useEffect(() => {
    const fetchAutoSync = async () => {
      try {
        const token = getAuthToken()
        if (!token) return
        const resp = await fetch('/api/admin/auto-sync', { headers: { Authorization: `Bearer ${token}` } })
        if (resp.ok) {
          const data = await resp.json()
          setAutoSyncInterval(String(data.interval_seconds || '0'))
        }
        setAutoSyncLoaded(true)
      } catch {
        setAutoSyncLoaded(true)
      }
    }
    void fetchAutoSync()
  }, [])
  
  useEffect(() => {
    const fetchSyncForce = async () => {
      try {
        const token = getAuthToken()
        if (!token) return
        const resp = await fetch('/api/admin/sync-force', { headers: { Authorization: `Bearer ${token}` } })
        if (resp.ok) {
          const data = await resp.json()
          setSyncForce(data.enabled || false)
        }
      } catch {}
    }
    void fetchSyncForce()
  }, [])

  useEffect(() => {
    const fetchCleanupForce = async () => {
      try {
        const token = getAuthToken()
        if (!token) return
        const resp = await fetch('/api/admin/cleanup-force', { headers: { Authorization: `Bearer ${token}` } })
        if (resp.ok) {
          const data = await resp.json()
          setCleanupForce(data.enabled || false)
        }
      } catch {}
    }
    void fetchCleanupForce()
  }, [])

  const loadRoles = async () => {
    try {
      const token = getAuthToken()
      if (!token) {
        console.error('No auth token for roles')
        return
      }
      const resp = await fetch('/api/admin/roles', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const rolesData = await resp.json()
        setRoles(rolesData)
      } else {
        console.error('Failed to load roles:', resp.status, resp.statusText)
      }
    } catch (e) {
      console.error('Error loading roles:', e)
    }
  }

  const createUser = async () => {
    setGenerated('')
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch(`/api/admin/users?username=${encodeURIComponent(newUser)}&role=${encodeURIComponent(newRole)}`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
      if (!resp.ok) throw new Error('Failed to create user')
      const data = await resp.json()
      setGenerated(`Temporary password: ${data.password}`)
      setNewUser('')
      await load()
    } catch (e: any) {
      setError(e.message || String(e))
    }
  }

  const onSaveEol = async () => {
    try {
      setSavingEol(true)
      await saveEolConfig(eolUrl.trim(), eolTtlDays)
      alert('EOL configuration saved')
    } catch (e) {
      alert('Failed to save EOL configuration')
    } finally {
      setSavingEol(false)
    }
  }

  const loadAutoSyncForBackend = async (backendName: string) => {
    try {
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch(`/api/admin/auto-sync?backend_name=${encodeURIComponent(backendName)}`, { 
        headers: { Authorization: `Bearer ${token}` } 
      })
      if (resp.ok) {
        const data = await resp.json()
        setAutoSyncInterval(data.interval_seconds || '')
        setAutoSyncLoaded(true)
      }
    } catch (e) {
      console.error('Failed to load auto-sync config:', e)
    }
  }

  const onSaveAutoSync = async () => {
    if (!selectedBackendForSync) {
      alert('Please select a backend')
      return
    }
    try {
      setSavingAutoSync(true)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('interval_seconds', autoSyncInterval || '0')
      body.set('backend_name', selectedBackendForSync)
      const resp = await fetch('/api/admin/auto-sync', { 
        method: 'POST', 
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' }, 
        body 
      })
      if (!resp.ok) throw new Error('Failed to save')
      const data = await resp.json()
      alert(data.message || 'Auto-sync configuration saved')
      // Reload backends to refresh the schedule display
      await loadBackends()
    } catch (e) {
      alert('Failed to save auto-sync configuration')
    } finally {
      setSavingAutoSync(false)
    }
  }

  const onSaveScheduleModal = async () => {
    if (!modalBackendName) return
    try {
      setSavingAutoSync(true)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('interval_seconds', modalSchedule || '0')
      body.set('backend_name', modalBackendName)
      const resp = await fetch('/api/admin/auto-sync', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      if (!resp.ok) throw new Error('Failed to save')
      const data = await resp.json()
      alert(data.message || 'Auto-sync configuration saved')
      setShowScheduleModal(false)
      await loadBackends()
    } catch (e) {
      alert('Failed to save auto-sync configuration')
    } finally {
      setSavingAutoSync(false)
    }
  }

  // Load schedule when selected backend changes (but not when Edit button was used)
  useEffect(() => {
    if (selectedBackendForSync && !skipAutoLoad) {
      loadAutoSyncForBackend(selectedBackendForSync)
    }
    // Reset the skip flag after effect runs
    if (skipAutoLoad) {
      setSkipAutoLoad(false)
    }
  }, [selectedBackendForSync])

  const onToggleSyncForce = async () => {
    try {
      setSavingSyncForce(true)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const newValue = !syncForce
      const body = new URLSearchParams()
      body.set('enabled', String(newValue))
      const resp = await fetch('/api/admin/sync-force', { 
        method: 'POST', 
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' }, 
        body 
      })
      if (!resp.ok) throw new Error('Failed to save')
      setSyncForce(newValue)
      alert(`Force sync ${newValue ? 'enabled' : 'disabled'}`)
    } catch (e) {
      alert('Failed to update force sync setting')
    } finally {
      setSavingSyncForce(false)
    }
  }

  const onToggleCleanupForce = async () => {
    const newValue = !cleanupForce
    if (newValue) {
      const ok = window.confirm(
        'Enable Cleanup Force?\n\n' +
        'When ON, sync cleanup will bypass the 10% mass-delete safety guard. ' +
        'This can WIPE most of your resource list if the sources file is incomplete.\n\n' +
        'Only enable this when you have intentionally shrunk the source list.'
      )
      if (!ok) return
    }
    try {
      setSavingCleanupForce(true)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('enabled', String(newValue))
      const resp = await fetch('/api/admin/cleanup-force', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body,
      })
      if (!resp.ok) throw new Error('Failed to save')
      setCleanupForce(newValue)
      alert(`Cleanup force ${newValue ? 'enabled' : 'disabled'}`)
    } catch (e) {
      alert('Failed to update cleanup force setting')
    } finally {
      setSavingCleanupForce(false)
    }
  }

  const loadBackends = async () => {
    try {
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch('/api/admin/backend-endpoints', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const data = await resp.json()
        setBackends(data)
        // Set first backend as default selection if not already set
        if (data.length > 0 && !selectedBackendForSync) {
          setSelectedBackendForSync(data[0].name)
        }
      }
    } catch (e) {
      console.error('Failed to load backends:', e)
    }
  }

  const loadProducts = async () => {
    try {
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch('/api/admin/products/list', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const data = await resp.json()
        setAllProducts(data.products || [])
      }
    } catch (e) {
      console.error('Failed to load products:', e)
    }
  }

  const loadManagedProducts = async () => {
    try {
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch('/api/admin/products/managed', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const data = await resp.json()
        setManagedProducts(data.products || [])
      }
    } catch (e) {
      console.error('Failed to load managed products:', e)
    }
  }

  const refreshProductList = async () => {
    try {
      setRefreshingProducts(true)
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch('/api/admin/products/refresh', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        const data = await resp.json()
        alert(data.message || 'Product list refreshed!')
        await loadProducts()
      } else {
        alert('Failed to refresh product list')
      }
    } catch (e) {
      alert('Error refreshing products: ' + e)
    } finally {
      setRefreshingProducts(false)
    }
  }

  const addToManaged = async (productName: string) => {
    try {
      const token = getAuthToken()
      if (!token) return
      const body = new URLSearchParams()
      body.set('product_name', productName)
      const resp = await fetch('/api/admin/products/managed', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      if (resp.ok) {
        await loadManagedProducts()
      } else {
        const data = await resp.json()
        alert(data.detail || 'Failed to add product')
      }
    } catch (e) {
      alert('Error adding product: ' + e)
    }
  }

  const updateImagePattern = async (productId: number, imagePattern: string) => {
    try {
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch(`/api/admin/products/managed/${productId}`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_pattern: imagePattern })
      })
      if (resp.ok) {
        await loadManagedProducts()
      } else {
        const data = await resp.json()
        alert(data.detail || 'Failed to update image pattern')
      }
    } catch (e) {
      alert('Error updating image pattern: ' + e)
    }
  }

  const removeFromManaged = async (productId: number) => {
    try {
      const token = getAuthToken()
      if (!token) return

      const product = managedProducts.find((p: any) => p.id === productId)
      const productName = product?.name || ''

      const resp = await fetch(`/api/admin/products/managed/${productId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` }
      })
      if (!resp.ok) { alert('Failed to remove product'); return }

      if (productName) {
        try {
          const backendsResp = await fetch('/api/admin/backend-endpoints', {
            headers: { Authorization: `Bearer ${token}` }
          })
          if (backendsResp.ok) {
            const allBackends = await backendsResp.json()
            const enabledBackends = allBackends.filter((b: any) => b.enabled)
            const results = await Promise.all(
              enabledBackends.map(async (backend: any) => {
                try {
                  const url = backend.is_default
                    ? '/api/resources/cleanup-by-product'
                    : `/api/proxy/${backend.name}/api/resources/cleanup-by-product`
                  const r = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                    body: JSON.stringify({ product_name: productName }),
                  })
                  if (r.ok) {
                    const d = await r.json()
                    return { backend: backend.name, ok: true, deleted: d.deleted || 0 }
                  }
                  return { backend: backend.name, ok: false, status: r.status }
                } catch {
                  return { backend: backend.name, ok: false, status: 'unreachable' }
                }
              })
            )
            const total = results.filter(r => r.ok).reduce((s, r) => s + (r.deleted || 0), 0)
            const failed = results.filter(r => !r.ok)
            if (total > 0 || failed.length > 0) {
              let msg = `Product "${productName}" removed.\n\nResource cleanup: ${total} deleted.`
              if (failed.length > 0) {
                msg += `\n\nFailed backends (may need updated code):\n` + failed.map(f => `  ${f.backend}: ${f.status}`).join('\n')
              }
              alert(msg)
            }
          }
        } catch { /* backend list fetch failed, product still removed */ }
      }

      await loadManagedProducts()
    } catch (e) {
      alert('Error removing product: ' + e)
    }
  }

  const addManualProduct = async () => {
    if (!manualProductName.trim()) {
      alert('Please enter a product name')
      return
    }
    await addToManaged(manualProductName.trim())
    setManualProductName('')
  }

  const exportManagedProducts = async () => {
    try {
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch('/api/admin/products/managed/export', {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        const blob = await resp.blob()
        const url = window.URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `managed_products_${new Date().toISOString().split('T')[0]}.json`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        window.URL.revokeObjectURL(url)
      } else {
        alert('Failed to export products')
      }
    } catch (e) {
      alert('Error exporting products: ' + e)
    }
  }

  const importManagedProducts = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    
    try {
      const token = getAuthToken()
      if (!token) return
      
      const formData = new FormData()
      formData.append('file', file)
      
      const resp = await fetch('/api/admin/products/managed/import', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData
      })
      
      const data = await resp.json()
      
      if (resp.ok) {
        await loadManagedProducts()
        alert(`Import complete!\n\n✅ Added: ${data.added_count} products\n⏭️ Skipped (already exist): ${data.skipped_count} products${data.sync_triggered ? '\n\n🔄 Sync triggered for all backends' : ''}`)
      } else {
        alert(data.detail || 'Failed to import products')
      }
    } catch (e) {
      alert('Error importing products: ' + e)
    }
    
    // Reset file input
    event.target.value = ''
  }

  const loadDynamicMode = async () => {
    try {
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch('/api/admin/products/dynamic-mode', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const data = await resp.json()
        setDynamicMode(data.enabled || false)
      }
    } catch (e) {
      console.error('Failed to load dynamic mode:', e)
    }
  }

  const toggleDynamicMode = async (enabled: boolean) => {
    try {
      const token = getAuthToken()
      if (!token) return
      const body = new URLSearchParams()
      body.set('enabled', String(enabled))
      const resp = await fetch('/api/admin/products/dynamic-mode', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      if (resp.ok) {
        const data = await resp.json()
        setDynamicMode(data.enabled)
        alert(data.message || 'Dynamic mode updated')
      } else {
        alert('Failed to update dynamic mode')
      }
    } catch (e) {
      alert('Error updating dynamic mode: ' + e)
    }
  }

  const discoverNow = async () => {
    try {
      setDiscovering(true)
      setDiscoveryResult(null)
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch('/api/admin/products/discover', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const data = await resp.json()
        setDiscoveryResult(data)
      } else {
        alert('Failed to discover resources')
      }
    } catch (e) {
      alert('Error discovering resources: ' + e)
    } finally {
      setDiscovering(false)
    }
  }

  const loadSmtpConfig = async () => {
    try {
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch('/api/admin/smtp', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const data = await resp.json()
        setSmtpServer(data.smtp_server || '')
        setSmtpPort(parseInt(data.smtp_port || '587'))
        setSmtpFromEmail(data.smtp_from_email || '')
        setSmtpUsername(data.smtp_username || '')
        setSmtpUseTls(data.smtp_use_tls === 'true')
        setSmtpSchedule(data.smtp_schedule || '')
      }
    } catch (e) {
      console.error('Failed to load SMTP config:', e)
    }
  }

  const loadCompareSettings = async () => {
    try {
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch('/api/admin/compare-settings', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const data = await resp.json()
        setMaxComparisonReports(parseInt(data.max_comparison_reports || '20'))
      }
    } catch (e) {
      console.error('Failed to load compare settings:', e)
    }
  }

  const saveCompareSettings = async () => {
    try {
      setSavingCompareSettings(true)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('max_comparison_reports', String(maxComparisonReports))
      const resp = await fetch('/api/admin/compare-settings', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      if (!resp.ok) throw new Error('Failed to save')
      alert('Compare settings saved successfully')
    } catch (e: any) {
      alert(e.message || 'Failed to save compare settings')
    } finally {
      setSavingCompareSettings(false)
    }
  }

  const loadProxySettings = async () => {
    try {
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch('/api/admin/proxy-settings', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const data = await resp.json()
        setProxyTimeout(data.proxy_timeout_seconds ?? 60)
        setStuckJobTimeout(data.stuck_job_timeout_minutes ?? 60)
        setStuckDetection(data.stuck_detection_seconds ?? 300)
        setCrashTolerance(data.crash_tolerance_seconds ?? 120)
        setPatchBatchSize(data.patch_batch_size ?? 10)
        setPatchBatchPause(data.patch_batch_pause_seconds ?? 2)
      }
    } catch (e) {
      console.error('Failed to load proxy settings:', e)
    }
  }

  const saveProxySettings = async () => {
    try {
      setSavingProxy(true)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const resp = await fetch('/api/admin/proxy-settings', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          proxy_timeout_seconds: proxyTimeout,
          stuck_job_timeout_minutes: stuckJobTimeout,
          stuck_detection_seconds: stuckDetection,
          crash_tolerance_seconds: crashTolerance,
          patch_batch_size: patchBatchSize,
          patch_batch_pause_seconds: patchBatchPause,
        })
      })
      if (!resp.ok) throw new Error('Failed to save')
      // Reset frontend cache so next fetch picks up the new value
      const { resetProxyTimeoutCache } = await import('../services/multiBackend')
      resetProxyTimeoutCache()
      alert('Proxy settings saved successfully')
    } catch (e: any) {
      alert(e.message || 'Failed to save proxy settings')
    } finally {
      setSavingProxy(false)
    }
  }

  // LDAP Functions
  const loadLdapConfig = async () => {
    try {
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch('/api/admin/ldap', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const data = await resp.json()
        setLdapEnabled(data.enabled || false)
        setLdapServerUrl(data.server_url || '')
        setLdapPort(data.port || 636)
        setLdapUseSsl(data.use_ssl !== false)
        setLdapCaCert(data.ca_cert || '')
        setLdapSkipCertVerify(data.skip_cert_verify || false)
        setLdapBindDn(data.bind_dn || '')
        setLdapBindPassword(data.bind_password || '')
        setLdapBaseDn(data.base_dn || '')
        setLdapUserSearchFilter(data.user_search_filter || '(&(objectClass=user)(sAMAccountName={username}))')
        setLdapUserSearchBase(data.user_search_base || '')
        setLdapUsernameAttr(data.username_attribute || 'sAMAccountName')
        setLdapEmailAttr(data.email_attribute || 'mail')
        setLdapDisplayNameAttr(data.display_name_attribute || 'displayName')
        setLdapGroupSearchBase(data.group_search_base || '')
        setLdapGroupSearchFilter(data.group_search_filter || '')
        setLdapAdminGroupDn(data.admin_group_dn || '')
        setLdapAnalystGroupDn(data.analyst_group_dn || '')
        setLdapReadonlyGroupDn(data.readonly_group_dn || '')
      }
    } catch (e) {
      console.error('Failed to load LDAP config:', e)
    }
  }

  const saveLdapConfig = async () => {
    try {
      setLdapSaving(true)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('enabled', String(ldapEnabled))
      body.set('server_url', ldapServerUrl)
      body.set('port', String(ldapPort))
      body.set('use_ssl', String(ldapUseSsl))
      body.set('ca_cert', ldapCaCert)
      body.set('skip_cert_verify', String(ldapSkipCertVerify))
      body.set('bind_dn', ldapBindDn)
      body.set('bind_password', ldapBindPassword)
      body.set('base_dn', ldapBaseDn)
      body.set('user_search_filter', ldapUserSearchFilter)
      body.set('user_search_base', ldapUserSearchBase)
      body.set('username_attribute', ldapUsernameAttr)
      body.set('email_attribute', ldapEmailAttr)
      body.set('display_name_attribute', ldapDisplayNameAttr)
      body.set('group_search_base', ldapGroupSearchBase)
      body.set('group_search_filter', ldapGroupSearchFilter)
      body.set('admin_group_dn', ldapAdminGroupDn)
      body.set('analyst_group_dn', ldapAnalystGroupDn)
      body.set('readonly_group_dn', ldapReadonlyGroupDn)
      const resp = await fetch('/api/admin/ldap', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      if (!resp.ok) throw new Error('Failed to save')
      setLdapTestResult({ success: true, message: 'Configuration saved successfully' })
    } catch (e: any) {
      setLdapTestResult({ success: false, message: e.message || 'Failed to save LDAP config' })
    } finally {
      setLdapSaving(false)
    }
  }

  const testLdapConnection = async () => {
    try {
      setLdapTesting(true)
      setLdapTestResult(null)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('server_url', ldapServerUrl)
      body.set('port', String(ldapPort))
      body.set('use_ssl', String(ldapUseSsl))
      body.set('ca_cert', ldapCaCert)
      body.set('skip_cert_verify', String(ldapSkipCertVerify))
      body.set('bind_dn', ldapBindDn)
      body.set('bind_password', ldapBindPassword)
      const resp = await fetch('/api/admin/ldap/test-connection', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      const data = await resp.json()
      setLdapTestResult({ success: data.success, message: data.message })
    } catch (e: any) {
      setLdapTestResult({ success: false, message: e.message || 'Connection test failed' })
    } finally {
      setLdapTesting(false)
    }
  }

  const detectBaseDn = async () => {
    try {
      setLdapDetecting(true)
      setLdapTestResult(null)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('server_url', ldapServerUrl)
      body.set('port', String(ldapPort))
      body.set('use_ssl', String(ldapUseSsl))
      body.set('ca_cert', ldapCaCert)
      body.set('skip_cert_verify', String(ldapSkipCertVerify))
      body.set('bind_dn', ldapBindDn)
      body.set('bind_password', ldapBindPassword)
      const resp = await fetch('/api/admin/ldap/detect-base-dn', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      const data = await resp.json()
      if (data.success) {
        setLdapBaseDn(data.base_dn)
        setLdapTestResult({ success: true, message: `Base DN detected: ${data.base_dn}` })
      } else {
        setLdapTestResult({ success: false, message: data.message || 'Failed to detect base DN' })
      }
    } catch (e: any) {
      setLdapTestResult({ success: false, message: e.message || 'Detection failed' })
    } finally {
      setLdapDetecting(false)
    }
  }

  const testBaseDn = async () => {
    try {
      setLdapTesting(true)
      setLdapTestResult(null)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('server_url', ldapServerUrl)
      body.set('port', String(ldapPort))
      body.set('use_ssl', String(ldapUseSsl))
      body.set('ca_cert', ldapCaCert)
      body.set('skip_cert_verify', String(ldapSkipCertVerify))
      body.set('bind_dn', ldapBindDn)
      body.set('bind_password', ldapBindPassword)
      body.set('base_dn', ldapBaseDn)
      const resp = await fetch('/api/admin/ldap/test-base-dn', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      const data = await resp.json()
      setLdapTestResult({ success: data.success, message: data.message })
    } catch (e: any) {
      setLdapTestResult({ success: false, message: e.message || 'Test failed' })
    } finally {
      setLdapTesting(false)
    }
  }

  const testUserLogin = async () => {
    try {
      setLdapTesting(true)
      setLdapTestUserResult(null)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('test_username', ldapTestUsername)
      body.set('test_password', ldapTestPassword)
      const resp = await fetch('/api/admin/ldap/test-user-login', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      const data = await resp.json()
      setLdapTestUserResult(data)
    } catch (e: any) {
      setLdapTestUserResult({ success: false, message: e.message || 'Test failed' })
    } finally {
      setLdapTesting(false)
    }
  }

  const loadRecipients = async () => {
    try {
      const token = getAuthToken()
      if (!token) return
      const resp = await fetch('/api/admin/email-recipients', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        setRecipients(await resp.json())
      }
    } catch (e) {
      console.error('Failed to load recipients:', e)
    }
  }

  const saveSmtpConfig = async () => {
    try {
      setSmtpSaving(true)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('smtp_server', smtpServer)
      body.set('smtp_port', String(smtpPort))
      body.set('smtp_from_email', smtpFromEmail)
      body.set('smtp_username', smtpUsername)
      if (smtpPassword) body.set('smtp_password', smtpPassword)
      body.set('smtp_use_tls', String(smtpUseTls))
      body.set('smtp_schedule', smtpSchedule)
      const resp = await fetch('/api/admin/smtp', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      if (!resp.ok) throw new Error('Failed to save')
      setSmtpPassword('')
      alert('SMTP configuration saved successfully')
    } catch (e: any) {
      alert(e.message || 'Failed to save SMTP configuration')
    } finally {
      setSmtpSaving(false)
    }
  }

  const testSmtpConnection = async () => {
    try {
      setSmtpTesting(true)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const resp = await fetch('/api/admin/smtp/test', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` }
      })
      const data = await resp.json()
      alert(data.message)
    } catch (e: any) {
      alert(e.message || 'Connection test failed')
    } finally {
      setSmtpTesting(false)
    }
  }

  const addRecipient = async () => {
    try {
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('email', newRecipientEmail)
      body.set('name', newRecipientName)
      body.set('enabled', 'true')
      const resp = await fetch('/api/admin/email-recipients', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      if (!resp.ok) throw new Error('Failed to add recipient')
      setNewRecipientEmail('')
      setNewRecipientName('')
      await loadRecipients()
      alert('Recipient added successfully')
    } catch (e: any) {
      alert(e.message || 'Failed to add recipient')
    }
  }

  const sendEmail = async () => {
    try {
      setSendingEmail(true)
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const recipientIds = selectedRecipients.size > 0 ? Array.from(selectedRecipients) : []
      const resp = await fetch('/api/resources/send-email', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          recipient_ids: recipientIds,
          subject: emailSubject,
          body: emailBody
        })
      })
      if (!resp.ok) throw new Error('Failed to send email')
      const data = await resp.json()
      alert(`Email sent successfully to ${data.recipients} recipient(s)`)
      setSelectedRecipients(new Set())
    } catch (e: any) {
      alert(e.message || 'Failed to send email')
    } finally {
      setSendingEmail(false)
    }
  }

  const createBackend = async () => {
    try {
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      const body = new URLSearchParams()
      body.set('name', newBackendName)
      body.set('platform', newBackendPlatform)
      body.set('api_url', newBackendUrl)
      body.set('enabled', 'true')
      body.set('skip_tls_verify', String(newBackendSkipTls))
      const resp = await fetch('/api/admin/backend-endpoints', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      if (!resp.ok) throw new Error('Failed to create backend')
      const data = await resp.json()
      setNewBackendName('')
      setNewBackendPlatform('')
      setNewBackendUrl('')
      setNewBackendSkipTls(false)
      await loadBackends()
      
      // Show token to user (IMPORTANT - save this!)
      const tokenMsg = `Backend created successfully!\n\n` +
        `⚠️ IMPORTANT: Copy this authentication token and configure it in your remote backend:\n\n` +
        `BACKEND_AUTH_TOKEN=${data.auth_token}\n\n` +
        `Environment Variables for Remote Backend:\n` +
        `BACKEND_NAME=${newBackendName}\n` +
        `BACKEND_PLATFORM=${newBackendPlatform}\n` +
        `BACKEND_URL=${newBackendUrl}\n` +
        `BACKEND_AUTH_TOKEN=${data.auth_token}\n` +
        `BACKEND_IS_DEFAULT=false\n\n` +
        `This token will NOT be shown again. Save it now!`
      
      alert(tokenMsg)
      
      // Copy to clipboard
      try {
        await navigator.clipboard.writeText(data.auth_token)
        alert('Token copied to clipboard!')
      } catch {
        // Clipboard copy failed silently
      }
    } catch (e: any) {
      alert(e.message || 'Failed to create backend')
    }
  }

  return (
    <div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ marginTop: 0 }}>Security Status Thresholds</h2>
        <p style={{ fontSize: 12, color: 'var(--muted)', margin: '0 0 12px 0' }}>
          Combined evaluation: uses the worst result from all enabled scanners (Twistlock, Trivy).
        </p>
        <SecurityThresholdsConfig />
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ marginTop: 0 }}>Twistlock / Prisma Cloud</h2>
        <TwistlockConfig />
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ marginTop: 0 }}>Trivy Scanner</h2>
        <TrivyConfig />
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ marginTop: 0 }}>LLM Security Advice</h2>
        <LLMConfig />
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ marginTop: 0 }}>LLM Prompts</h2>
        <p className="muted">Customize AI system prompts for security advice and upgrade analysis. Leave empty to use defaults.</p>
        <LLMPrompts />
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ marginTop: 0 }}>Latest Version Check</h2>
        <LatestVersionConfig />
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ marginTop: 0 }}>Users & Roles</h2>
        {error && <div className="muted" style={{ color: '#fecaca', marginBottom: 12 }}>{error}</div>}
        <h3 style={{fontSize:14,marginBottom:8}}>Add / Invite User</h3>
        <div className="inputs" style={{ marginTop: 8, flexWrap: 'wrap', marginBottom: 16 }}>
          <input className="input" placeholder="new username" value={newUser} onChange={(e) => setNewUser(e.target.value)} />
          <select className="select" value={newRole} onChange={(e) => setNewRole(e.target.value)}>
            {roles.length === 0 && <option value="">Loading roles...</option>}
            {roles.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <button className="btn" onClick={createUser} disabled={!newUser || roles.length === 0}>Add / Invite</button>
          {generated && <span className="muted" style={{ fontSize: 12 }}>{generated}</span>}
        </div>
        <h3 style={{fontSize:14,marginBottom:8}}>Existing Users</h3>
        {rows.length === 0 ? (
          <p className="muted" style={{fontSize:11}}>No users found or loading...</p>
        ) : (
          <div className="tableWrap">
            <table className="compactTable" style={{width:'100%'}}>
              <thead>
                <tr style={{borderBottom:'1px solid var(--border)'}}>
                  <th style={{textAlign:'left',padding:'6px',fontSize:11}}>Username</th>
                  <th style={{textAlign:'left',padding:'6px',fontSize:11}}>Role</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>Change Role</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.username} style={{borderBottom:'1px solid var(--border)'}}>
                    <td style={{padding:'6px',fontSize:11}}>{r.username}</td>
                    <td style={{padding:'6px',fontSize:11}}>
                      {newRoleFor === r.username ? (
                        <div style={{display:'flex',gap:6,alignItems:'center'}}>
                          <select className="select" value={roleChoice} onChange={(e) => setRoleChoice(e.target.value)} style={{fontSize:10,padding:'3px 5px'}}>
                            {roles.map((rr) => <option key={rr} value={rr}>{rr}</option>)}
                          </select>
                          <button className="btn" onClick={async () => {
                            const token = sessionStorage.getItem('token') || ''
                            await fetch(`/api/admin/users/${encodeURIComponent(r.username)}/role?role=${encodeURIComponent(roleChoice)}`, { method: 'PUT', headers: { Authorization: `Bearer ${token}` } })
                            setNewRoleFor('')
                            await load()
                          }} style={{fontSize:10,padding:'3px 6px'}}>Save</button>
                          <button className="btn secondary" onClick={() => setNewRoleFor('')} style={{fontSize:10,padding:'3px 6px'}}>Cancel</button>
                        </div>
                      ) : (
                        <span>{r.role}</span>
                      )}
                    </td>
                    <td style={{padding:'6px',textAlign:'center'}}>
                      {newRoleFor !== r.username && (
                        <button className="btn secondary" onClick={() => { setNewRoleFor(r.username); setRoleChoice(r.role) }} style={{fontSize:10,padding:'3px 6px'}}>Change</button>
                      )}
                    </td>
                    <td style={{padding:'6px',textAlign:'center'}}>
                      <div style={{display:'flex',gap:6,justifyContent:'center',flexWrap:'wrap'}}>
                        <button className="btn secondary" onClick={async () => {
                          const token = sessionStorage.getItem('token') || ''
                          const resp = await fetch(`/api/admin/users/${encodeURIComponent(r.username)}/reset-password`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
                          if (resp.ok) {
                            const d = await resp.json()
                            alert(`Temporary password for ${r.username}: ${d.password}`)
                          }
                        }} style={{fontSize:10,padding:'3px 6px',whiteSpace:'nowrap'}}>Reset Pwd</button>
                        <button className="btn danger" onClick={async () => {
                          const token = sessionStorage.getItem('token') || ''
                          if (!confirm(`Delete user ${r.username}?`)) return
                          await fetch(`/api/admin/users/${encodeURIComponent(r.username)}`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } })
                          await load()
                        }} style={{fontSize:10,padding:'3px 6px'}}>Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>EOL Integration</h2>
        <p className="muted">Configure the EOL API base URL. Leave empty to use the default (https://endoflife.date).</p>
        <div className="inputs" style={{ marginTop: 8 }}>
          <input className="input" style={{ minWidth: 360 }} placeholder="https://endoflife.date" value={eolUrl} onChange={e => setEolUrl(e.target.value)} />
          <button className={`btn${savingEol ? ' secondary' : ''}`} onClick={onSaveEol} disabled={savingEol}>{savingEol ? 'Saving...' : 'Save'}</button>
        </div>
        <p className="muted" style={{ marginTop: 12 }}>AI EOL status cache TTL (days) — how long an LLM-derived support status is kept before it is re-analyzed. Used only for products with no public EOL date.</p>
        <div className="inputs" style={{ marginTop: 8 }}>
          <input className="input" type="number" min={1} max={365} style={{ maxWidth: 120 }} value={eolTtlDays}
            onChange={e => setEolTtlDays(Math.max(1, Math.min(365, parseInt(e.target.value) || 3)))} />
          <span className="muted">days (default 3)</span>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Auto-Sync Schedule</h2>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 300px' }}>
            <label style={{ display: 'block', marginBottom: 4, fontSize: 13, color: 'var(--muted)' }}>Schedule (cron format)</label>
            <input 
              className="input" 
              type="text"
              placeholder="e.g. 0 3 * * * (daily at 3 AM)" 
              value={autoSyncInterval} 
              onChange={e => setAutoSyncInterval(e.target.value)} 
            />
          </div>
          <div style={{ flex: '0 0 200px' }}>
            <label style={{ display: 'block', marginBottom: 4, fontSize: 13, color: 'var(--muted)' }}>Backend</label>
            <select 
              className="input"
              value={selectedBackendForSync}
              onChange={e => setSelectedBackendForSync(e.target.value)}
            >
              {backends.map(b => (
                <option key={b.name} value={b.name}>
                  {b.name} ({b.platform})
                </option>
              ))}
            </select>
          </div>
          <button 
            className={`btn${savingAutoSync ? ' secondary' : ''}`} 
            onClick={onSaveAutoSync} 
            disabled={savingAutoSync || !selectedBackendForSync}
          >
            {savingAutoSync ? 'Saving...' : 'Save'}
          </button>
        </div>

        {backends.length > 0 && (
          <div style={{ marginTop: 20, overflowX: 'auto' }}>
            <h3 style={{ fontSize: 14, marginBottom: 8 }}>Current Schedules</h3>
            <table className="adminTable" style={{ width: '100%', fontSize: 12 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', padding: '8px' }}>Backend</th>
                  <th style={{ textAlign: 'left', padding: '8px' }}>Platform</th>
                  <th style={{ textAlign: 'left', padding: '8px' }}>Schedule</th>
                  <th style={{ textAlign: 'left', padding: '8px' }}>Status</th>
                  <th style={{ textAlign: 'center', padding: '8px', width: '120px' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {backends.map(b => (
                  <tr key={b.name} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '8px' }}>
                      <span style={{ fontWeight: 500 }}>{b.name}</span>
                      {b.is_default && <span className="pill same" style={{ fontSize: 9, marginLeft: 6 }}>default</span>}
                    </td>
                    <td style={{ padding: '8px', color: 'var(--muted)' }}>{b.platform}</td>
                    <td style={{ padding: '8px' }}>
                      {b.auto_sync_schedule ? (
                        <code style={{ background: 'var(--bg)', padding: '2px 6px', borderRadius: 3, fontSize: 11 }}>
                          {b.auto_sync_schedule}
                        </code>
                      ) : (
                        <span style={{ color: 'var(--muted)', fontStyle: 'italic' }}>Not configured</span>
                      )}
                    </td>
                    <td style={{ padding: '8px' }}>
                      {b.enabled ? (
                        <span className="pill same" style={{ fontSize: 9 }}>Active</span>
                      ) : (
                        <span className="pill unknown" style={{ fontSize: 9 }}>Disabled</span>
                      )}
                    </td>
                    <td style={{ padding: '8px', textAlign: 'center' }}>
                      <div style={{ display: 'flex', gap: 4, justifyContent: 'center' }}>
                        <button
                          className="btn secondary"
                          style={{ fontSize: 10, padding: '4px 8px' }}
                          onClick={() => {
                            setModalBackendName(b.name)
                            setModalSchedule(b.auto_sync_schedule || '')
                            setShowScheduleModal(true)
                          }}
                          title="Edit schedule"
                        >
                          Edit
                        </button>
                        {b.auto_sync_schedule && (
                          <button
                            className="btn secondary"
                            style={{ fontSize: 10, padding: '4px 8px', background: '#dc2626', borderColor: '#dc2626', color: '#fff' }}
                            onClick={async () => {
                              if (confirm(`Remove schedule for ${b.name}?`)) {
                                try {
                                  const token = getAuthToken()
                                  if (!token) return
                                  const body = new URLSearchParams()
                                  body.set('interval_seconds', '0')
                                  body.set('backend_name', b.name)
                                  const resp = await fetch('/api/admin/auto-sync', {
                                    method: 'POST',
                                    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
                                    body
                                  })
                                  if (resp.ok) {
                                    await loadBackends()
                                    if (selectedBackendForSync === b.name) {
                                      setAutoSyncInterval('')
                                    }
                                  } else {
                                    alert('Failed to remove schedule')
                                  }
                                } catch (e) {
                                  alert('Error removing schedule')
                                }
                              }
                            }}
                            title="Remove schedule"
                          >
                            Remove
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Timeout Settings</h2>
        <p className="muted">Configure timeouts for proxy requests and job execution.</p>
        <div className="inputs" style={{ alignItems: 'flex-end', gap: 12, marginTop: 8, flexWrap: 'wrap' }}>
          <div>
            <label style={{ fontSize: 11, color: 'var(--muted)' }}>Proxy Timeout (seconds)</label>
            <input className="input" type="number" min={10} max={3600} value={proxyTimeout}
              onChange={e => setProxyTimeout(Number(e.target.value))}
              style={{ width: 100 }} />
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--muted)' }}>Stuck Job Timeout (minutes)</label>
            <input className="input" type="number" min={10} max={1440} value={stuckJobTimeout}
              onChange={e => setStuckJobTimeout(Number(e.target.value))}
              style={{ width: 100 }} />
          </div>
          <button className="btn" onClick={saveProxySettings} disabled={savingProxy}>
            {savingProxy ? 'Saving...' : 'Save'}
          </button>
        </div>
        <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 8 }}>
          Proxy: 10–3600s (must be ≤ the OpenShift route timeout on BOTH the API and frontend routes). Stuck Job: 10–1440min (24h). Jobs are only auto-failed when no progress is detected for this duration. (Trivy scan/cache timeouts are in the Trivy Scanner card; Twistlock refresh interval is in the Twistlock card.)
        </div>

        <div style={{ borderTop: '1px solid var(--border)', marginTop: 16, paddingTop: 16 }}>
          <h3 style={{ fontSize: 14, marginBottom: 4 }}>Smart Watch Settings</h3>
          <p className="muted" style={{ fontSize: 11, marginBottom: 8 }}>Intelligent rollout monitoring with tolerance-based auto-rollback.</p>
          <div className="inputs" style={{ alignItems: 'flex-end', gap: 12, flexWrap: 'wrap' }}>
            <div>
              <label style={{ fontSize: 11, color: 'var(--muted)' }}>Stuck Detection (seconds)</label>
              <input className="input" type="number" min={60} max={1800} value={stuckDetection}
                onChange={e => setStuckDetection(Number(e.target.value))}
                style={{ width: 100 }} />
            </div>
            <div>
              <label style={{ fontSize: 11, color: 'var(--muted)' }}>Crash Tolerance (seconds)</label>
              <input className="input" type="number" min={30} max={600} value={crashTolerance}
                onChange={e => setCrashTolerance(Number(e.target.value))}
                style={{ width: 100 }} />
            </div>
            <div>
              <label style={{ fontSize: 11, color: 'var(--muted)' }}>Patch Batch Size</label>
              <input className="input" type="number" min={1} max={100} value={patchBatchSize}
                onChange={e => setPatchBatchSize(Number(e.target.value))}
                style={{ width: 100 }} />
            </div>
            <div>
              <label style={{ fontSize: 11, color: 'var(--muted)' }}>Batch Pause (seconds)</label>
              <input className="input" type="number" min={0} max={60} value={patchBatchPause}
                onChange={e => setPatchBatchPause(Number(e.target.value))}
                style={{ width: 100 }} />
            </div>
            <button className="btn" onClick={saveProxySettings} disabled={savingProxy}>
              {savingProxy ? 'Saving...' : 'Save'}
            </button>
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 8 }}>
            Stuck Detection: auto-rollback if pod not ready (60–1800s). Crash Tolerance: tolerate CrashLoopBackOff/ImagePullBackOff before rollback (30–600s). Batch Size: resources per batch (1–100). Batch Pause: seconds between batches (0–60s).
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Backend API Endpoints</h2>
        <p className="muted">Configure additional backend APIs to query multiple OpenShift/Kubernetes clusters. All backends share the same database.</p>
        
        <h3 style={{fontSize:14,marginTop:16,marginBottom:8}}>Add New Backend</h3>
        <div className="inputs" style={{marginBottom:16,flexWrap:'wrap',alignItems:'flex-end'}}>
          <input className="input" placeholder="Name (e.g. prod-api)" value={newBackendName} onChange={e => setNewBackendName(e.target.value)} style={{minWidth:150}} />
          <input className="input" placeholder="Platform (e.g. prod-openshift)" value={newBackendPlatform} onChange={e => setNewBackendPlatform(e.target.value)} style={{minWidth:180}} />
          <input className="input" placeholder="API URL (e.g. https://api.example.com)" value={newBackendUrl} onChange={e => setNewBackendUrl(e.target.value)} style={{minWidth:300}} />
          <label style={{display:'flex',alignItems:'center',gap:6,fontSize:11,whiteSpace:'nowrap'}}>
            <input type="checkbox" checked={newBackendSkipTls} onChange={e => setNewBackendSkipTls(e.target.checked)} />
            Skip TLS Verify
          </label>
          <button className="btn" onClick={createBackend} disabled={!newBackendName || !newBackendPlatform || !newBackendUrl}>Add Backend</button>
        </div>

        <h3 style={{fontSize:14,marginBottom:8}}>Configured Backends</h3>
        {backends.length === 0 ? (
          <p className="muted" style={{fontSize:11}}>No additional backends configured. The default local backend is always active.</p>
        ) : (
          <div className="tableWrap">
            <table className="compactTable" style={{width:'100%'}}>
              <thead>
                <tr style={{borderBottom:'1px solid var(--border)'}}>
                  <th style={{textAlign:'left',padding:'6px',fontSize:11}}>Name</th>
                  <th style={{textAlign:'left',padding:'6px',fontSize:11}}>Platform</th>
                  <th style={{textAlign:'left',padding:'6px',fontSize:11}}>API URL</th>
                  <th style={{textAlign:'left',padding:'6px',fontSize:11}}>Token</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>Status</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>Approved</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>Skip TLS</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>Latest Ver.</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>Twistlock</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>Trivy</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>EOL</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>LLM</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>LDAP</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {backends.map(b => (
                  <tr key={b.id} style={{borderBottom:'1px solid var(--border)'}}>
                    <td style={{padding:'6px',fontSize:11,whiteSpace:'nowrap'}}>
                      {b.name} {b.is_default && <span className="pill same" style={{fontSize:9,marginLeft:4}}>default</span>}
                    </td>
                    <td style={{padding:'6px',fontSize:11,whiteSpace:'nowrap'}}>{b.platform}</td>
                    <td style={{padding:'6px',fontSize:10,maxWidth:300,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}} title={b.api_url}>{b.api_url}</td>
                    <td style={{padding:'6px',fontSize:10,whiteSpace:'nowrap'}}>
                      <code style={{fontSize:9}}>{b.auth_token_preview || '...'}</code>
                    </td>
                    <td style={{padding:'6px',textAlign:'center'}}>
                      <span className={`pill ${b.enabled ? 'same' : 'unknown'}`} style={{fontSize:9}}>
                        {b.enabled ? 'On' : 'Off'}
                      </span>
                    </td>
                    <td style={{padding:'6px',textAlign:'center'}}>
                      {b.is_default ? (
                        <span className="pill same" style={{fontSize:9}} title="Default backend is auto-approved">✓ Active</span>
                      ) : (
                        <span 
                          className={`pill ${b.approved ? 'same' : 'alert'}`} 
                          style={{fontSize:9}}
                          title={b.approved 
                            ? `Connected ${b.last_seen_at ? 'at ' + new Date(b.last_seen_at).toLocaleString() : ''}` 
                            : 'Waiting for backend to connect with auth token'}>
                          {b.approved ? '✓ Connected' : '⚠ Pending'}
                        </span>
                      )}
                    </td>
                    <td style={{padding:'6px',textAlign:'center'}}>
                      <input 
                        type="checkbox" 
                        checked={b.skip_tls_verify || false} 
                        onChange={async () => {
                          const token = getAuthToken()
                          if (!token) return
                          const body = new URLSearchParams()
                          body.set('skip_tls_verify', String(!b.skip_tls_verify))
                          await fetch(`/api/admin/backend-endpoints/${b.id}`, {
                            method: 'PUT',
                            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
                            body
                          })
                          await loadBackends()
                        }}
                        title="Skip TLS certificate verification"
                      />
                    </td>
                    <td style={{padding:'6px',textAlign:'center'}}>
                      {b.is_default ? (
                        <span className="muted" style={{fontSize:9}}>N/A</span>
                      ) : (
                        <select
                          className="select"
                          style={{fontSize:9,padding:'2px 4px',minWidth:70}}
                          value={b.latest_version_mode || ''}
                          onChange={async (e) => {
                            const token = getAuthToken()
                            if (!token) return
                            const body = new URLSearchParams()
                            body.set('latest_version_mode', e.target.value)
                            await fetch(`/api/admin/backend-endpoints/${b.id}`, {
                              method: 'PUT',
                              headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
                              body
                            })
                            await loadBackends()
                          }}
                          title="Latest version check mode for this backend"
                        >
                          <option value="">Global</option>
                          <option value="local">Local</option>
                          <option value="primary">Primary</option>
                        </select>
                      )}
                    </td>
                    <td style={{padding:'6px',textAlign:'center'}}>
                      {b.is_default ? (
                        <span style={{fontSize:9,color:'var(--muted)'}}>N/A</span>
                      ) : (
                        <select
                          className="select"
                          style={{fontSize:9,padding:'2px 4px',minWidth:70}}
                          value={b.twistlock_mode || ''}
                          onChange={async (e) => {
                            const token = getAuthToken()
                            if (!token) return
                            const body = new URLSearchParams()
                            body.set('twistlock_mode', e.target.value)
                            await fetch(`/api/admin/backend-endpoints/${b.id}`, {
                              method: 'PUT',
                              headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
                              body
                            })
                            await loadBackends()
                          }}
                          title="Twistlock query mode for this backend"
                        >
                          <option value="">Global</option>
                          <option value="local">Local</option>
                          <option value="primary">Primary</option>
                        </select>
                      )}
                    </td>
                    <td style={{padding:'6px',textAlign:'center'}}>
                      {b.is_default ? (
                        <span style={{fontSize:9,color:'var(--muted)'}}>N/A</span>
                      ) : (
                        <select
                          className="select"
                          style={{fontSize:9,padding:'2px 4px',minWidth:70}}
                          value={b.trivy_mode || ''}
                          onChange={async (e) => {
                            const token = getAuthToken()
                            if (!token) return
                            const body = new URLSearchParams()
                            body.set('trivy_mode', e.target.value)
                            await fetch(`/api/admin/backend-endpoints/${b.id}`, {
                              method: 'PUT',
                              headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
                              body
                            })
                            await loadBackends()
                          }}
                          title="Trivy scan mode for this backend"
                        >
                          <option value="">Global</option>
                          <option value="local">Local</option>
                          <option value="primary">Primary</option>
                        </select>
                      )}
                    </td>
                    <td style={{padding:'6px',textAlign:'center'}}>
                      {b.is_default ? (
                        <span style={{fontSize:9,color:'var(--muted)'}}>N/A</span>
                      ) : (
                        <select
                          className="select"
                          style={{fontSize:9,padding:'2px 4px',minWidth:70}}
                          value={b.eol_mode || ''}
                          onChange={async (e) => {
                            const token = getAuthToken()
                            if (!token) return
                            const body = new URLSearchParams()
                            body.set('eol_mode', e.target.value)
                            await fetch(`/api/admin/backend-endpoints/${b.id}`, {
                              method: 'PUT',
                              headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
                              body
                            })
                            await loadBackends()
                          }}
                          title="EOL data query mode for this backend"
                        >
                          <option value="">Global</option>
                          <option value="local">Local</option>
                          <option value="primary">Primary</option>
                        </select>
                      )}
                    </td>
                    {/* LLM Mode */}
                    <td style={{padding:'6px',textAlign:'center'}}>
                      {b.is_default ? (
                        <span style={{fontSize:9,color:'var(--muted)'}}>N/A</span>
                      ) : (
                        <select
                          className="select"
                          style={{fontSize:9,padding:'2px 4px',minWidth:70}}
                          value={b.llm_mode || ''}
                          onChange={async (e) => {
                            const token = getAuthToken()
                            if (!token) return
                            const body = new URLSearchParams()
                            body.set('llm_mode', e.target.value)
                            await fetch(`/api/admin/backend-endpoints/${b.id}`, {
                              method: 'PUT',
                              headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
                              body
                            })
                            await loadBackends()
                          }}
                          title="LLM advice query mode: Primary routes requests through primary backend (rate-limited, cached)"
                        >
                          <option value="">Global</option>
                          <option value="local">Local</option>
                          <option value="primary">Primary</option>
                        </select>
                      )}
                    </td>
                    {/* LDAP Mode */}
                    <td style={{padding:'6px',textAlign:'center'}}>
                      {b.is_default ? (
                        <span style={{fontSize:9,color:'var(--muted)'}}>N/A</span>
                      ) : b.backend_type === 'primary' ? (
                        <span className="pill same" style={{fontSize:9}}>Local</span>
                      ) : (
                        <select
                          className="select"
                          style={{fontSize:9,padding:'2px 4px',minWidth:70}}
                          value={b.ldap_mode || 'primary'}
                          onChange={async (e) => {
                            const token = getAuthToken()
                            if (!token) return
                            const body = new URLSearchParams()
                            body.set('ldap_mode', e.target.value)
                            await fetch(`/api/admin/backend-endpoints/${b.id}`, {
                              method: 'PUT',
                              headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
                              body
                            })
                            await loadBackends()
                          }}
                          title="LDAP authentication mode: 'Primary' proxies auth to primary backend, 'Local' uses this backend's LDAP config"
                        >
                          <option value="primary">Primary</option>
                          <option value="local">Local</option>
                        </select>
                      )}
                    </td>
                    <td style={{padding:'6px',textAlign:'center'}}>
                      <div style={{display:'flex',gap:4,justifyContent:'center'}}>
                        <button className="btn secondary" onClick={async () => {
                          const token = getAuthToken()
                          if (!token) return
                          const body = new URLSearchParams()
                          body.set('enabled', String(!b.enabled))
                          await fetch(`/api/admin/backend-endpoints/${b.id}`, {
                            method: 'PUT',
                            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
                            body
                          })
                          await loadBackends()
                        }} style={{fontSize:9,padding:'2px 5px'}}>
                          {b.enabled ? 'Disable' : 'Enable'}
                        </button>
                        {!b.is_default && (
                          <button className="btn danger" onClick={async () => {
                            const token = getAuthToken()
                            if (!token) return
                            const confirmMsg = `⚠️ WARNING: Delete backend "${b.name}"?\n\nThis will permanently delete:\n- Backend configuration\n- All resources for platform "${b.platform}"\n- Resource history\n- Resource upgrade plans\n- Sync logs\n\nThis action CANNOT be undone!\n\nType "DELETE" to confirm:`
                            const userInput = prompt(confirmMsg)
                            if (userInput !== 'DELETE') {
                              if (userInput !== null) alert('Deletion cancelled. You must type "DELETE" to confirm.')
                              return
                            }
                            try {
                              const resp = await fetch(`/api/admin/backend-endpoints/${b.id}`, {
                                method: 'DELETE',
                                headers: { Authorization: `Bearer ${token}` }
                              })
                              if (!resp.ok) {
                                const data = await resp.json().catch(() => ({ detail: 'Unknown error' }))
                                throw new Error(data.detail || 'Failed to delete')
                              }
                              const result = await resp.json()
                              const deleted = result.deleted || {}
                              alert(`✅ Successfully deleted!\n\nDeleted:\n- ${deleted.sync_logs || 0} sync logs\n- ${deleted.resources || 0} resources\n- ${deleted.resource_plans || 0} resource plans\n- ${deleted.resource_history || 0} history records`)
                              await loadBackends()
                              // Also reload the main resource list if on main page
                              window.location.reload()
                            } catch (e: any) {
                              console.error('Delete backend error:', e)
                              alert(`Failed to delete backend: ${e.message || String(e)}`)
                            }
                          }} style={{fontSize:9,padding:'2px 5px'}}>Del</button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Force Sync Mode <span style={{fontSize:13,fontWeight:'normal',color:'var(--muted)',marginLeft:8}}>(Manual SYNC Only)</span></h2>
        <div style={{padding:12,background:'rgba(59,130,246,0.1)',border:'1px solid var(--border)',borderRadius:6,marginBottom:12}}>
          <div style={{fontSize:12,fontWeight:600,marginBottom:4,color:'#60a5fa'}}>ℹ️ Note:</div>
          <div style={{fontSize:11,color:'var(--muted)'}}>
            <strong>Auto-Sync (scheduled)</strong> always forces sync to ensure fresh data.<br/>
            This toggle only affects <strong>manual SYNC button</strong> operations.
          </div>
        </div>
        <p className="muted">When enabled, manual SYNC will bypass all caching and force refresh all data from sources.</p>
        <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 12 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input 
              type="checkbox" 
              checked={syncForce} 
              onChange={onToggleSyncForce}
              disabled={savingSyncForce}
              style={{ width: 18, height: 18, cursor: 'pointer' }}
            />
            <span>Enable Force Sync (bypasses cache)</span>
          </label>
          {savingSyncForce && <span className="muted" style={{ fontSize: 12 }}>Saving...</span>}
        </div>
        <div style={{ marginTop: 12, fontSize: 12, color: syncForce ? '#fbbf24' : 'var(--muted)' }}>
          {syncForce ? '⚠️ Force mode is ON - all SYNC operations will refresh all data' : 'Force mode is OFF - SYNC uses smart caching'}
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Cleanup Force Mode <span style={{fontSize:13,fontWeight:'normal',color:'var(--muted)',marginLeft:8}}>(Safety Override)</span></h2>
        <div style={{padding:12,background:'rgba(239,68,68,0.1)',border:'1px solid rgba(239,68,68,0.4)',borderRadius:6,marginBottom:12}}>
          <div style={{fontSize:12,fontWeight:600,marginBottom:4,color:'#f87171'}}>⚠️ Danger:</div>
          <div style={{fontSize:11,color:'var(--muted)'}}>
            By default, sync cleanup is <strong>aborted</strong> if it would delete more than <strong>10%</strong> of the resources for the managed platforms in a single run. This prevents mass-deletion bugs from wiping the database.<br/>
            Enabling this flag <strong>bypasses</strong> that guard. Use ONLY when you have intentionally shrunk the source list and accept that many existing resources will be removed.
          </div>
        </div>
        <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 12 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={cleanupForce}
              onChange={onToggleCleanupForce}
              disabled={savingCleanupForce}
              style={{ width: 18, height: 18, cursor: 'pointer' }}
            />
            <span>Enable Cleanup Force (bypass 10% mass-delete guard)</span>
          </label>
          {savingCleanupForce && <span className="muted" style={{ fontSize: 12 }}>Saving...</span>}
        </div>
        <div style={{ marginTop: 12, fontSize: 12, color: cleanupForce ? '#f87171' : 'var(--muted)' }}>
          {cleanupForce ? '⚠️ Cleanup force is ON - cleanup may delete >10% of resources in a single sync' : 'Cleanup force is OFF - cleanup is aborted if >10% of resources would be deleted'}
        </div>
      </div>

      {/* Exclusion Management */}
      <ExclusionManagementCard />

      {/* Database Cleanup */}
      <div className="card" style={{ marginTop: 16 }}>
        <h2>Database Cleanup</h2>
        <p className="muted" style={{fontSize:12,marginBottom:16}}>
          Remove unwanted resources from the database that should not be tracked.
        </p>
        
        <div style={{display:'flex',flexDirection:'column',gap:12}}>
          <div>
            <h3 style={{margin:0,fontSize:13,marginBottom:6}}>Excluded Namespaces</h3>
            <p className="muted" style={{fontSize:11,marginBottom:8}}>
              Remove resources from excluded namespaces (configured above in Exclusion Management)
            </p>
            <button 
              className="btn secondary" 
              onClick={async () => {
                if (!confirm('Remove all resources from system namespaces (openshift*, kube-*, kubernetes*, portworx*, ibm-*, default)?\n\nThis will run on ALL backends.')) return
                try {
                  const token = getAuthToken()
                  if (!token) throw new Error('Not authenticated')
                  
                  // Get all enabled backends
                  const backendsResp = await fetch('/api/admin/backend-endpoints', {
                    headers: { Authorization: `Bearer ${token}` }
                  })
                  if (!backendsResp.ok) throw new Error('Failed to load backends')
                  const allBackends = await backendsResp.json()
                  const enabledBackends = allBackends.filter((b: any) => b.enabled)
                  
                  // Clean up on all backends in parallel
                  const results = await Promise.all(
                    enabledBackends.map(async (backend: any) => {
                      try {
                        const baseUrl = backend.is_default ? '' : backend.api_url.replace(/\/api\/?$/, '')
                        const resp = await fetch(`${baseUrl}/api/resources/cleanup-system-namespaces`, {
                    method: 'POST',
                    headers: { Authorization: `Bearer ${token}` }
                  })
                        if (resp.ok) {
                  const data = await resp.json()
                          return { backend: backend.name, success: true, deleted: data.deleted || 0 }
                        }
                        return { backend: backend.name, success: false, error: `HTTP ${resp.status}` }
                      } catch (e: any) {
                        return { backend: backend.name, success: false, error: e.message }
                      }
                    })
                  )
                  
                  // Show summary
                  const totalDeleted = results.filter(r => r.success).reduce((sum, r) => sum + (r.deleted || 0), 0)
                  let message = `✅ Cleaned up ${totalDeleted} resources across ${results.length} backends:\n\n`
                  results.forEach(r => {
                    message += `${r.backend}: ${r.success ? `${r.deleted} removed` : `❌ ${r.error}`}\n`
                  })
                  alert(message)
                } catch (e: any) {
                  alert(e.message || 'Cleanup failed')
                }
              }}
              style={{fontSize:12,padding:'8px 16px'}}
            >
              🗑️ Clean Up System Namespaces
            </button>
          </div>
          
          <div style={{borderTop:'1px solid var(--border)',paddingTop:12}}>
            <h3 style={{margin:0,fontSize:13,marginBottom:6}}>Orphaned Resources</h3>
            <p className="muted" style={{fontSize:11,marginBottom:8}}>
              Remove resources whose platform doesn't match any registered backend (e.g., old backends that were removed)
            </p>
            <button 
              className="btn secondary" 
              onClick={async () => {
                if (!confirm('Remove all resources from unregistered platforms?\n\nThis will run on ALL backends.')) return
                try {
                  const token = getAuthToken()
                  if (!token) throw new Error('Not authenticated')
                  
                  // Get all enabled backends
                  const backendsResp = await fetch('/api/admin/backend-endpoints', {
                    headers: { Authorization: `Bearer ${token}` }
                  })
                  if (!backendsResp.ok) throw new Error('Failed to load backends')
                  const allBackends = await backendsResp.json()
                  const enabledBackends = allBackends.filter((b: any) => b.enabled)
                  
                  // Clean up on all backends in parallel
                  const results = await Promise.all(
                    enabledBackends.map(async (backend: any) => {
                      try {
                        const baseUrl = backend.is_default ? '' : backend.api_url.replace(/\/api\/?$/, '')
                        const resp = await fetch(`${baseUrl}/api/resources/cleanup-orphaned`, {
                          method: 'POST',
                          headers: { Authorization: `Bearer ${token}` }
                        })
                        if (resp.ok) {
                          const data = await resp.json()
                          return { 
                            backend: backend.name, 
                            success: true, 
                            deleted: data.deleted || 0,
                            orphaned_platforms: data.orphaned_platforms || [],
                            per_platform: data.per_platform || {}
                          }
                        }
                        return { backend: backend.name, success: false, error: `HTTP ${resp.status}` }
                      } catch (e: any) {
                        return { backend: backend.name, success: false, error: e.message }
                      }
                    })
                  )
                  
                  // Show summary
                  const totalDeleted = results.filter(r => r.success).reduce((sum, r) => sum + (r.deleted || 0), 0)
                  let message = `✅ Cleaned up ${totalDeleted} orphaned resources across ${results.length} backends:\n\n`
                  results.forEach(r => {
                    if (r.success) {
                      message += `${r.backend}: ${r.deleted} removed`
                      if (r.orphaned_platforms && r.orphaned_platforms.length > 0) {
                        message += ` from platforms: ${r.orphaned_platforms.join(', ')}`
                      }
                      message += '\n'
                    } else {
                      message += `${r.backend}: ❌ ${r.error}\n`
                    }
                  })
                  alert(message)
                  // Refresh the page to show updated resources
                  window.location.reload()
                } catch (e: any) {
                  alert(e.message || 'Cleanup failed')
                }
              }}
              style={{fontSize:12,padding:'8px 16px'}}
            >
              🧹 Clean Up Orphaned Resources
            </button>
          </div>
          
          <div style={{borderTop:'1px solid var(--border)',paddingTop:12}}>
            <h3 style={{margin:0,fontSize:13,marginBottom:6}}>Excluded Registry Images</h3>
            <p className="muted" style={{fontSize:11,marginBottom:8}}>
              Remove resources using excluded registries (configured above in Exclusion Management)
            </p>
            <button 
              className="btn secondary" 
              onClick={async () => {
                if (!confirm('Remove all resources using internal OpenShift registry (image-registry.openshift-image-registry.svc)?\n\nThis will run on ALL backends.')) return
                try {
                  const token = getAuthToken()
                  if (!token) throw new Error('Not authenticated')
                  
                  // Get all enabled backends
                  const backendsResp = await fetch('/api/admin/backend-endpoints', {
                    headers: { Authorization: `Bearer ${token}` }
                  })
                  if (!backendsResp.ok) throw new Error('Failed to load backends')
                  const allBackends = await backendsResp.json()
                  const enabledBackends = allBackends.filter((b: any) => b.enabled)
                  
                  // Clean up on all backends in parallel
                  const results = await Promise.all(
                    enabledBackends.map(async (backend: any) => {
                      try {
                        const baseUrl = backend.is_default ? '' : backend.api_url.replace(/\/api\/?$/, '')
                        const resp = await fetch(`${baseUrl}/api/resources/cleanup-internal-registry`, {
                    method: 'POST',
                    headers: { Authorization: `Bearer ${token}` }
                  })
                        if (resp.ok) {
                  const data = await resp.json()
                          return { backend: backend.name, success: true, deleted: data.deleted || 0 }
                        }
                        return { backend: backend.name, success: false, error: `HTTP ${resp.status}` }
                      } catch (e: any) {
                        return { backend: backend.name, success: false, error: e.message }
                      }
                    })
                  )
                  
                  // Show summary
                  const totalDeleted = results.filter(r => r.success).reduce((sum, r) => sum + (r.deleted || 0), 0)
                  let message = `✅ Cleaned up ${totalDeleted} resources across ${results.length} backends:\n\n`
                  results.forEach(r => {
                    message += `${r.backend}: ${r.success ? `${r.deleted} removed` : `❌ ${r.error}`}\n`
                  })
                  alert(message)
                } catch (e: any) {
                  alert(e.message || 'Cleanup failed')
                }
              }}
              style={{fontSize:12,padding:'8px 16px'}}
            >
              🗑️ Clean Up Internal Registry Images
            </button>
          </div>
          
          <div style={{borderTop:'1px solid var(--border)',paddingTop:12}}>
            <h3 style={{margin:0,fontSize:13,marginBottom:6}}>Stuck Sync Logs</h3>
            <p className="muted" style={{fontSize:11,marginBottom:8}}>
              Clean up sync logs stuck in "running" status (useful after backend restarts or crashes)
            </p>
            <button 
              className="btn secondary" 
              onClick={async () => {
                if (!confirm('Mark all stuck "running" sync logs as "timeout"?\n\nThis will run on ALL backends.')) return
                try {
                  const token = getAuthToken()
                  if (!token) throw new Error('Not authenticated')
                  
                  // Get all enabled backends
                  const backendsResp = await fetch('/api/admin/backend-endpoints', {
                    headers: { Authorization: `Bearer ${token}` }
                  })
                  if (!backendsResp.ok) throw new Error('Failed to load backends')
                  const allBackends = await backendsResp.json()
                  const enabledBackends = allBackends.filter((b: any) => b.enabled)
                  
                  // Clean up on all backends in parallel
                  const results = await Promise.all(
                    enabledBackends.map(async (backend: any) => {
                      try {
                        const baseUrl = backend.is_default ? '' : backend.api_url.replace(/\/api\/?$/, '')
                        const resp = await fetch(`${baseUrl}/api/sync-logs/cleanup-stuck`, {
                    method: 'POST',
                    headers: { Authorization: `Bearer ${token}` }
                  })
                        if (resp.ok) {
                  const data = await resp.json()
                          return { backend: backend.name, success: true, cleaned: data.cleaned || 0 }
                        }
                        return { backend: backend.name, success: false, error: `HTTP ${resp.status}` }
                      } catch (e: any) {
                        return { backend: backend.name, success: false, error: e.message }
                      }
                    })
                  )
                  
                  // Show summary
                  const totalCleaned = results.filter(r => r.success).reduce((sum, r) => sum + (r.cleaned || 0), 0)
                  let message = `✅ Cleaned up ${totalCleaned} sync logs across ${results.length} backends:\n\n`
                  results.forEach(r => {
                    message += `${r.backend}: ${r.success ? `${r.cleaned} cleaned` : `❌ ${r.error}`}\n`
                  })
                  alert(message)
                  // Refresh the page to show updated sync logs
                  window.location.reload()
                } catch (e: any) {
                  alert(e.message || 'Cleanup failed')
                }
              }}
              style={{fontSize:12,padding:'8px 16px'}}
            >
              🔧 Clean Up Stuck Sync Logs
            </button>
          </div>
          
          <div style={{borderTop:'1px solid var(--border)',paddingTop:12}}>
            <h3 style={{margin:0,fontSize:13,marginBottom:6}}>Unmanaged Product Resources</h3>
            <p className="muted" style={{fontSize:11,marginBottom:8}}>
              Remove resources whose product is no longer in the managed products list (e.g., products that were removed)
            </p>
            <button 
              className="btn secondary" 
              onClick={async () => {
                if (!confirm('Remove all resources belonging to products no longer in the managed list?\n\nThis will run on ALL backends.')) return
                try {
                  const token = getAuthToken()
                  if (!token) throw new Error('Not authenticated')
                  
                  // Fetch managed product names from primary to send to all backends
                  const managedResp = await fetch('/api/admin/products/managed', {
                    headers: { Authorization: `Bearer ${token}` }
                  })
                  if (!managedResp.ok) throw new Error('Failed to load managed products')
                  const managedData = await managedResp.json()
                  const managedNames: string[] = (managedData.products || []).map((p: any) => p.name)
                  if (managedNames.length === 0) throw new Error('Managed product list is empty — aborting to prevent data loss')

                  const backendsResp = await fetch('/api/admin/backend-endpoints', {
                    headers: { Authorization: `Bearer ${token}` }
                  })
                  if (!backendsResp.ok) throw new Error('Failed to load backends')
                  const allBackends = await backendsResp.json()
                  const enabledBackends = allBackends.filter((b: any) => b.enabled)
                  
                  const results = await Promise.all(
                    enabledBackends.map(async (backend: any) => {
                      try {
                        const url = backend.is_default
                          ? '/api/resources/cleanup-unmanaged'
                          : `/api/proxy/${backend.name}/api/resources/cleanup-unmanaged`
                        const resp = await fetch(url, {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                          body: JSON.stringify({ managed_names: managedNames }),
                        })
                        if (resp.ok) {
                          const data = await resp.json()
                          return { backend: backend.name, success: true, deleted: data.deleted || 0, products: data.per_product || {} }
                        }
                        return { backend: backend.name, success: false, error: `HTTP ${resp.status}` }
                      } catch (e: any) {
                        return { backend: backend.name, success: false, error: e.message }
                      }
                    })
                  )
                  
                  const totalDeleted = results.filter(r => r.success).reduce((sum, r) => sum + (r.deleted || 0), 0)
                  let message = `Cleaned up ${totalDeleted} resources across ${results.length} backends:\n\n`
                  results.forEach(r => {
                    if (r.success) {
                      const prods = Object.entries(r.products || {}).map(([p, c]) => `  ${p}: ${c}`).join('\n')
                      message += `${r.backend}: ${r.deleted} removed${prods ? '\n' + prods : ''}\n`
                    } else {
                      message += `${r.backend}: ${r.error}\n`
                    }
                  })
                  alert(message)
                  if (totalDeleted > 0) window.location.reload()
                } catch (e: any) {
                  alert(e.message || 'Cleanup failed')
                }
              }}
              style={{fontSize:12,padding:'8px 16px'}}
            >
              🧹 Clean Up Unmanaged Product Resources
            </button>
          </div>
          
          <div style={{borderTop:'1px solid var(--border)',paddingTop:12}}>
            <h3 style={{margin:0,fontSize:13,marginBottom:6,color:'#ef4444'}}>⚠️ Delete ALL Resources</h3>
            <p className="muted" style={{fontSize:11,marginBottom:8}}>
              <strong style={{color:'#ef4444'}}>DANGER ZONE:</strong> Remove ALL resources from the database. Use this to completely reset the resources table.
              This will run on ALL backends and cannot be undone. You'll need to run SYNC again to repopulate.
            </p>
            <button 
              className="btn" 
              onClick={async () => {
                const firstConfirm = confirm('⚠️ WARNING: This will DELETE ALL RESOURCES from the database!\n\nAre you absolutely sure?')
                if (!firstConfirm) return
                
                const secondConfirm = confirm('⚠️ FINAL WARNING: This action CANNOT BE UNDONE!\n\nAll resource data will be permanently deleted from ALL backends.\n\nType "DELETE ALL" in the next prompt to confirm.')
                if (!secondConfirm) return
                
                const userInput = prompt('Type "DELETE ALL" to confirm deletion:')
                if (userInput !== 'DELETE ALL') {
                  alert('Deletion cancelled - confirmation text did not match.')
                  return
                }
                
                try {
                  const token = getAuthToken()
                  if (!token) throw new Error('Not authenticated')
                  
                  // Get all enabled backends
                  const backendsResp = await fetch('/api/admin/backend-endpoints', {
                    headers: { Authorization: `Bearer ${token}` }
                  })
                  if (!backendsResp.ok) throw new Error('Failed to load backends')
                  const allBackends = await backendsResp.json()
                  const enabledBackends = allBackends.filter((b: any) => b.enabled)
                  
                  // Delete all resources on all backends in parallel
                  const results = await Promise.all(
                    enabledBackends.map(async (backend: any) => {
                      try {
                        const baseUrl = backend.is_default ? '' : backend.api_url.replace(/\/api\/?$/, '')
                        const resp = await fetch(`${baseUrl}/api/resources/cleanup-all`, {
                          method: 'POST',
                          headers: { Authorization: `Bearer ${token}` }
                        })
                        if (resp.ok) {
                          const data = await resp.json()
                          return { backend: backend.name, success: true, deleted: data.deleted || 0, per_platform: data.per_platform }
                        }
                        return { backend: backend.name, success: false, error: `HTTP ${resp.status}` }
                      } catch (e: any) {
                        return { backend: backend.name, success: false, error: e.message }
                      }
                    })
                  )
                  
                  // Show summary
                  const totalDeleted = results.filter(r => r.success).reduce((sum, r) => sum + (r.deleted || 0), 0)
                  let message = `✅ Deleted ${totalDeleted} resources across ${results.length} backends:\n\n`
                  results.forEach(r => {
                    if (r.success) {
                      message += `${r.backend}: ${r.deleted} deleted`
                      if (r.per_platform) {
                        message += ` (${Object.entries(r.per_platform).map(([p, c]) => `${p}:${c}`).join(', ')})`
                      }
                      message += '\n'
                    } else {
                      message += `${r.backend}: ❌ ${r.error}\n`
                    }
                  })
                  message += '\n⚠️ Run SYNC to repopulate resources.'
                  alert(message)
                  // Refresh the page to show empty table
                  window.location.reload()
                } catch (e: any) {
                  alert(e.message || 'Cleanup failed')
                }
              }}
              style={{fontSize:12,padding:'8px 16px',background:'#dc2626',borderColor:'#dc2626'}}
            >
              💣 Delete ALL Resources
            </button>
          </div>
        </div>
      </div>

      {/* Product Responsibility Management */}
      <div className="card" style={{ marginTop: 16 }}>
        <h2>Product Responsibility Management</h2>
        <p className="muted" style={{fontSize:12,marginBottom:16}}>
          Manage which products to monitor across all backends. The primary backend refreshes the product list daily at 01:00 AM from endoflife.date. Secondary backends sync this list from the primary.
        </p>
        
        <div style={{padding:10,background:'rgba(59,130,246,0.15)',border:'1px solid rgba(59,130,246,0.3)',borderRadius:6,marginBottom:16,fontSize:11,lineHeight:1.5}}>
          <strong>ℹ️ Multi-Backend Note:</strong> In a federated setup, the <strong>primary backend</strong> manages the product list and refreshes it daily from endoflife.date. 
          <strong>Secondary backends</strong> automatically fetch the managed product list from the primary backend. 
          Configure managed products via the <strong>primary backend's Admin UI only</strong>.
        </div>

        {/* Dynamic Discovery Mode */}
        <div style={{marginBottom:20,padding:12,background:dynamicMode?'rgba(22,163,74,0.15)':'var(--bg)',borderRadius:8,border:`1px solid ${dynamicMode?'rgba(22,163,74,0.4)':'var(--border)'}`}}>
          <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:8}}>
            <div>
              <h3 style={{margin:0,fontSize:13}}>🔍 Dynamic Discovery Mode</h3>
              <p className="muted" style={{fontSize:11,marginTop:4}}>
                {dynamicMode 
                  ? '✅ Enabled - Resources auto-discovered from Kubernetes based on managed products'
                  : '⚪ Disabled - Using static sources.txt file'}
              </p>
            </div>
            <button 
              className={dynamicMode ? "btn" : "btn secondary"}
              onClick={() => toggleDynamicMode(!dynamicMode)}
              style={{fontSize:11,padding:'6px 16px',background:dynamicMode?'#16a34a':undefined}}
            >
              {dynamicMode ? 'Disable' : 'Enable'}
            </button>
          </div>
          
          {dynamicMode && (
            <div style={{marginTop:12,paddingTop:12,borderTop:'1px solid var(--border)'}}>
              <button className="btn secondary" onClick={discoverNow} disabled={discovering} style={{fontSize:11,padding:'6px 16px',marginRight:8}}>
                {discovering ? 'Discovering...' : '🔎 Discover Resources Now'}
              </button>
              {discoveryResult && (
                <span className="muted" style={{fontSize:11}}>
                  Found {discoveryResult.discovered} resources on platform "{discoveryResult.platform}"
                </span>
              )}
            </div>
          )}
          
          {dynamicMode && discoveryResult && discoveryResult.sources && discoveryResult.sources.length > 0 && (
            <div style={{marginTop:12,maxHeight:200,overflowY:'auto',fontSize:10,background:'var(--bg)',padding:8,borderRadius:4}}>
              <div style={{fontWeight:500,marginBottom:6}}>Discovered Resources:</div>
              {discoveryResult.sources.slice(0, 20).map((s: any, i: number) => (
                <div key={i} style={{padding:'2px 0',color:'var(--muted)'}}>
                  • {s.namespace}/{s.resource_name} ({s.kind}) → {s.product_name}
                </div>
              ))}
              {discoveryResult.sources.length > 20 && (
                <div style={{padding:'2px 0',color:'var(--muted)',fontStyle:'italic'}}>
                  ... and {discoveryResult.sources.length - 20} more
                </div>
              )}
            </div>
          )}
        </div>

        <div style={{marginBottom:20}}>
          <button className="btn" onClick={refreshProductList} disabled={refreshingProducts} style={{marginRight:8}}>
            {refreshingProducts ? 'Refreshing...' : '🔄 Refresh Product List'}
          </button>
          <span className="muted" style={{fontSize:11}}>
            {allProducts.length} products available
          </span>
        </div>

        {/* Manual Product Entry */}
        <div style={{marginBottom:20,padding:12,background:'var(--bg)',borderRadius:8,border:'1px solid var(--border)'}}>
          <h3 style={{margin:'0 0 8px 0',fontSize:13}}>Add Manual Product</h3>
          <p className="muted" style={{fontSize:11,marginBottom:10}}>
            Add a product that's not in the endoflife.date API list
          </p>
          <div style={{display:'flex',gap:8}}>
            <input
              className="input"
              type="text"
              placeholder="Product name (e.g., my-custom-app)"
              value={manualProductName}
              onChange={e => setManualProductName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && addManualProduct()}
              style={{flex:1}}
            />
            <button className="btn secondary" onClick={addManualProduct}>Add</button>
          </div>
        </div>

        {/* Dual List Selection */}
        <div style={{display:'grid',gridTemplateColumns:'1fr auto 1fr',gap:12,alignItems:'start'}}>
          {/* Left: Available Products */}
          <div style={{border:'1px solid var(--border)',borderRadius:8,padding:12,background:'var(--bg)'}}>
            <h3 style={{margin:'0 0 8px 0',fontSize:13}}>All Products ({allProducts.length})</h3>
            <input
              className="input"
              type="text"
              placeholder="Search..."
              value={productSearch}
              onChange={e => setProductSearch(e.target.value)}
              style={{marginBottom:8,width:'100%'}}
            />
            <div style={{maxHeight:400,overflowY:'auto',fontSize:11}}>
              {allProducts
                .filter(p => !managedProducts.some(m => m.name === p.name))
                .filter(p => p.name.toLowerCase().includes(productSearch.toLowerCase()))
                .map(product => (
                  <div
                    key={product.id}
                    style={{
                      padding:'6px 8px',
                      marginBottom:4,
                      background:'var(--panel)',
                      borderRadius:4,
                      cursor:'pointer',
                      display:'flex',
                      justifyContent:'space-between',
                      alignItems:'center',
                      border:'1px solid transparent'
                    }}
                    onMouseEnter={e => e.currentTarget.style.borderColor = 'var(--accent)'}
                    onMouseLeave={e => e.currentTarget.style.borderColor = 'transparent'}
                  >
                    <span>{product.name}</span>
                    <button
                      className="btn secondary"
                      onClick={() => addToManaged(product.name)}
                      style={{fontSize:9,padding:'2px 6px'}}
                    >
                      Add →
                    </button>
                  </div>
                ))}
            </div>
          </div>

          {/* Middle: Arrow */}
          <div style={{display:'flex',alignItems:'center',justifyContent:'center',padding:'0 8px'}}>
            <span style={{fontSize:24,color:'var(--accent)'}}>⇄</span>
          </div>

          {/* Right: Managed Products */}
          <div style={{border:'1px solid var(--accent)',borderRadius:8,padding:12,background:'var(--bg)'}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
              <div>
                <h3 style={{margin:0,fontSize:13,color:'var(--accent)'}}>My Managed Products ({managedProducts.length})</h3>
                <p className="muted" style={{fontSize:10,marginTop:4}}>Products you are responsible for</p>
              </div>
              <div style={{display:'flex',gap:6}}>
                <button
                  className="btn secondary"
                  onClick={exportManagedProducts}
                  style={{fontSize:9,padding:'4px 8px'}}
                  title="Export to JSON"
                >
                  📤 Export
                </button>
                <label className="btn secondary" style={{fontSize:9,padding:'4px 8px',cursor:'pointer'}} title="Import from JSON">
                  📥 Import
                  <input
                    type="file"
                    accept=".json"
                    style={{display:'none'}}
                    onChange={importManagedProducts}
                  />
                </label>
              </div>
            </div>
            <div style={{maxHeight:400,overflowY:'auto',fontSize:11}}>
              {managedProducts.length === 0 ? (
                <p className="muted" style={{fontSize:11,textAlign:'center',padding:20}}>
                  No products managed yet
                </p>
              ) : (
                managedProducts.map(product => (
                  <div
                    key={product.id}
                    style={{
                      padding:'6px 8px',
                      marginBottom:4,
                      background:'var(--panel)',
                      borderRadius:4,
                      border:'1px solid var(--accent)'
                    }}
                  >
                    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                      <div style={{flex:1}}>
                        <span>{product.name}</span>
                        {product.is_manual && (
                          <span className="pill" style={{marginLeft:6,fontSize:8,padding:'1px 4px',background:'#dc2626',color:'#fff'}}>
                            Manual
                          </span>
                        )}
                      </div>
                      <button
                        className="btn danger"
                        onClick={() => removeFromManaged(product.id)}
                        style={{fontSize:9,padding:'2px 6px'}}
                      >
                        Remove
                      </button>
                    </div>
                    <div style={{display:'flex',alignItems:'center',gap:4,marginTop:3}}>
                      <span className="muted" style={{fontSize:9,whiteSpace:'nowrap'}}>Image Pattern:</span>
                      <input
                        className="input"
                        style={{fontSize:9,padding:'1px 4px',flex:1,height:20}}
                        placeholder="e.g. apache/apisix, yourorg/custom-runner"
                        defaultValue={product.image_pattern || ''}
                        onBlur={e => {
                          const val = e.target.value.trim()
                          if (val !== (product.image_pattern || '')) {
                            updateImagePattern(product.id, val)
                          }
                        }}
                        onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
                      />
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Compare Settings Card */}
      <div className="card" style={{ marginTop: 16 }}>
        <h2>🔍 Compare Settings</h2>
        <p className="muted">Configure cluster comparison feature settings.</p>
        
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginTop: 16, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ fontSize: 13, whiteSpace: 'nowrap' }}>Max Saved Reports:</label>
            <input 
              type="number" 
              className="input" 
              value={maxComparisonReports} 
              onChange={e => setMaxComparisonReports(Math.max(1, Math.min(100, Number(e.target.value))))}
              min={1}
              max={100}
              style={{ width: 80 }}
            />
          </div>
          <span className="muted" style={{ fontSize: 11 }}>
            When limit is reached, oldest report will be automatically deleted (1-100)
          </span>
        </div>
        
        <div style={{ marginTop: 16 }}>
          <button 
            className="btn" 
            onClick={saveCompareSettings} 
            disabled={savingCompareSettings}
            style={{ fontSize: 11, padding: '6px 16px' }}
          >
            {savingCompareSettings ? 'Saving...' : 'Save Compare Settings'}
          </button>
        </div>
      </div>

      {/* LDAP/AD Integration Card */}
      <div className="card" style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <h2>🔐 LDAP/AD Integration</h2>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input 
              type="checkbox" 
              checked={ldapEnabled} 
              onChange={e => setLdapEnabled(e.target.checked)}
              style={{ width: 18, height: 18 }}
            />
            <span style={{ fontSize: 12 }}>Enable LDAP Authentication</span>
          </label>
        </div>
        <p className="muted">Configure LDAP/Active Directory integration for user authentication.</p>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 0, marginTop: 16, borderBottom: '1px solid var(--border)' }}>
          {(['server', 'users', 'groups', 'test'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setLdapTab(tab)}
              style={{
                padding: '10px 20px',
                background: ldapTab === tab ? 'var(--panel)' : 'transparent',
                border: 'none',
                borderBottom: ldapTab === tab ? '2px solid var(--brand)' : '2px solid transparent',
                color: ldapTab === tab ? 'var(--brand)' : 'var(--text)',
                cursor: 'pointer',
                fontSize: 13,
                fontWeight: ldapTab === tab ? 600 : 400,
                textTransform: 'capitalize'
              }}
            >
              {tab === 'server' ? '🖥️ Server' : tab === 'users' ? '👤 Users' : tab === 'groups' ? '👥 Groups' : '🧪 Test'}
            </button>
          ))}
        </div>

        {/* Server Tab */}
        {ldapTab === 'server' && (
          <div style={{ marginTop: 16 }}>
            <h3 style={{ fontSize: 13, marginBottom: 12 }}>Server Connection</h3>
            <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
              <input
                className="input"
                placeholder="LDAP Server URL (ldaps://ldaps.example.com)"
                value={ldapServerUrl}
                onChange={e => setLdapServerUrl(e.target.value)}
                style={{ flex: 1, minWidth: 300 }}
              />
              <input
                className="input"
                type="number"
                placeholder="Port"
                value={ldapPort}
                onChange={e => setLdapPort(Number(e.target.value))}
                style={{ width: 80 }}
              />
              <button className="btn secondary" onClick={() => setLdapPort(ldapUseSsl ? 636 : 389)} style={{ fontSize: 11 }}>
                Detect Port
              </button>
            </div>
            
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                <input 
                  type="checkbox" 
                  checked={ldapUseSsl} 
                  onChange={e => { setLdapUseSsl(e.target.checked); setLdapPort(e.target.checked ? 636 : 389) }}
                />
                Use SSL/TLS (LDAPS)
              </label>
            </div>

            {/* SSL Certificate Options */}
            {ldapUseSsl && (
              <div style={{ marginBottom: 16, padding: 12, borderRadius: 8, background: 'rgba(99,102,241,0.05)', border: '1px solid var(--border)' }}>
                <h4 style={{ fontSize: 12, marginBottom: 8 }}>🔒 SSL Certificate Settings</h4>
                
                <div style={{ marginBottom: 12 }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, cursor: 'pointer' }}>
                    <input 
                      type="checkbox" 
                      checked={ldapSkipCertVerify} 
                      onChange={e => setLdapSkipCertVerify(e.target.checked)}
                    />
                    Skip certificate verification (not recommended for production)
                  </label>
                </div>

                {!ldapSkipCertVerify && (
                  <div>
                    <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>
                      CA Certificate (PEM format) - for self-signed certificates
                    </label>
                    <textarea
                      className="input"
                      placeholder="-----BEGIN CERTIFICATE-----
MIIDXTCCAkWgAwIBAgIJAJC1...
-----END CERTIFICATE-----"
                      value={ldapCaCert}
                      onChange={e => setLdapCaCert(e.target.value)}
                      style={{ 
                        width: '100%', 
                        minHeight: 120, 
                        fontFamily: 'monospace', 
                        fontSize: 11,
                        resize: 'vertical'
                      }}
                    />
                    <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
                      Paste the CA certificate content here if your LDAP server uses a self-signed certificate.
                      Leave empty to use system CA certificates.
                    </div>
                  </div>
                )}
              </div>
            )}

            <h3 style={{ fontSize: 13, marginBottom: 12 }}>Bind Credentials</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 12 }}>
              <input
                className="input"
                placeholder="Bind DN (CN=service_account,OU=Service Accounts,DC=example,DC=com)"
                value={ldapBindDn}
                onChange={e => setLdapBindDn(e.target.value)}
              />
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  className="input"
                  type="password"
                  placeholder="Bind Password"
                  value={ldapBindPassword}
                  onChange={e => setLdapBindPassword(e.target.value)}
                  style={{ flex: 1 }}
                />
                <button 
                  className="btn secondary" 
                  onClick={saveLdapConfig} 
                  disabled={ldapSaving}
                  style={{ fontSize: 11 }}
                >
                  {ldapSaving ? 'Saving...' : 'Save Credentials'}
                </button>
              </div>
            </div>

            <h3 style={{ fontSize: 13, marginBottom: 12 }}>Base DN</h3>
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              <input
                className="input"
                placeholder="Base DN (DC=example,DC=com)"
                value={ldapBaseDn}
                onChange={e => setLdapBaseDn(e.target.value)}
                style={{ flex: 1 }}
              />
              <button 
                className="btn secondary" 
                onClick={detectBaseDn} 
                disabled={ldapDetecting || !ldapServerUrl}
                style={{ fontSize: 11 }}
              >
                {ldapDetecting ? 'Detecting...' : 'Detect Base DN'}
              </button>
              <button 
                className="btn secondary" 
                onClick={testBaseDn} 
                disabled={ldapTesting || !ldapBaseDn}
                style={{ fontSize: 11 }}
              >
                Test Base DN
              </button>
            </div>

            <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
              <button 
                className="btn" 
                onClick={testLdapConnection} 
                disabled={ldapTesting || !ldapServerUrl || !ldapBindDn}
              >
                {ldapTesting ? 'Testing...' : '🔌 Test Connection'}
              </button>
              <button className="btn" onClick={saveLdapConfig} disabled={ldapSaving}>
                {ldapSaving ? 'Saving...' : '💾 Save Configuration'}
              </button>
            </div>
          </div>
        )}

        {/* Users Tab (Login Attributes) */}
        {ldapTab === 'users' && (
          <div style={{ marginTop: 16 }}>
            <h3 style={{ fontSize: 13, marginBottom: 12 }}>User Search Settings</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
              <div>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>User Search Filter</label>
                <input
                  className="input"
                  placeholder="(&(objectClass=user)(sAMAccountName={username}))"
                  value={ldapUserSearchFilter}
                  onChange={e => setLdapUserSearchFilter(e.target.value)}
                  style={{ width: '100%' }}
                />
                <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
                  Use {'{username}'} as placeholder for the login username
                </div>
              </div>
              <div>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>User Search Base (optional)</label>
                <input
                  className="input"
                  placeholder="OU=Users,DC=example,DC=com (leave empty to use Base DN)"
                  value={ldapUserSearchBase}
                  onChange={e => setLdapUserSearchBase(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
            </div>

            <h3 style={{ fontSize: 13, marginBottom: 12 }}>Attribute Mapping</h3>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
              <div>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Username Attribute</label>
                <input
                  className="input"
                  placeholder="sAMAccountName"
                  value={ldapUsernameAttr}
                  onChange={e => setLdapUsernameAttr(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Email Attribute</label>
                <input
                  className="input"
                  placeholder="mail"
                  value={ldapEmailAttr}
                  onChange={e => setLdapEmailAttr(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Display Name Attribute</label>
                <input
                  className="input"
                  placeholder="displayName"
                  value={ldapDisplayNameAttr}
                  onChange={e => setLdapDisplayNameAttr(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
            </div>

            <div style={{ marginTop: 16 }}>
              <button className="btn" onClick={saveLdapConfig} disabled={ldapSaving}>
                {ldapSaving ? 'Saving...' : '💾 Save User Settings'}
              </button>
            </div>
          </div>
        )}

        {/* Groups Tab */}
        {ldapTab === 'groups' && (
          <div style={{ marginTop: 16 }}>
            {/* Default Role Info */}
            <div style={{ 
              padding: 12, 
              borderRadius: 8, 
              background: 'rgba(59, 130, 246, 0.1)', 
              border: '1px solid rgba(59, 130, 246, 0.3)',
              marginBottom: 16,
              fontSize: 12
            }}>
              <strong>ℹ️ Default Behavior:</strong> All LDAP users get <strong>read-only</strong> role by default. 
              Configure groups below to assign higher roles.
            </div>

            <h3 style={{ fontSize: 13, marginBottom: 12 }}>Role Mapping (Optional)</h3>
            <p className="muted" style={{ fontSize: 11, marginBottom: 12 }}>
              Map LDAP groups to application roles. Priority: Admin &gt; Analyst &gt; Read-Only. 
              Leave empty to use default read-only role for all LDAP users.
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>
                  <span style={{ color: 'var(--ok)' }}>●</span> Admin Group DN (full access, can configure system)
                </label>
                <input
                  className="input"
                  placeholder="CN=PatchMgmt-Admins,OU=Groups,DC=example,DC=com"
                  value={ldapAdminGroupDn}
                  onChange={e => setLdapAdminGroupDn(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>
                  <span style={{ color: 'var(--warning)' }}>●</span> Analyst Group DN (can compare clusters, create reports)
                </label>
                <input
                  className="input"
                  placeholder="CN=PatchMgmt-Analysts,OU=Groups,DC=example,DC=com"
                  value={ldapAnalystGroupDn}
                  onChange={e => setLdapAnalystGroupDn(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>
                  <span style={{ color: 'var(--muted)' }}>●</span> Read-Only Group DN (optional, view only access)
                </label>
                <input
                  className="input"
                  placeholder="CN=PatchMgmt-Users,OU=Groups,DC=example,DC=com (optional)"
                  value={ldapReadonlyGroupDn}
                  onChange={e => setLdapReadonlyGroupDn(e.target.value)}
                  style={{ width: '100%' }}
                />
                <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
                  If empty, all LDAP users without admin/analyst group will get read-only access
                </div>
              </div>
            </div>

            <h3 style={{ fontSize: 13, marginTop: 20, marginBottom: 12 }}>Group Search Settings (Advanced)</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Group Search Base (optional)</label>
                <input
                  className="input"
                  placeholder="OU=Groups,DC=example,DC=com"
                  value={ldapGroupSearchBase}
                  onChange={e => setLdapGroupSearchBase(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Group Search Filter (optional)</label>
                <input
                  className="input"
                  placeholder="(objectClass=group)"
                  value={ldapGroupSearchFilter}
                  onChange={e => setLdapGroupSearchFilter(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
            </div>

            <div style={{ marginTop: 16 }}>
              <button className="btn" onClick={saveLdapConfig} disabled={ldapSaving}>
                {ldapSaving ? 'Saving...' : '💾 Save Group Settings'}
              </button>
            </div>
          </div>
        )}

        {/* Test Tab */}
        {ldapTab === 'test' && (
          <div style={{ marginTop: 16 }}>
            <h3 style={{ fontSize: 13, marginBottom: 12 }}>Test User Authentication</h3>
            <p className="muted" style={{ fontSize: 11, marginBottom: 16 }}>
              Test LDAP authentication with a real user account. This will verify the configuration works 
              and show the assigned role based on group membership.
            </p>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 400 }}>
              <div>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Username</label>
                <input
                  className="input"
                  placeholder="Enter LDAP username"
                  value={ldapTestUsername}
                  onChange={e => setLdapTestUsername(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Password</label>
                <input
                  className="input"
                  type="password"
                  placeholder="Enter LDAP password"
                  value={ldapTestPassword}
                  onChange={e => setLdapTestPassword(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
              <button 
                className="btn" 
                onClick={testUserLogin}
                disabled={ldapTesting || !ldapTestUsername || !ldapTestPassword || !ldapEnabled}
                style={{ marginTop: 8 }}
              >
                {ldapTesting ? 'Testing...' : '🔐 Test Login'}
              </button>
            </div>

            {!ldapEnabled && (
              <div style={{ 
                marginTop: 16, 
                padding: 12, 
                borderRadius: 8, 
                background: 'rgba(239, 68, 68, 0.1)',
                border: '1px solid var(--danger)',
                fontSize: 12
              }}>
                ⚠️ LDAP is not enabled. Enable it first from the checkbox above.
              </div>
            )}

            {ldapTestUserResult && (
              <div style={{ 
                marginTop: 16, 
                padding: 16, 
                borderRadius: 8, 
                background: ldapTestUserResult.success ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                border: `1px solid ${ldapTestUserResult.success ? 'var(--ok)' : 'var(--danger)'}`
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ fontSize: 18 }}>{ldapTestUserResult.success ? '✅' : '❌'}</span>
                  <strong>{ldapTestUserResult.message}</strong>
                </div>
                
                {ldapTestUserResult.user_info && (
                  <div style={{ fontSize: 12, marginTop: 12 }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                      <tbody>
                        <tr>
                          <td style={{ padding: '4px 8px', color: 'var(--muted)' }}>Username:</td>
                          <td style={{ padding: '4px 8px' }}>{ldapTestUserResult.user_info.username}</td>
                        </tr>
                        <tr>
                          <td style={{ padding: '4px 8px', color: 'var(--muted)' }}>Display Name:</td>
                          <td style={{ padding: '4px 8px' }}>{ldapTestUserResult.user_info.display_name || '-'}</td>
                        </tr>
                        <tr>
                          <td style={{ padding: '4px 8px', color: 'var(--muted)' }}>Email:</td>
                          <td style={{ padding: '4px 8px' }}>{ldapTestUserResult.user_info.email || '-'}</td>
                        </tr>
                        <tr>
                          <td style={{ padding: '4px 8px', color: 'var(--muted)' }}>Assigned Role:</td>
                          <td style={{ padding: '4px 8px' }}>
                            <span style={{ 
                              padding: '2px 8px', 
                              borderRadius: 4, 
                              background: ldapTestUserResult.user_info.assigned_role === 'admin' 
                                ? 'var(--ok)' 
                                : ldapTestUserResult.user_info.assigned_role === 'analyst'
                                ? 'var(--warning)'
                                : 'var(--muted)',
                              color: 'white',
                              fontSize: 11
                            }}>
                              {ldapTestUserResult.user_info.assigned_role}
                            </span>
                          </td>
                        </tr>
                        {ldapTestUserResult.user_info.groups?.length > 0 && (
                          <tr>
                            <td style={{ padding: '4px 8px', color: 'var(--muted)', verticalAlign: 'top' }}>Groups:</td>
                            <td style={{ padding: '4px 8px', fontSize: 10, wordBreak: 'break-all' }}>
                              {ldapTestUserResult.user_info.groups.slice(0, 5).map((g: string, i: number) => (
                                <div key={i} style={{ marginBottom: 2 }}>{g}</div>
                              ))}
                              {ldapTestUserResult.user_info.groups.length > 5 && (
                                <div style={{ color: 'var(--muted)' }}>...and {ldapTestUserResult.user_info.groups.length - 5} more</div>
                              )}
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Status Message */}
        {ldapTestResult && (
          <div style={{ 
            marginTop: 16, 
            padding: 12, 
            borderRadius: 8, 
            background: ldapTestResult.success ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)',
            border: `1px solid ${ldapTestResult.success ? 'var(--ok)' : 'var(--danger)'}`,
            display: 'flex',
            alignItems: 'center',
            gap: 8
          }}>
            <span>{ldapTestResult.success ? '✅' : '❌'}</span>
            <span style={{ fontSize: 12 }}>{ldapTestResult.message}</span>
          </div>
        )}

        {/* Info Box */}
        <div style={{ 
          marginTop: 16, 
          padding: 12, 
          borderRadius: 8, 
          background: 'rgba(99,102,241,0.05)', 
          fontSize: 11, 
          color: 'var(--muted)' 
        }}>
          <div style={{ marginBottom: 8 }}>
            💡 <strong>How it works:</strong> When LDAP is enabled, users can log in with their domain credentials. 
            The system first tries LDAP authentication, then falls back to local accounts.
          </div>
          <div style={{ marginBottom: 8 }}>
            👤 <strong>Default Role:</strong> All LDAP users get <strong>read-only</strong> role by default. 
            Configure group mappings to assign admin or analyst roles.
          </div>
          <div>
            🌐 <strong>Federation:</strong> Each backend has its own LDAP configuration. 
            Configure LDAP separately for primary and secondary backends.
          </div>
        </div>
      </div>

      {/* SMTP / Email Integration Card */}
      <div className="card" style={{ marginTop: 16 }}>
        <h2>SMTP / Email Integration</h2>
        <p className="muted">Configure email delivery for automated CSV reports. Supports both authenticated and anonymous SMTP servers.</p>
        
        <h3 style={{fontSize:14,marginTop:16,marginBottom:8}}>SMTP Server Configuration</h3>
        <div className="inputs" style={{marginBottom:12,flexWrap:'wrap'}}>
          <input className="input" placeholder="SMTP Server" value={smtpServer} onChange={e => setSmtpServer(e.target.value)} style={{minWidth:200}} />
          <input className="input" type="number" placeholder="Port" value={smtpPort} onChange={e => setSmtpPort(Number(e.target.value))} style={{width:80}} />
          <input className="input" placeholder="From Email" value={smtpFromEmail} onChange={e => setSmtpFromEmail(e.target.value)} style={{minWidth:220}} />
        </div>
        
        <h4 style={{fontSize:12,marginTop:12,marginBottom:8,color:'var(--muted)'}}>Authentication (Optional - leave empty for anonymous SMTP)</h4>
        <div className="inputs" style={{marginBottom:12,flexWrap:'wrap'}}>
          <input className="input" placeholder="Username" value={smtpUsername} onChange={e => setSmtpUsername(e.target.value)} style={{minWidth:180}} />
          <input className="input" type="password" placeholder="Password" value={smtpPassword} onChange={e => setSmtpPassword(e.target.value)} style={{minWidth:180}} />
          <label style={{display:'flex',alignItems:'center',gap:6,fontSize:11,whiteSpace:'nowrap'}}>
            <input type="checkbox" checked={smtpUseTls} onChange={e => setSmtpUseTls(e.target.checked)} />
            Use TLS/SSL
          </label>
        </div>

        <h4 style={{fontSize:12,marginTop:12,marginBottom:8,color:'var(--muted)'}}>Scheduled Report (Optional)</h4>
        <div style={{marginBottom:16}}>
          <input className="input" placeholder="Cron Schedule (e.g., 0 8 * * 1 = Every Monday 8AM)" value={smtpSchedule} onChange={e => setSmtpSchedule(e.target.value)} style={{minWidth:350}} />
        </div>

        <div style={{display:'flex',gap:8,marginBottom:16}}>
          <button className="btn secondary" onClick={testSmtpConnection} disabled={smtpTesting || !smtpServer} style={{fontSize:11,padding:'4px 12px'}}>
            {smtpTesting ? 'Testing...' : 'Test Connection'}
          </button>
          <button className="btn" onClick={saveSmtpConfig} disabled={smtpSaving || !smtpServer} style={{fontSize:11,padding:'4px 12px'}}>
            {smtpSaving ? 'Saving...' : 'Save Configuration'}
          </button>
        </div>

        <h3 style={{fontSize:14,marginTop:24,marginBottom:8}}>Email Recipients</h3>
        <div className="inputs" style={{marginBottom:16,flexWrap:'wrap'}}>
          <input className="input" placeholder="Email Address" value={newRecipientEmail} onChange={e => setNewRecipientEmail(e.target.value)} style={{minWidth:220}} />
          <input className="input" placeholder="Name (optional)" value={newRecipientName} onChange={e => setNewRecipientName(e.target.value)} style={{minWidth:150}} />
          <button className="btn secondary" onClick={addRecipient} disabled={!newRecipientEmail} style={{fontSize:11,padding:'4px 12px'}}>
            Add Recipient
          </button>
        </div>

        {recipients.length === 0 ? (
          <p className="muted" style={{fontSize:11}}>No email recipients configured.</p>
        ) : (
          <div className="tableWrap">
            <table className="compactTable" style={{width:'100%'}}>
              <thead>
                <tr style={{borderBottom:'1px solid var(--border)'}}>
                  <th style={{textAlign:'left',padding:'6px',fontSize:11}}>Email</th>
                  <th style={{textAlign:'left',padding:'6px',fontSize:11}}>Name</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>Status</th>
                  <th style={{textAlign:'center',padding:'6px',fontSize:11}}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {recipients.map(r => (
                  <tr key={r.id} style={{borderBottom:'1px solid var(--border)'}}>
                    <td style={{padding:'6px',fontSize:11}}>{r.email}</td>
                    <td style={{padding:'6px',fontSize:11}}>{r.name || '-'}</td>
                    <td style={{padding:'6px',textAlign:'center'}}>
                      <span className={`pill ${r.enabled ? 'same' : 'unknown'}`} style={{fontSize:9}}>
                        {r.enabled ? 'On' : 'Off'}
                      </span>
                    </td>
                    <td style={{padding:'6px',textAlign:'center'}}>
                      <div style={{display:'flex',gap:4,justifyContent:'center'}}>
                        <button className="btn secondary" onClick={async () => {
                          const token = getAuthToken()
                          if (!token) return
                          const body = new URLSearchParams()
                          body.set('enabled', String(!r.enabled))
                          await fetch(`/api/admin/email-recipients/${r.id}`, {
                            method: 'PUT',
                            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
                            body
                          })
                          await loadRecipients()
                        }} style={{fontSize:9,padding:'2px 5px'}}>
                          {r.enabled ? 'Disable' : 'Enable'}
                        </button>
                        <button className="btn secondary" onClick={async () => {
                          if (!confirm(`Delete ${r.email}?`)) return
                          const token = getAuthToken()
                          if (!token) return
                          await fetch(`/api/admin/email-recipients/${r.id}`, {
                            method: 'DELETE',
                            headers: { Authorization: `Bearer ${token}` }
                          })
                          await loadRecipients()
                        }} style={{fontSize:9,padding:'2px 5px'}}>Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h3 style={{fontSize:14,marginTop:24,marginBottom:8}}>Send CSV Report Now</h3>
        <div style={{marginBottom:12}}>
          <div style={{display:'flex',gap:8,marginBottom:8,flexWrap:'wrap'}}>
            <button className="btn secondary" onClick={() => setSelectedRecipients(new Set())} style={{fontSize:10,padding:'3px 8px'}}>
              Send to All Enabled
            </button>
            <button className="btn secondary" onClick={() => {
              const enabled = recipients.filter(r => r.enabled).map(r => r.id)
              setSelectedRecipients(new Set(enabled))
            }} style={{fontSize:10,padding:'3px 8px'}}>
              Select All Enabled
            </button>
            <button className="btn secondary" onClick={() => setSelectedRecipients(new Set())} style={{fontSize:10,padding:'3px 8px'}}>
              Clear Selection
            </button>
          </div>
          {selectedRecipients.size > 0 && (
            <p className="muted" style={{fontSize:10,marginBottom:8}}>
              Selected: {selectedRecipients.size} recipient(s)
            </p>
          )}
        </div>
        <div style={{marginBottom:16}}>
          <input className="input" placeholder="Email Subject" value={emailSubject} onChange={e => setEmailSubject(e.target.value)} style={{marginBottom:8,width:'100%',maxWidth:500}} />
          <textarea className="input" placeholder="Email Body" value={emailBody} onChange={e => setEmailBody(e.target.value)} style={{width:'100%',maxWidth:500,minHeight:80,fontFamily:'inherit',fontSize:'inherit',padding:8}} />
        </div>
        <button className="btn" onClick={sendEmail} disabled={sendingEmail || recipients.length === 0 || !smtpServer} style={{fontSize:11,padding:'6px 16px'}}>
          {sendingEmail ? 'Sending...' : 'Send CSV Report Now'}
        </button>
        {!smtpServer && <p className="muted" style={{fontSize:10,marginTop:8}}>⚠️ Configure SMTP settings first</p>}
        {recipients.length === 0 && smtpServer && <p className="muted" style={{fontSize:10,marginTop:8}}>⚠️ Add at least one recipient</p>}
      </div>

      {/* Schedule Edit Modal */}
      {showScheduleModal && (
        <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.92)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:2000}}>
          <div style={{background:'var(--panel)',padding:24,borderRadius:8,minWidth:500,maxWidth:'90vw',boxShadow:'0 8px 32px rgba(0,0,0,0.5)'}}>
            <h2 style={{marginTop:0,marginBottom:16}}>Edit Auto-Sync Schedule</h2>
            <p className="muted" style={{fontSize:12,marginBottom:16}}>
              Configure auto-sync schedule for <strong>{modalBackendName}</strong>
            </p>
            
            <div style={{marginBottom:20}}>
              <label style={{display:'block',marginBottom:6,fontSize:13,fontWeight:500}}>Schedule (cron format)</label>
              <input
                className="input"
                type="text"
                placeholder="e.g. 0 3 * * * (daily at 3 AM)"
                value={modalSchedule}
                onChange={e => setModalSchedule(e.target.value)}
                style={{width:'100%'}}
                autoFocus
              />
              <div style={{marginTop:8,fontSize:11,color:'var(--muted)'}}>
                <div><strong>Examples:</strong></div>
                <div style={{marginLeft:12,marginTop:4}}>
                  <div>• <code style={{background:'var(--bg)',padding:'2px 4px',borderRadius:3}}>0 1 * * *</code> - Daily at 1:00 AM</div>
                  <div>• <code style={{background:'var(--bg)',padding:'2px 4px',borderRadius:3}}>0 3 * * *</code> - Daily at 3:00 AM</div>
                  <div>• <code style={{background:'var(--bg)',padding:'2px 4px',borderRadius:3}}>0 */6 * * *</code> - Every 6 hours</div>
                </div>
              </div>
            </div>

            <div style={{display:'flex',gap:8,justifyContent:'flex-end'}}>
              <button
                className="btn secondary"
                onClick={() => setShowScheduleModal(false)}
                disabled={savingAutoSync}
              >
                Cancel
              </button>
              <button
                className="btn"
                onClick={onSaveScheduleModal}
                disabled={savingAutoSync || !modalSchedule.trim()}
              >
                {savingAutoSync ? 'Saving...' : 'Save Schedule'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function PasswordChanger(): JSX.Element {
  const [pw1, setPw1] = useState('')
  const [pw2, setPw2] = useState('')
  const [msg, setMsg] = useState('')
  return (
    <div className="inputs" style={{ flexWrap: 'wrap' }}>
      <input className="input" type="password" placeholder="new password" value={pw1} onChange={(e) => setPw1(e.target.value)} />
      <input className="input" type="password" placeholder="confirm password" value={pw2} onChange={(e) => setPw2(e.target.value)} />
      <button className="btn" onClick={async () => {
        setMsg('')
        if (pw1 !== pw2) { setMsg('Passwords do not match'); return }
        try {
          const token = sessionStorage.getItem('token') || ''
          const body = new URLSearchParams()
          body.set('new_password', pw1)
          body.set('confirm_password', pw2)
          const resp = await fetch('/api/auth/reset-password', { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' }, body })
          if (!resp.ok) throw new Error('Failed')
          setMsg('Password updated')
          setPw1(''); setPw2('')
        } catch (e) {
          setMsg('Password update failed')
        }
      }}>Save</button>
      {msg && <span className="muted" style={{ fontSize: 12 }}>{msg}</span>}
    </div>
  )
}

function SecurityThresholdsConfig(): JSX.Element {
  const [redRiskFactor, setRedRiskFactor] = useState(10)
  const [redCritical, setRedCritical] = useState(0)
  const [orangeRiskFactorMin, setOrangeRiskFactorMin] = useState(5)
  const [orangeRiskFactorMax, setOrangeRiskFactorMax] = useState(10)
  const [orangeCritical, setOrangeCritical] = useState(0)
  const [greenRiskFactor, setGreenRiskFactor] = useState(5)
  const [greenCritical, setGreenCritical] = useState(0)
  const [thresholdMsg, setThresholdMsg] = useState('')

  const loadThresholds = async () => {
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/admin/security-thresholds', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const d = await resp.json()
        setRedRiskFactor(d.red?.riskFactor ?? 10)
        setRedCritical(d.red?.critical ?? 0)
        setOrangeRiskFactorMin(d.orange?.riskFactorMin ?? 5)
        setOrangeRiskFactorMax(d.orange?.riskFactorMax ?? 10)
        setOrangeCritical(d.orange?.critical ?? 0)
        setGreenRiskFactor(d.green?.riskFactor ?? 5)
        setGreenCritical(d.green?.critical ?? 0)
      }
    } catch {}
  }

  React.useEffect(() => { void loadThresholds() }, [])

  const saveThresholds = async () => {
    setThresholdMsg('')
    try {
      const token = sessionStorage.getItem('token') || ''
      const body = new URLSearchParams()
      body.set('red_risk_factor', String(redRiskFactor))
      body.set('red_critical', String(redCritical))
      body.set('orange_risk_factor_min', String(orangeRiskFactorMin))
      body.set('orange_risk_factor_max', String(orangeRiskFactorMax))
      body.set('orange_critical', String(orangeCritical))
      body.set('green_risk_factor', String(greenRiskFactor))
      body.set('green_critical', String(greenCritical))
      const resp = await fetch('/api/admin/security-thresholds', { 
        method: 'POST', 
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' }, 
        body 
      })
      if (!resp.ok) throw new Error('Save failed')
      setThresholdMsg('Saved')
    } catch (e: any) {
      setThresholdMsg(e.message || 'Save failed')
    }
  }

  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 12, lineHeight: 1.5 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <span style={{ width: 12, height: 12, borderRadius: 3, background: '#dc2626', display: 'inline-block' }}></span>
          <span><strong>RED:</strong> Risk Factor &gt; {redRiskFactor} OR Critical &gt; {redCritical}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <span style={{ width: 12, height: 12, borderRadius: 3, background: '#d97706', display: 'inline-block' }}></span>
          <span><strong>ORANGE:</strong> Risk Factor {orangeRiskFactorMin}-{orangeRiskFactorMax} AND Critical = 0</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 12, height: 12, borderRadius: 3, background: '#16a34a', display: 'inline-block' }}></span>
          <span><strong>GREEN:</strong> Risk Factor &lt; {greenRiskFactor} AND Critical = 0</span>
        </div>
      </div>
      
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginBottom: 12 }}>
        <div style={{ padding: 12, background: 'rgba(220, 38, 38, 0.1)', borderRadius: 8, border: '1px solid rgba(220, 38, 38, 0.3)' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#dc2626', marginBottom: 8 }}>RED (Critical)</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
            <label style={{ fontSize: 11, minWidth: 80 }}>Risk Factor &gt;</label>
            <input className="input" type="number" value={redRiskFactor} onChange={(e) => setRedRiskFactor(Number(e.target.value))} style={{ width: 60, padding: '4px 8px', fontSize: 12 }} />
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <label style={{ fontSize: 11, minWidth: 80 }}>Critical &gt;</label>
            <input className="input" type="number" value={redCritical} onChange={(e) => setRedCritical(Number(e.target.value))} style={{ width: 60, padding: '4px 8px', fontSize: 12 }} />
          </div>
        </div>
        <div style={{ padding: 12, background: 'rgba(217, 119, 6, 0.1)', borderRadius: 8, border: '1px solid rgba(217, 119, 6, 0.3)' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#d97706', marginBottom: 8 }}>ORANGE (Warning)</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
            <label style={{ fontSize: 11, minWidth: 80 }}>RF Min</label>
            <input className="input" type="number" value={orangeRiskFactorMin} onChange={(e) => setOrangeRiskFactorMin(Number(e.target.value))} style={{ width: 60, padding: '4px 8px', fontSize: 12 }} />
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
            <label style={{ fontSize: 11, minWidth: 80 }}>RF Max</label>
            <input className="input" type="number" value={orangeRiskFactorMax} onChange={(e) => setOrangeRiskFactorMax(Number(e.target.value))} style={{ width: 60, padding: '4px 8px', fontSize: 12 }} />
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <label style={{ fontSize: 11, minWidth: 80 }}>Critical</label>
            <input className="input" type="number" value={orangeCritical} onChange={(e) => setOrangeCritical(Number(e.target.value))} style={{ width: 60, padding: '4px 8px', fontSize: 12 }} />
          </div>
        </div>
        <div style={{ padding: 12, background: 'rgba(22, 163, 74, 0.1)', borderRadius: 8, border: '1px solid rgba(22, 163, 74, 0.3)' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#16a34a', marginBottom: 8 }}>GREEN (OK)</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
            <label style={{ fontSize: 11, minWidth: 80 }}>RF &lt;</label>
            <input className="input" type="number" value={greenRiskFactor} onChange={(e) => setGreenRiskFactor(Number(e.target.value))} style={{ width: 60, padding: '4px 8px', fontSize: 12 }} />
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <label style={{ fontSize: 11, minWidth: 80 }}>Critical</label>
            <input className="input" type="number" value={greenCritical} onChange={(e) => setGreenCritical(Number(e.target.value))} style={{ width: 60, padding: '4px 8px', fontSize: 12 }} />
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="btn" onClick={saveThresholds}>Save Thresholds</button>
        {thresholdMsg && <span className="muted" style={{ fontSize: 12 }}>{thresholdMsg}</span>}
      </div>
    </div>
  )
}

function TwistlockConfig(): JSX.Element {
  const [url, setUrl] = useState('')
  const [user, setUser] = useState('')
  const [password, setPassword] = useState('')
  const [verify, setVerify] = useState<'true' | 'false'>('true')
  const [refreshHours, setRefreshHours] = useState('24')
  const [msg, setMsg] = useState('')

  const load = async () => {
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/admin/twistlock', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const d = await resp.json()
        setUrl(d.url || '')
        setUser(d.user || '')
        setVerify((d.verify === 'false') ? 'false' : 'true')
        setRefreshHours(String(d.refresh_hours ?? '24'))
      }
    } catch {}
  }

  React.useEffect(() => { 
    void load()
  }, [])

  const save = async () => {
    setMsg('')
    try {
      const token = sessionStorage.getItem('token') || ''
      const body = new URLSearchParams()
      if (url) body.set('url', url)
      if (user) body.set('user', user)
      if (password) body.set('password', password)
      body.set('verify', verify)
      body.set('refresh_hours', refreshHours)
      const resp = await fetch('/api/admin/twistlock', { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' }, body })
      if (!resp.ok) throw new Error('Save failed')
      setMsg('Saved')
      setPassword('')
    } catch (e: any) {
      setMsg(e.message || 'Save failed')
    }
  }

  return (
    <div>
      <div className="inputs" style={{ flexWrap: 'wrap' }}>
        <input className="input" placeholder="URL (e.g. https://twistlock-console...)" value={url} onChange={(e) => setUrl(e.target.value)} style={{ minWidth: 360 }} />
        <input className="input" placeholder="User" value={user} onChange={(e) => setUser(e.target.value)} />
        <input className="input" type="password" placeholder="Password (leave blank to keep)" value={password} onChange={(e) => setPassword(e.target.value)} />
        <select className="select" value={verify} onChange={(e) => setVerify(e.target.value as any)}>
          <option value="true">Verify TLS</option>
          <option value="false">Skip TLS Verify</option>
        </select>
        <input className="input" type="number" min={1} max={720} value={refreshHours}
          onChange={(e) => setRefreshHours(e.target.value)} style={{ width: 220 }}
          title="How old stored Twistlock data may be before a sync re-fetches it, in hours (1–720)."
          placeholder="Refresh interval hours (default 24)" />
        <button className="btn" onClick={save}>Save</button>
        {msg && <span className="muted" style={{ fontSize: 12 }}>{msg}</span>}
      </div>
    </div>
  )
}

function TrivyConfig(): JSX.Element {
  const [trivyUrl, setTrivyUrl] = useState('')
  const [trivySkipTls, setTrivySkipTls] = useState<'true' | 'false'>('true')
  const [trivyMaxDetail, setTrivyMaxDetail] = useState('20')
  const [trivyDockerhubProxy, setTrivyDockerhubProxy] = useState('')
  const [trivyScanTimeout, setTrivyScanTimeout] = useState('300')
  const [trivyCacheTtl, setTrivyCacheTtl] = useState('6')
  const [trivyMsg, setTrivyMsg] = useState('')

  const loadTrivy = async () => {
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/admin/trivy', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const d = await resp.json()
        setTrivyUrl(d.url || '')
        setTrivySkipTls(d.skip_tls_verify === 'true' ? 'true' : 'false')
        setTrivyMaxDetail(String(d.max_detail_vulns ?? '20'))
        setTrivyDockerhubProxy(d.dockerhub_proxy || '')
        setTrivyScanTimeout(String(d.scan_timeout_seconds ?? '300'))
        setTrivyCacheTtl(String(d.cache_ttl_hours ?? '6'))
      }
    } catch {}
  }

  React.useEffect(() => { void loadTrivy() }, [])

  const saveTrivy = async () => {
    setTrivyMsg('')
    try {
      const token = sessionStorage.getItem('token') || ''
      const body = new URLSearchParams()
      body.set('url', trivyUrl)
      body.set('skip_tls_verify', trivySkipTls)
      body.set('max_detail_vulns', trivyMaxDetail)
      body.set('dockerhub_proxy', trivyDockerhubProxy)
      body.set('scan_timeout_seconds', trivyScanTimeout)
      body.set('cache_ttl_hours', trivyCacheTtl)
      const resp = await fetch('/api/admin/trivy', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      })
      if (!resp.ok) throw new Error('Save failed')
      setTrivyMsg('Saved')
    } catch (e: any) {
      setTrivyMsg(e.message || 'Save failed')
    }
  }

  return (
    <div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12, lineHeight: 1.6 }}>
        Trivy Server scans container images for vulnerabilities. Install Trivy Server and enter the URL below.
        Secondary backends can fetch results from the primary via the Trivy column in Backend Endpoints.
      </div>
      <div className="inputs" style={{ flexWrap: 'wrap', marginBottom: 12 }}>
        <input className="input" placeholder="Trivy Server URL (e.g. http://trivy-server:4954)" value={trivyUrl} onChange={(e) => setTrivyUrl(e.target.value)} style={{ minWidth: 360 }} />
        <select className="select" value={trivySkipTls} onChange={(e) => setTrivySkipTls(e.target.value as any)}>
          <option value="true">Skip TLS Verify</option>
          <option value="false">Verify TLS</option>
        </select>
        <input className="input" type="number" min={0} value={trivyMaxDetail}
          onChange={(e) => setTrivyMaxDetail(e.target.value)} style={{ width: 200 }}
          title="Max detailed CVEs to store per image (0 = unlimited). Distribution counts are always complete."
          placeholder="Max detailed CVEs (0=∞)" />
        <input className="input" value={trivyDockerhubProxy}
          onChange={(e) => setTrivyDockerhubProxy(e.target.value)} style={{ minWidth: 320 }}
          title="Optional: pull docker.io images for scanning through this proxy (e.g. internal Harbor) to avoid Docker Hub rate limits. Empty = direct docker.io."
          placeholder="Docker Hub pull proxy (e.g. harbor.example.com/docker-proxy)" />
        <input className="input" type="number" min={30} max={1800} value={trivyScanTimeout}
          onChange={(e) => setTrivyScanTimeout(e.target.value)} style={{ width: 200 }}
          title="Per-image Trivy scan subprocess timeout in seconds (30–1800). A rate-limited docker.io pull hangs to this ceiling."
          placeholder="Scan timeout sec (default 300)" />
        <input className="input" type="number" min={1} max={720} value={trivyCacheTtl}
          onChange={(e) => setTrivyCacheTtl(e.target.value)} style={{ width: 200 }}
          title="How long a stored Trivy scan stays fresh before re-scan, in hours (1–720)."
          placeholder="Cache TTL hours (default 6)" />
        <button className="btn" onClick={saveTrivy}>Save</button>
        {trivyMsg && <span className="muted" style={{ fontSize: 12 }}>{trivyMsg}</span>}
      </div>
    </div>
  )
}


interface LLMProvider {
  id: string; name: string; base_url: string; model: string;
  api_key: string; api_key_header: string; max_tokens: number; temperature: number; active: boolean;
}

function LLMConfig(): JSX.Element {
  const [providers, setProviders] = useState<LLMProvider[]>([])
  const [msg, setMsg] = useState('')
  const [editId, setEditId] = useState<string | null>(null)
  const [form, setForm] = useState<LLMProvider>({ id: '', name: '', base_url: '', model: 'llm', api_key: '', api_key_header: '', max_tokens: 1000, temperature: 0.3, active: false })
  const [testResult, setTestResult] = useState('')
  const [testing, setTesting] = useState(false)

  const load = async () => {
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/admin/llm', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const d = await resp.json()
        setProviders(d.providers || [])
      }
    } catch {}
  }

  React.useEffect(() => { void load() }, [])

  const save = async (list: LLMProvider[]) => {
    setMsg('')
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/admin/llm', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ providers: list })
      })
      if (!resp.ok) throw new Error('Save failed')
      setProviders(list)
      setMsg('Saved')
    } catch (e: any) {
      setMsg(e.message || 'Save failed')
    }
  }

  const toggleActive = (id: string) => {
    const updated = providers.map(p => ({ ...p, active: p.id === id ? !p.active : false }))
    void save(updated)
  }

  const removeProvider = (id: string) => {
    const updated = providers.filter(p => p.id !== id)
    void save(updated)
  }

  const startEdit = (p: LLMProvider) => {
    setEditId(p.id)
    setForm({ ...p, api_key: '' })
    setTestResult('')
  }

  const startAdd = () => {
    const id = `llm-${Date.now()}`
    setEditId('__new__')
    setForm({ id, name: '', base_url: '', model: 'llm', api_key: '', api_key_header: '', max_tokens: 1000, temperature: 0.3, active: false })
    setTestResult('')
  }

  const saveForm = () => {
    if (!form.name || !form.base_url) { setMsg('Name and Base URL are required'); return }
    let updated: LLMProvider[]
    if (editId === '__new__') {
      updated = [...providers, form]
    } else {
      updated = providers.map(p => {
        if (p.id !== editId) return p
        const merged = { ...p, ...form }
        if (!form.api_key) merged.api_key = p.api_key
        if (form.api_key_header !== undefined) merged.api_key_header = form.api_key_header
        return merged
      })
    }
    void save(updated)
    setEditId(null)
  }

  const testConnection = async () => {
    if (!form.base_url) { setTestResult('Base URL is required'); return }
    setTesting(true); setTestResult('')
    try {
      const resp = await fetch(`${form.base_url}/models`, { headers: { Authorization: `Bearer ${form.api_key || 'dummy'}` } })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const d = await resp.json()
      const models = (d.data || []).map((m: any) => m.id).join(', ')
      setTestResult(`Connected. Models: ${models || 'none'}`)
    } catch (e: any) {
      setTestResult(`Failed: ${e.message}`)
    } finally { setTesting(false) }
  }

  const thStyle: React.CSSProperties = { textAlign: 'left', padding: '6px 8px', fontSize: 11, borderBottom: '1px solid var(--border)' }
  const tdStyle: React.CSSProperties = { padding: '6px 8px', fontSize: 12, borderBottom: '1px solid var(--border)' }

  return (
    <div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12, lineHeight: 1.6 }}>
        Add OpenAI-compatible LLM providers to generate AI-powered security advice.
        Only the <strong>active</strong> provider is used. Inactive providers are ignored.
      </div>

      {providers.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 12 }}>
          <thead>
            <tr>
              <th style={thStyle}>Name</th>
              <th style={thStyle}>Model</th>
              <th style={thStyle}>Base URL</th>
              <th style={thStyle}>Status</th>
              <th style={thStyle}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {providers.map(p => (
              <tr key={p.id}>
                <td style={tdStyle}><strong>{p.name}</strong></td>
                <td style={tdStyle}><code style={{ fontSize: 11 }}>{p.model}</code></td>
                <td style={{ ...tdStyle, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.base_url}</td>
                <td style={tdStyle}>
                  <button
                    className="btn"
                    onClick={() => toggleActive(p.id)}
                    style={{
                      fontSize: 10, padding: '2px 8px',
                      background: p.active ? '#16a34a' : '#6b7280',
                      color: '#fff', border: 'none'
                    }}>
                    {p.active ? 'Active' : 'Inactive'}
                  </button>
                </td>
                <td style={tdStyle}>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <button className="btn secondary" style={{ fontSize: 10, padding: '2px 6px' }} onClick={() => startEdit(p)}>Edit</button>
                    <button className="btn secondary" style={{ fontSize: 10, padding: '2px 6px', color: '#dc2626' }} onClick={() => removeProvider(p.id)}>Delete</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editId ? (
        <div style={{ padding: 12, background: 'rgba(99,102,241,0.05)', borderRadius: 8, border: '1px solid rgba(99,102,241,0.2)', marginBottom: 12 }}>
          <h4 style={{ margin: '0 0 10px 0', fontSize: 13 }}>{editId === '__new__' ? 'Add Provider' : 'Edit Provider'}</h4>
          <div className="inputs" style={{ flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
            <input className="input" placeholder="Provider Name (e.g. Groq Cloud)" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} style={{ width: 200 }} />
            <input className="input" placeholder="Base URL" value={form.base_url} onChange={e => setForm({ ...form, base_url: e.target.value })} style={{ minWidth: 300 }} />
          </div>
          <div className="inputs" style={{ flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
            <input className="input" placeholder="Model Name" value={form.model} onChange={e => setForm({ ...form, model: e.target.value })} style={{ width: 180 }} />
            <input className="input" placeholder="Header Name (e.g. apikey, Authorization)" value={form.api_key_header} onChange={e => setForm({ ...form, api_key_header: e.target.value })} style={{ width: 220 }} />
            <input className="input" type="password" placeholder="API Key (blank = keep current)" value={form.api_key} onChange={e => setForm({ ...form, api_key: e.target.value })} style={{ width: 180 }} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <label style={{ fontSize: 11, color: 'var(--muted)' }}>Tokens:</label>
              <input className="input" type="number" value={form.max_tokens} onChange={e => setForm({ ...form, max_tokens: Number(e.target.value) })} style={{ width: 70, padding: '4px 6px', fontSize: 12 }} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <label style={{ fontSize: 11, color: 'var(--muted)' }}>Temp:</label>
              <input className="input" type="number" step="0.1" min="0" max="2" value={form.temperature} onChange={e => setForm({ ...form, temperature: Number(e.target.value) })} style={{ width: 60, padding: '4px 6px', fontSize: 12 }} />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="btn" onClick={saveForm}>Save</button>
            <button className="btn secondary" onClick={testConnection} disabled={testing}>{testing ? 'Testing...' : 'Test'}</button>
            <button className="btn secondary" onClick={() => setEditId(null)}>Cancel</button>
          </div>
          {testResult && (
            <div style={{ marginTop: 8, fontSize: 12, padding: '6px 10px', borderRadius: 6,
              background: testResult.startsWith('Connected') ? 'rgba(22,163,74,0.1)' : 'rgba(220,38,38,0.1)',
              color: testResult.startsWith('Connected') ? '#16a34a' : '#dc2626',
            }}>{testResult}</div>
          )}
        </div>
      ) : (
        <button className="btn" onClick={startAdd}>+ Add Provider</button>
      )}
      {msg && <span className="muted" style={{ fontSize: 12, marginLeft: 8 }}>{msg}</span>}
    </div>
  )
}

function LLMPrompts(): JSX.Element {
  const [securityPrompt, setSecurityPrompt] = useState('')
  const [upgradePrompt, setUpgradePrompt] = useState('')
  const [eolPrompt, setEolPrompt] = useState('')
  const [securityDefault, setSecurityDefault] = useState('')
  const [upgradeDefault, setUpgradeDefault] = useState('')
  const [eolDefault, setEolDefault] = useState('')
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)

  const load = async () => {
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/admin/llm-prompts', { headers: { Authorization: `Bearer ${token}` } })
      if (resp.ok) {
        const d = await resp.json()
        setSecurityPrompt(d.security_prompt || '')
        setUpgradePrompt(d.upgrade_prompt || '')
        setEolPrompt(d.eol_status_prompt || '')
        setSecurityDefault(d.security_default || '')
        setUpgradeDefault(d.upgrade_default || '')
        setEolDefault(d.eol_status_default || '')
      }
    } catch {} finally { setLoading(false) }
  }

  React.useEffect(() => { void load() }, [])

  const save = async () => {
    setMsg('')
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/admin/llm-prompts', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ security_prompt: securityPrompt, upgrade_prompt: upgradePrompt, eol_status_prompt: eolPrompt })
      })
      if (!resp.ok) throw new Error('Save failed')
      setMsg('Saved')
    } catch (e: any) { setMsg(e.message || 'Save failed') }
  }

  if (loading) return <p className="muted">Loading...</p>

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Security Advice Prompt</label>
          <button className="btn secondary" style={{ fontSize: 11, padding: '2px 8px' }}
            onClick={() => setSecurityPrompt(securityDefault)}>Reset to Default</button>
        </div>
        <textarea className="input" rows={6} value={securityPrompt}
          onChange={e => setSecurityPrompt(e.target.value)}
          placeholder={securityDefault}
          style={{ width: '100%', fontFamily: 'monospace', fontSize: 12, resize: 'vertical' }} />
      </div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Upgrade Advice Prompt</label>
          <button className="btn secondary" style={{ fontSize: 11, padding: '2px 8px' }}
            onClick={() => setUpgradePrompt(upgradeDefault)}>Reset to Default</button>
        </div>
        <textarea className="input" rows={6} value={upgradePrompt}
          onChange={e => setUpgradePrompt(e.target.value)}
          placeholder={upgradeDefault}
          style={{ width: '100%', fontFamily: 'monospace', fontSize: 12, resize: 'vertical' }} />
      </div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
          <label style={{ fontSize: 13, fontWeight: 600 }}>EOL Support Prompt</label>
          <button className="btn secondary" style={{ fontSize: 11, padding: '2px 8px' }}
            onClick={() => setEolPrompt(eolDefault)}>Reset to Default</button>
        </div>
        <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>
          Used only when a product/version has no public EOL (endoflife.date) data — asks the LLM whether
          it is still supported. Leave empty to use the default.
        </div>
        <textarea className="input" rows={6} value={eolPrompt}
          onChange={e => setEolPrompt(e.target.value)}
          placeholder={eolDefault}
          style={{ width: '100%', fontFamily: 'monospace', fontSize: 12, resize: 'vertical' }} />
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="btn" onClick={save}>Save Prompts</button>
        {msg && <span className="muted" style={{ fontSize: 12 }}>{msg}</span>}
      </div>
    </div>
  )
}

function LatestVersionConfig(): JSX.Element {
  const [mode, setMode] = useState<'local' | 'primary'>('local')
  const [cacheHours, setCacheHours] = useState(12)
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)

  const load = async () => {
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/admin/latest-version-config', { 
        headers: { Authorization: `Bearer ${token}` } 
      })
      if (resp.ok) {
        const d = await resp.json()
        setMode(d.mode || 'local')
        setCacheHours(d.cache_hours ?? 12)
      }
    } catch (e) {
      console.error('Failed to load latest version config:', e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const save = async () => {
    setMsg('')
    try {
      const token = sessionStorage.getItem('token') || ''
      const body = new URLSearchParams()
      body.set('mode', mode)
      body.set('cache_hours', String(cacheHours))
      const resp = await fetch('/api/admin/latest-version-config', { 
        method: 'POST', 
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/x-www-form-urlencoded' }, 
        body 
      })
      if (!resp.ok) throw new Error('Save failed')
      setMsg('Saved')
    } catch (e: any) {
      setMsg(e.message || 'Save failed')
    }
  }

  if (loading) {
    return <div className="muted" style={{ fontSize: 12 }}>Loading...</div>
  }

  return (
    <div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16, lineHeight: 1.6 }}>
        Configure how secondary (remote) backends fetch the latest version of container images.
        <ul style={{ margin: '8px 0', paddingLeft: 20 }}>
          <li><strong>Local:</strong> Each backend fetches latest version directly from registries (quay.io, ghcr.io, etc.)</li>
          <li><strong>Primary:</strong> Secondary backends request latest version from primary backend (useful when secondary backends have limited network access)</li>
        </ul>
      </div>

      <div className="inputs" style={{ flexWrap: 'wrap', marginBottom: 16, gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <label style={{ fontSize: 12, minWidth: 100 }}>Check Mode:</label>
          <select 
            className="select" 
            value={mode} 
            onChange={(e) => setMode(e.target.value as 'local' | 'primary')}
            style={{ minWidth: 150 }}
          >
            <option value="local">Local (each backend)</option>
            <option value="primary">Primary (via federation)</option>
          </select>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <label style={{ fontSize: 12, minWidth: 100 }}>Cache Hours:</label>
          <input 
            className="input" 
            type="number" 
            min={0} 
            max={24} 
            value={cacheHours} 
            onChange={(e) => setCacheHours(Number(e.target.value))}
            style={{ width: 80 }}
          />
          <span className="muted" style={{ fontSize: 11 }}>(0-24, 0 = no cache)</span>
        </div>
      </div>

      {mode === 'primary' && (
        <div style={{ 
          padding: 12, 
          background: 'rgba(59, 130, 246, 0.1)', 
          borderRadius: 8, 
          border: '1px solid rgba(59, 130, 246, 0.3)',
          marginBottom: 16,
          fontSize: 11,
          color: 'var(--muted)'
        }}>
          <strong style={{ color: '#3b82f6' }}>ℹ️ Primary Mode:</strong> Secondary backends will request latest version info from the primary backend. 
          This is useful when secondary backends have network restrictions (e.g., cannot reach quay.io, ghcr.io, etc.).
          Primary backend must have internet access to container registries.
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="btn" onClick={save}>Save</button>
        {msg && <span className="muted" style={{ fontSize: 12 }}>{msg}</span>}
      </div>
    </div>
  )
}

function ExclusionManagementCard(): JSX.Element {
  const [registries, setRegistries] = useState<string[]>([])
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  
  // Load exclusions on mount
  useEffect(() => {
    loadExclusions()
  }, [])
  
  const loadExclusions = async () => {
    try {
      const token = getAuthToken()
      if (!token) return
      
      const resp = await fetch('/api/exclusions', {
        headers: { Authorization: `Bearer ${token}` }
      })
      
      if (resp.ok) {
        const data = await resp.json()
        setRegistries(data.registries || [])
        setNamespaces(data.namespaces || [])
      }
    } catch (e) {
      console.error('Failed to load exclusions:', e)
    } finally {
      setLoading(false)
    }
  }
  
  const saveRegistries = async () => {
    setSaving(true)
    setMessage('')
    try {
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      
      // Filter out empty lines before saving
      const cleanedRegistries = registries.map(s => s.trim()).filter(Boolean)
      
      const resp = await fetch('/api/exclusions/registries', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(cleanedRegistries)
      })
      
      if (!resp.ok) throw new Error('Failed to save')
      
      // Update state with cleaned list
      setRegistries(cleanedRegistries)
      setMessage('✅ Registry exclusions saved')
      setTimeout(() => setMessage(''), 3000)
    } catch (e: any) {
      setMessage(`❌ Error: ${e.message}`)
    } finally {
      setSaving(false)
    }
  }
  
  const saveNamespaces = async () => {
    setSaving(true)
    setMessage('')
    try {
      const token = getAuthToken()
      if (!token) throw new Error('Not authenticated')
      
      // Filter out empty lines before saving
      const cleanedNamespaces = namespaces.map(s => s.trim()).filter(Boolean)
      
      const resp = await fetch('/api/exclusions/namespaces', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(cleanedNamespaces)
      })
      
      if (!resp.ok) throw new Error('Failed to save')
      
      // Update state with cleaned list
      setNamespaces(cleanedNamespaces)
      setMessage('✅ Namespace exclusions saved')
      setTimeout(() => setMessage(''), 3000)
    } catch (e: any) {
      setMessage(`❌ Error: ${e.message}`)
    } finally {
      setSaving(false)
    }
  }
  
  if (loading) {
    return (
      <div className="card" style={{ marginTop: 16 }}>
        <h2>Exclusion Management</h2>
        <p className="muted">Loading...</p>
      </div>
    )
  }
  
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2>Exclusion Management</h2>
      <p className="muted" style={{ fontSize: 12, marginBottom: 16 }}>
        Configure which image registries and namespaces to skip during SYNC. 
        These resources will not be tracked or displayed. Changes apply to all backends.
      </p>
      
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        {/* Excluded Registries */}
        <div>
          <h3 style={{ margin: 0, fontSize: 14, marginBottom: 8 }}>Excluded Image Registries</h3>
          <p className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
            Skip containers from these registries (supports regex)
          </p>
          <textarea
            className="input"
            style={{ width: '100%', minHeight: 150, fontFamily: 'monospace', fontSize: 11 }}
            placeholder="image-registry.openshift-image-registry.svc&#10;my-internal-registry.local:5000&#10;^.*\\.internal\\.company\\.com"
            value={registries.join('\n')}
            onChange={(e) => setRegistries(e.target.value.split('\n'))}
          />
          <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4, marginBottom: 8, lineHeight: '1.4' }}>
            <strong>How to use:</strong> Press <kbd style={{padding:'2px 4px',background:'rgba(0,0,0,0.3)',borderRadius:2,fontFamily:'monospace'}}>Enter</kbd> for new line. One pattern per line.<br/>
            <strong>Pattern types:</strong><br/>
            • <code style={{background:'rgba(139,92,246,0.1)',padding:'0 4px',borderRadius:2}}>^image-registry\\..*\\.svc</code> → Regex (starts with ^)<br/>
            • <code style={{background:'rgba(139,92,246,0.1)',padding:'0 4px',borderRadius:2}}>nexus.company.com</code> → Substring match<br/>
            • <code style={{background:'rgba(139,92,246,0.1)',padding:'0 4px',borderRadius:2}}>myregistry.local/</code> → Registry prefix
          </div>
          <button 
            className="btn secondary" 
            onClick={saveRegistries} 
            disabled={saving}
            style={{ fontSize: 12, padding: '4px 12px' }}
          >
            {saving ? 'Saving...' : 'Save Registries'}
          </button>
        </div>
        
        {/* Excluded Namespaces */}
        <div>
          <h3 style={{ margin: 0, fontSize: 14, marginBottom: 8 }}>Excluded Namespaces</h3>
          <p className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
            Skip resources from these namespaces (supports regex)
          </p>
          <textarea
            className="input"
            style={{ width: '100%', minHeight: 150, fontFamily: 'monospace', fontSize: 11 }}
            placeholder="openshift-&#10;kube-&#10;default&#10;^ibm-.*&#10;^test-[0-9]+"
            value={namespaces.join('\n')}
            onChange={(e) => setNamespaces(e.target.value.split('\n'))}
          />
          <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4, marginBottom: 8, lineHeight: '1.4' }}>
            <strong>How to use:</strong> Press <kbd style={{padding:'2px 4px',background:'rgba(0,0,0,0.3)',borderRadius:2,fontFamily:'monospace'}}>Enter</kbd> for new line. One pattern per line.<br/>
            <strong>Pattern types:</strong><br/>
            • <code style={{background:'rgba(139,92,246,0.1)',padding:'0 4px',borderRadius:2}}>^openshift-.*</code> → Regex (starts with ^)<br/>
            • <code style={{background:'rgba(139,92,246,0.1)',padding:'0 4px',borderRadius:2}}>openshift-</code> → Prefix (ends with -)<br/>
            • <code style={{background:'rgba(139,92,246,0.1)',padding:'0 4px',borderRadius:2}}>default</code> → Exact match
          </div>
          <button 
            className="btn secondary" 
            onClick={saveNamespaces} 
            disabled={saving}
            style={{ fontSize: 12, padding: '4px 12px' }}
          >
            {saving ? 'Saving...' : 'Save Namespaces'}
          </button>
        </div>
      </div>
      
      {message && (
        <div style={{ marginTop: 12, fontSize: 12, padding: 8, background: 'rgba(139,92,246,0.1)', borderRadius: 4 }}>
          {message}
        </div>
      )}
      
      <div style={{ marginTop: 16, padding: 12, background: 'rgba(251,191,36,0.1)', borderRadius: 4, fontSize: 11 }}>
        <strong style={{ color: '#fbbf24' }}>⚠️ Important:</strong>
        <ul style={{ margin: '8px 0 0 0', paddingLeft: 20 }}>
          <li>Exclusions take effect on the next SYNC operation</li>
          <li>Existing resources won't be auto-removed - use "Database Cleanup" below to remove them</li>
          <li>These settings are shared across all backends using this database</li>
        </ul>
      </div>
    </div>
  )
}


