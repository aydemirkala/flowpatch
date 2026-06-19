import React, { useEffect, useState, useMemo } from 'react'

type ProductInfo = {
  product_name: string
  platforms: string[]
  namespaces: string[]
  resources: Array<{
    id: number
    platform: string
    namespace: string
    resource_name: string
    kind: string
    container_name?: string
    current_image: string
    current_version?: string
    latest_version?: string
    version_diff?: string
    helm_status?: string | null
    helm_release_name?: string | null
  }>
  current_images: string[]
  latest_images: string[]
}

type ImageUpdateJob = {
  id: number
  product_name: string
  source_image?: string  // Filter: only update containers matching this image repo
  target_image: string
  image_source: string
  target_platforms?: string[]
  target_namespaces?: string[]
  scheduled_at?: string
  status: string
  approval_required: boolean
  health_check_enabled: boolean
  health_check_mode: string
  approved_by?: string
  approved_at?: string
  trigger_token?: string
  started_at?: string
  finished_at?: string
  created_by: string
  created_at: string
  notes?: string
  // Live progress tracking
  progress_total?: number
  progress_current?: number
  progress_message?: string
  cancel_requested?: boolean
  cancelled_at?: string
  results_summary?: {
    total: number
    success: number
    failed: number
    skipped: number
    rolled_back: number
  }
}

type PreviewResult = {
  product_name: string
  target_image: string
  total_resources: number
  total_containers: number
  will_change: number
  affected: Array<{
    id: number
    platform: string
    namespace: string
    resource_name: string
    kind: string
    containers: Array<{
      name?: string
      current_image: string
      will_change: boolean
    }>
  }>
}

type JobResult = {
  id: number
  platform: string
  namespace: string
  resource_name: string
  kind: string
  container_name?: string
  old_image?: string
  new_image: string
  status: 'success' | 'failed' | 'skipped' | 'rolled_back'
  error_message?: string
  executed_at: string
  // Health check fields
  health_check_passed?: boolean
  health_check_message?: string
  health_checked_at?: string
  // Rollback fields
  was_rolled_back?: boolean
  rollback_reason?: string
  // Detailed state and logs
  pre_patch_state?: {
    replicas?: number
    ready_replicas?: number
    pods?: Array<{
      name: string
      phase: string
      ready: boolean
      restart_count: number
      container_statuses?: Array<{
        name: string
        ready: boolean
        restart_count: number
        state?: string
        reason?: string
        message?: string
      }>
    }>
  }
  post_patch_state?: {
    replicas?: number
    ready_replicas?: number
    pods?: Array<{
      name: string
      phase: string
      ready: boolean
      restart_count: number
    }>
  }
  execution_log?: Array<{
    timestamp: string
    level: string
    message: string
  }>
  // Phase 1 (Update Product): applied manifest edits + Helm-managed flag.
  field_changes?: Array<{ path: string; old?: any; new?: any }> | null
  helm_managed?: boolean | null
}

type ImageUpdateProps = {
  onResourcesChanged?: () => void  // Callback to refresh main resources table
  availablePlatformNames?: string[]  // List of platform names from parent (resources)
}

// Platform info from backend endpoints
interface PlatformInfo {
  name: string
  platform: string
  enabled: boolean
}

// Dry Run Report types
type DryRunContainer = {
  name: string
  current_image: string
  target_image: string
  will_update: boolean
}

type DryRunDetail = {
  platform: string
  namespace: string
  resource: string
  kind: string
  status: 'ready' | 'warning' | 'error'
  message: string
  current_image: string | null
  containers: DryRunContainer[]
  warnings?: string[]
}

type DryRunReport = {
  job_id: number
  product: string
  target_image: string
  source_image?: string
  summary: {
    total: number
    ready: number
    warning: number
    error: number
  }
  by_platform: Record<string, { ready: number; warning: number; error: number }>
  details: DryRunDetail[]
  // Phase 1: planned manifest edits + Helm-managed targets (shown for field-edit jobs).
  field_edits?: any[] | null
  helm_managed_targets?: any[]
  helm_managed_ack?: boolean
}

export default function ImageUpdate({ onResourcesChanged, availablePlatformNames = [] }: ImageUpdateProps): JSX.Element {
  const [products, setProducts] = useState<ProductInfo[]>([])
  const [selectedProduct, setSelectedProduct] = useState<string>('')
  const [productDetail, setProductDetail] = useState<ProductInfo | null>(null)
  const [loading, setLoading] = useState<boolean>(false)  // Changed: don't load on mount
  const [loadingProductDetail, setLoadingProductDetail] = useState<boolean>(false)
  const [jobs, setJobs] = useState<ImageUpdateJob[]>([])
  const [showCreateModal, setShowCreateModal] = useState<boolean>(false)
  const [showJobDetailModal, setShowJobDetailModal] = useState<ImageUpdateJob | null>(null)
  const [jobResults, setJobResults] = useState<JobResult[]>([])
  const [loadingResults, setLoadingResults] = useState<boolean>(false)
  const [actionLoading, setActionLoading] = useState<string>('')  // 'executing', 'rolling_back', 'cancelling', 'dry_run', etc.
  const [error, setError] = useState<string>('')
  const [success, setSuccess] = useState<string>('')
  
  // Dry Run state
  const [dryRunReport, setDryRunReport] = useState<DryRunReport | null>(null)
  const [showDryRunModal, setShowDryRunModal] = useState<boolean>(false)

  // Execute confirmation state
  const [showExecuteConfirm, setShowExecuteConfirm] = useState<boolean>(false)
  const [executeConfirmJobId, setExecuteConfirmJobId] = useState<number | null>(null)
  const [executeConfirmParams, setExecuteConfirmParams] = useState<{
    stuck_detection: number, crash_tolerance: number, batch_size: number, batch_pause: number
  } | null>(null)
  const [executeConfirmLoading, setExecuteConfirmLoading] = useState<boolean>(false)
  
  // Rollback confirmation state
  const [showRollbackConfirm, setShowRollbackConfirm] = useState<boolean>(false)
  const [rollbackPreview, setRollbackPreview] = useState<{
    job_id: number, product_name: string, total: number,
    items: { platform: string, namespace: string, resource_name: string, kind: string, container_name: string | null, current_image: string, rollback_to: string }[]
  } | null>(null)
  const [rollbackConfirmLoading, setRollbackConfirmLoading] = useState<boolean>(false)
  
  // Platform selection (Step 0) - NEW
  const [availablePlatforms, setAvailablePlatforms] = useState<PlatformInfo[]>([])
  const [loadingPlatforms, setLoadingPlatforms] = useState<boolean>(false)
  const [preSelectedPlatforms, setPreSelectedPlatforms] = useState<string[]>([])
  const [wizardStep, setWizardStep] = useState<number>(0)  // 0: platforms, 1: products loaded

  // Form state
  const [sourceImage, setSourceImage] = useState<string>('') // Source image repo to filter
  const [sourceVersion, setSourceVersion] = useState<string>('') // Source image tag to filter (empty = all versions)
  const [showAllSourceImages, setShowAllSourceImages] = useState<boolean>(false) // Show all images, not just matching product
  const [imageSource, setImageSource] = useState<'latest' | 'custom'>('latest')
  const [selectedLatestImage, setSelectedLatestImage] = useState<string>('') // Which latest image is selected
  const [customImage, setCustomImage] = useState<string>('')
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([])
  const [selectedNamespaces, setSelectedNamespaces] = useState<string[]>([])
  const [namespaceSearch, setNamespaceSearch] = useState<string>('')  // Search filter for namespaces
  const [selectedKinds, setSelectedKinds] = useState<string[]>([])
  // Use unique resource keys instead of IDs for cross-backend compatibility
  // Format: "platform|namespace|kind|resource_name"
  const [selectedResourceKeys, setSelectedResourceKeys] = useState<string[]>([])
  const [scheduleType, setScheduleType] = useState<'now' | 'scheduled'>('now')
  const [scheduledAt, setScheduledAt] = useState<string>('')
  const [approvalRequired, setApprovalRequired] = useState<boolean>(true)
  const [monitorRollout, setMonitorRollout] = useState<boolean>(true)
  const [adminBatchSize, setAdminBatchSize] = useState<number>(10)
  const [adminBatchPause, setAdminBatchPause] = useState<number>(2)
  const [adminStuckDetection, setAdminStuckDetection] = useState<number>(300)
  const [adminCrashTolerance, setAdminCrashTolerance] = useState<number>(120)
  const [notes, setNotes] = useState<string>('')
  // Update Product: mode selector (Patch=image|fields). Helm is a disabled signpost — Helm-managed
  // products are upgraded by an admin via the helm CLI (this tool does not run helm upgrade).
  const [updateMode, setUpdateMode] = useState<'image' | 'fields' | 'helm'>('image')
  const [fieldEdits, setFieldEdits] = useState<any[]>([])
  const [helmManagedAck, setHelmManagedAck] = useState<boolean>(false)
  const [helmManagedTargets, setHelmManagedTargets] = useState<any[]>([])
  // Removed: preview and previewLoading states - now using client-side clientPreview calculation

  // Jobs table filter states
  const [jobFilterProduct, setJobFilterProduct] = useState<string>('')
  const [jobFilterImage, setJobFilterImage] = useState<string>('')
  const [jobFilterStatus, setJobFilterStatus] = useState<string>('')
  const [jobFilterResult, setJobFilterResult] = useState<string>('')
  const [jobFilterPlatform, setJobFilterPlatform] = useState<string>('')
  const [jobFilterDateFrom, setJobFilterDateFrom] = useState<string>('')
  const [jobFilterDateTo, setJobFilterDateTo] = useState<string>('')

  const token = sessionStorage.getItem('token') || ''

  // Load available platforms - try from API first, then from props as fallback
  const loadPlatforms = async () => {
    // If we have platforms from props (parent component), use those
    if (availablePlatformNames && availablePlatformNames.length > 0) {
      const platformsFromProps = availablePlatformNames.map(p => ({
        name: p,
        platform: p,
        enabled: true
      }))
      setAvailablePlatforms(platformsFromProps)
      console.log('Platforms loaded from props:', platformsFromProps)
      return
    }

    // Otherwise try API
    setLoadingPlatforms(true)
    try {
      const resp = await fetch('/api/backend-endpoints', {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        const data = await resp.json()
        console.log('Backend endpoints loaded:', data)
        if (data && data.length > 0) {
          const platforms = data.map((b: any) => ({
            name: b.name,
            platform: b.platform,
            enabled: b.enabled
          }))
          setAvailablePlatforms(platforms)
          console.log('Available platforms set:', platforms)
        } else {
          console.warn('Backend endpoints returned empty array')
        }
      } else {
        console.error('Failed to load platforms, status:', resp.status)
      }
    } catch (e) {
      console.error('Failed to load platforms:', e)
    } finally {
      setLoadingPlatforms(false)
    }
  }

  // Load products filtered by selected platforms
  const loadProducts = async (platforms?: string[]) => {
    setLoading(true)
    try {
      let url = '/api/image-updates/products'
      if (platforms && platforms.length > 0) {
        url += `?platforms=${encodeURIComponent(platforms.join(','))}`
      }
      const resp = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        const data = await resp.json()
        setProducts(data)
        setWizardStep(1)  // Move to product selection step
      }
    } catch (e) {
      console.error('Failed to load products:', e)
    }
    setLoading(false)
  }

  const loadJobs = async () => {
    try {
      const resp = await fetch('/api/image-updates/jobs?limit=50', {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        setJobs(await resp.json())
      }
    } catch (e) {
      console.error('Failed to load jobs:', e)
    }
  }

  const loadJobResults = async (jobId: number) => {
    setLoadingResults(true)
    try {
      const resp = await fetch(`/api/image-updates/jobs/${jobId}/results`, {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        setJobResults(await resp.json())
      } else {
        setJobResults([])
      }
    } catch (e) {
      console.error('Failed to load job results:', e)
      setJobResults([])
    }
    setLoadingResults(false)
  }

  const downloadResults = async (jobId: number, format: 'csv' | 'json' | 'pdf') => {
    try {
      if (format === 'pdf') {
        // Fetch PDF with authentication and open in new window
        const resp = await fetch(`/api/image-updates/jobs/${jobId}/results/pdf`, {
          headers: { Authorization: `Bearer ${token}` }
        })
        
        if (!resp.ok) {
          setError(`Failed to download PDF: ${resp.statusText}`)
          return
        }
        
        const html = await resp.text()
        const newWindow = window.open('', '_blank')
        if (newWindow) {
          newWindow.document.write(html)
          newWindow.document.close()
        }
        return
      }
      
      const resp = await fetch(`/api/image-updates/jobs/${jobId}/results/export?format=${format}`, {
        headers: { Authorization: `Bearer ${token}` }
      })
      
      if (!resp.ok) {
        setError(`Failed to download results: ${resp.statusText}`)
        return
      }
      
      const blob = await resp.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `job_${jobId}_results_${new Date().toISOString().slice(0,10)}.${format}`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
    } catch (e) {
      console.error('Failed to download results:', e)
      setError('Failed to download results')
    }
  }

  const openJobDetail = async (job: ImageUpdateJob) => {
    setShowJobDetailModal(job)
    setJobResults([])
    // Fetch fresh data from API to avoid stale state
    try {
      const resp = await fetch(`/api/image-updates/jobs/${job.id}`, {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        const fresh: ImageUpdateJob = await resp.json()
        setShowJobDetailModal(fresh)
        if (fresh.results_summary && fresh.results_summary.total > 0) {
          loadJobResults(fresh.id)
        }
      }
    } catch { /* use cached job data */ }
  }

  const loadProductDetail = async (productName: string, platformsToFilter?: string[]) => {
    setLoadingProductDetail(true)
    try {
      // Build URL with platforms filter for faster loading
      let url = `/api/image-updates/products/${encodeURIComponent(productName)}`
      if (platformsToFilter && platformsToFilter.length > 0) {
        url += `?platforms=${encodeURIComponent(platformsToFilter.join(','))}`
      }
      const resp = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        const data = await resp.json()
        setProductDetail(data)
        // Only select platforms that were pre-selected in Step 1 AND exist for this product
        // Use passed platforms or fall back to preSelectedPlatforms state
        const platforms = platformsToFilter || preSelectedPlatforms
        const filteredPlatforms = platforms.length > 0
          ? data.platforms.filter((p: string) => platforms.includes(p))
          : data.platforms
        setSelectedPlatforms(filteredPlatforms.length > 0 ? filteredPlatforms : data.platforms)
        setSelectedNamespaces([]) // Reset namespace selection
        setNamespaceSearch('') // Reset namespace search
        setSelectedKinds([]) // Reset kind selection
        setSelectedResourceIds([]) // Reset resource selection
        setSourceImage('') // Reset source image selection
        setSourceVersion('') // Reset source version selection
        setShowAllSourceImages(false) // Reset show all images
        setSelectedLatestImage('') // Reset target image selection
      }
    } catch (e) {
      console.error('Failed to load product detail:', e)
    } finally {
      setLoadingProductDetail(false)
    }
  }

  useEffect(() => {
    loadPlatforms()
    loadJobs()
    if (token) {
      fetch('/api/admin/proxy-settings', { headers: { Authorization: `Bearer ${token}` } })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data?.patch_batch_size) setAdminBatchSize(data.patch_batch_size)
          if (data?.patch_batch_pause_seconds != null) setAdminBatchPause(data.patch_batch_pause_seconds)
          if (data?.stuck_detection_seconds != null) setAdminStuckDetection(data.stuck_detection_seconds)
          if (data?.crash_tolerance_seconds != null) setAdminCrashTolerance(data.crash_tolerance_seconds)
        })
        .catch(() => {})
    }
    const handleOpenJobDetail = async (e: Event) => {
      const jobId = (e as CustomEvent).detail?.jobId
      if (!jobId || !token) return
      try {
        const resp = await fetch(`/api/image-updates/jobs/${jobId}`, {
          headers: { Authorization: `Bearer ${token}` }
        })
        if (resp.ok) {
          const job = await resp.json()
          openJobDetail(job)
        }
      } catch { /* ignore */ }
    }
    window.addEventListener('open-job-detail', handleOpenJobDetail)
    return () => window.removeEventListener('open-job-detail', handleOpenJobDetail)
  }, [availablePlatformNames])

  useEffect(() => {
    if (selectedProduct) {
      loadProductDetail(selectedProduct, preSelectedPlatforms)
    } else {
      setProductDetail(null)
    }
  }, [selectedProduct, preSelectedPlatforms])

  // Auto-poll progress for active jobs (executing / rolling_back)
  useEffect(() => {
    const hasActiveJob = jobs.some(j => ['executing', 'rolling_back'].includes(j.status))
    if (!hasActiveJob) return

    const interval = setInterval(async () => {
      try {
        const resp = await fetch('/api/image-updates/jobs?limit=50', {
          headers: { Authorization: `Bearer ${token}` }
        })
        if (!resp.ok) return
        const freshJobs: ImageUpdateJob[] = await resp.json()
        setJobs(freshJobs)

        // Update modal if it's showing an active job
        let completedJobId: number | null = null
        setShowJobDetailModal(prev => {
          if (!prev) return null
          const updated = freshJobs.find(j => j.id === prev.id)
          if (!updated) return prev
          const wasActive = ['executing', 'rolling_back'].includes(prev.status)
          const nowFinished = !['executing', 'rolling_back'].includes(updated.status)
          if (wasActive && nowFinished) {
            onResourcesChanged?.()
            if (updated.results_summary && updated.results_summary.total > 0) {
              completedJobId = updated.id
            }
          }
          return updated
        })
        if (completedJobId) {
          loadJobResults(completedJobId)
        }
      } catch { /* ignore */ }
    }, 2000)

    return () => clearInterval(interval)
  }, [jobs.some(j => ['executing', 'rolling_back'].includes(j.status))])

  // Auto-load results once when a job reaches a terminal state
  useEffect(() => {
    if (
      showJobDetailModal &&
      ['completed', 'failed', 'rolled_back', 'cancelled'].includes(showJobDetailModal.status) &&
      jobResults.length === 0 &&
      !loadingResults
    ) {
      loadJobResults(showJobDetailModal.id)
    }
  }, [showJobDetailModal?.status, showJobDetailModal?.id])

  // Extract and normalize repository from image (without tag)
  // Normalizes docker.io prefix: "fluent/fluent-bit" becomes "docker.io/fluent/fluent-bit"
  const getImageRepo = (image: string): string => {
    if (!image) return ''
    const withoutTag = image.split(':')[0]
    
    // Check if it already has a registry prefix
    const parts = withoutTag.split('/')
    if (parts.length === 1) {
      // Single part like "nginx" -> docker.io/library/nginx
      return `docker.io/library/${withoutTag}`
    } else if (parts.length === 2) {
      // Two parts - check if first part is a registry (contains . or :) or user/repo
      const firstPart = parts[0]
      if (firstPart.includes('.') || firstPart.includes(':')) {
        // It's a registry like "gcr.io/project" or "localhost:5000/image"
        return withoutTag
      } else {
        // It's user/repo like "fluent/fluent-bit" -> docker.io/fluent/fluent-bit
        return `docker.io/${withoutTag}`
      }
    }
    // Three or more parts - already has registry
    return withoutTag
  }

  // Get available namespaces based on selected platforms (targets come BEFORE source image)
  // Returns namespaces with platform info for better UX when multiple platforms selected
  const availableNamespacesWithPlatform = useMemo(() => {
    if (!productDetail) return []
    
    // Map: namespace -> Set of platforms it exists in
    const nsToPlats = new Map<string, Set<string>>()
    productDetail.resources.forEach(r => {
      if (selectedPlatforms.length === 0 || selectedPlatforms.includes(r.platform)) {
        if (!nsToPlats.has(r.namespace)) {
          nsToPlats.set(r.namespace, new Set())
        }
        nsToPlats.get(r.namespace)!.add(r.platform)
      }
    })
    
    // Convert to array with platform info
    return Array.from(nsToPlats.entries())
      .map(([ns, plats]) => ({
        namespace: ns,
        platforms: Array.from(plats).sort(),
        displayName: selectedPlatforms.length > 1 
          ? `${ns} (${Array.from(plats).join(', ')})` 
          : ns
      }))
      .sort((a, b) => a.namespace.localeCompare(b.namespace))
  }, [productDetail, selectedPlatforms])
  
  // Simple namespace list for backward compatibility
  const availableNamespaces = useMemo(() => {
    return availableNamespacesWithPlatform.map(n => n.namespace)
  }, [availableNamespacesWithPlatform])

  // Get available kinds based on selected platforms and namespaces
  const availableKinds = useMemo(() => {
    if (!productDetail) return []
    const kindSet = new Set<string>()
    productDetail.resources.forEach(r => {
      if (selectedPlatforms.length > 0 && !selectedPlatforms.includes(r.platform)) return
      if (selectedNamespaces.length > 0 && !selectedNamespaces.includes(r.namespace)) return
      if (r.kind) kindSet.add(r.kind)
    })
    return Array.from(kindSet).sort()
  }, [productDetail, selectedPlatforms, selectedNamespaces])

  // Helper: check if an image matches the product name (defined early so it can be used in useMemo)
  const checkImageMatchesProduct = (image: string, product: string): boolean => {
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

  // Distinct product-matched container names across the selected targets — the container
  // dropdown for container-scoped manifest edits (env/resources/command/args). Blank = the
  // matched container (the backend auto-targets it); an explicit value overrides.
  const matchedContainers = useMemo(() => {
    if (!productDetail || !selectedProduct) return [] as string[]
    const names = new Set<string>()
    const haveKeys = selectedResourceKeys.length > 0
    productDetail.resources.forEach(r => {
      if (haveKeys) {
        // Specific resources chosen → only THEIR matched containers (not every deployment
        // in the namespace), so the dropdown reflects the actual targets.
        if (!selectedResourceKeys.includes(`${r.platform}|${r.namespace}|${r.kind}|${r.resource_name}`)) return
      } else {
        if (selectedPlatforms.length > 0 && !selectedPlatforms.includes(r.platform)) return
        if (selectedNamespaces.length > 0 && !selectedNamespaces.includes(r.namespace)) return
        if (selectedKinds.length > 0 && !selectedKinds.includes(r.kind)) return
      }
      const cn = (r.container_name || '').trim()
      if (cn && checkImageMatchesProduct(r.current_image, selectedProduct)) names.add(cn)
    })
    return Array.from(names).sort()
  }, [productDetail, selectedProduct, selectedPlatforms, selectedNamespaces, selectedKinds, selectedResourceKeys])

  // Selected target resources that are Helm-managed (helm_status ok/drift) — drives the
  // proactive create-time Helm-managed acknowledgment. Works for federated/remote resources
  // too, since each backend reports its own helm_status into the aggregated product detail.
  const helmManagedSelected = useMemo(() => {
    if (!productDetail || !selectedProduct) return [] as any[]
    const haveKeys = selectedResourceKeys.length > 0
    const seen = new Set<string>()
    const out: any[] = []
    productDetail.resources.forEach(r => {
      if (haveKeys) {
        if (!selectedResourceKeys.includes(`${r.platform}|${r.namespace}|${r.kind}|${r.resource_name}`)) return
      } else {
        if (selectedPlatforms.length > 0 && !selectedPlatforms.includes(r.platform)) return
        if (selectedNamespaces.length > 0 && !selectedNamespaces.includes(r.namespace)) return
        if (selectedKinds.length > 0 && !selectedKinds.includes(r.kind)) return
      }
      const hs = (r.helm_status || '')
      if (hs !== 'ok' && hs !== 'drift') return
      const key = `${r.platform}|${r.namespace}|${r.kind}|${r.resource_name}`
      if (seen.has(key)) return
      seen.add(key)
      out.push({ platform: r.platform, namespace: r.namespace, kind: r.kind, resource_name: r.resource_name, release: r.helm_release_name })
    })
    return out
  }, [productDetail, selectedProduct, selectedPlatforms, selectedNamespaces, selectedKinds, selectedResourceKeys])

  // Matched container name(s) derived from the dry-run report — so a blank-container edit
  // shows the REAL container it targets instead of the generic "matched container".
  const dryRunMatchedContainers = useMemo(() => {
    if (!dryRunReport) return [] as string[]
    const names = new Set<string>()
    dryRunReport.details.forEach(d => {
      (d.containers || []).forEach((c: any) => {
        if (c?.name && checkImageMatchesProduct(c.current_image || '', dryRunReport.product)) names.add(c.name)
      })
    })
    return Array.from(names)
  }, [dryRunReport])

  // Get unique source image repositories - FILTERED by selected targets AND optionally product name
  const uniqueSourceImages = useMemo(() => {
    if (!productDetail || !selectedProduct) return []
    const repoMap = new Map<string, { repo: string, count: number, versions: Set<string>, platforms: Set<string>, namespaces: Set<string>, matchesProduct: boolean }>()
    
    productDetail.resources.forEach(r => {
      // Filter by selected platforms
      if (selectedPlatforms.length > 0 && !selectedPlatforms.includes(r.platform)) return
      // Filter by selected namespaces
      if (selectedNamespaces.length > 0 && !selectedNamespaces.includes(r.namespace)) return
      // Filter by selected kinds
      if (selectedKinds.length > 0 && !selectedKinds.includes(r.kind)) return
      
      if (r.current_image) {
        const matchesProduct = checkImageMatchesProduct(r.current_image, selectedProduct)
        
        // Skip if not showing all images and doesn't match product
        if (!showAllSourceImages && !matchesProduct) return
        
        const repo = getImageRepo(r.current_image)
        const version = r.current_image.split(':')[1] || 'latest'
        if (!repoMap.has(repo)) {
          repoMap.set(repo, { repo, count: 0, versions: new Set(), platforms: new Set(), namespaces: new Set(), matchesProduct })
        }
        const entry = repoMap.get(repo)!
        entry.count++
        entry.versions.add(version)
        entry.platforms.add(r.platform)
        entry.namespaces.add(r.namespace)
        if (matchesProduct) entry.matchesProduct = true
      }
    })
    
    // Sort: matching products first, then by count
    return Array.from(repoMap.values()).sort((a, b) => {
      if (a.matchesProduct && !b.matchesProduct) return -1
      if (!a.matchesProduct && b.matchesProduct) return 1
      return b.count - a.count
    })
  }, [productDetail, selectedProduct, selectedPlatforms, selectedNamespaces, selectedKinds, showAllSourceImages])

  const availableSourceVersions = useMemo(() => {
    if (!sourceImage || !productDetail) return []
    const versionData = new Map<string, { count: number, namespaces: Set<string> }>()
    productDetail.resources.forEach(r => {
      if (selectedPlatforms.length > 0 && !selectedPlatforms.includes(r.platform)) return
      if (selectedKinds.length > 0 && !selectedKinds.includes(r.kind)) return
      if (!r.current_image) return
      if (getImageRepo(r.current_image) !== sourceImage) return
      const tag = r.current_image.split(':')[1] || 'latest'
      if (!versionData.has(tag)) versionData.set(tag, { count: 0, namespaces: new Set() })
      const entry = versionData.get(tag)!
      entry.count++
      entry.namespaces.add(r.namespace)
    })
    return Array.from(versionData.entries())
      .map(([tag, d]) => ({ tag, count: d.count, namespaces: Array.from(d.namespaces).sort() }))
      .sort((a, b) => b.count - a.count)
  }, [productDetail, sourceImage, selectedPlatforms, selectedKinds])

  // Auto-select version when only one exists for the selected source image
  useEffect(() => {
    if (sourceImage && availableSourceVersions.length === 1 && sourceVersion === '') {
      setSourceVersion(availableSourceVersions[0].tag)
    }
  }, [sourceImage, availableSourceVersions])

  // Debug: count of resources after each filter step
  const filterDebugInfo = useMemo(() => {
    if (!productDetail) return null
    const total = productDetail.resources.length
    const afterPlatform = productDetail.resources.filter(r => 
      selectedPlatforms.length === 0 || selectedPlatforms.includes(r.platform)
    ).length
    const afterNs = productDetail.resources.filter(r => {
      if (selectedPlatforms.length > 0 && !selectedPlatforms.includes(r.platform)) return false
      if (selectedNamespaces.length > 0 && !selectedNamespaces.includes(r.namespace)) return false
      return true
    }).length
    
    // Get resources after kind filter with their images for debugging
    const resourcesAfterKind = productDetail.resources.filter(r => {
      if (selectedPlatforms.length > 0 && !selectedPlatforms.includes(r.platform)) return false
      if (selectedNamespaces.length > 0 && !selectedNamespaces.includes(r.namespace)) return false
      if (selectedKinds.length > 0 && !selectedKinds.includes(r.kind)) return false
      return true
    })
    
    const afterKind = resourcesAfterKind.length
    const afterProduct = resourcesAfterKind.filter(r => {
      if (!r.current_image) return false
      return checkImageMatchesProduct(r.current_image, selectedProduct)
    }).length
    
    // Collect sample images for debugging
    const sampleImages = resourcesAfterKind.slice(0, 5).map(r => ({
      resource: r.resource_name,
      container: r.container_name || 'N/A',
      image: r.current_image || '(empty)',
      matches: r.current_image ? checkImageMatchesProduct(r.current_image, selectedProduct) : false
    }))
    
    return { total, afterPlatform, afterNs, afterKind, afterProduct, sampleImages }
  }, [productDetail, selectedPlatforms, selectedNamespaces, selectedKinds, selectedProduct, checkImageMatchesProduct])

  // Get available resources based on selected platforms, namespaces, and kinds
  // Deduplicate by unique key: platform + namespace + kind + resource_name
  const availableResources = useMemo(() => {
    if (!productDetail) return []
    
    const filtered = productDetail.resources.filter(r => {
      if (selectedPlatforms.length > 0 && !selectedPlatforms.includes(r.platform)) return false
      if (selectedNamespaces.length > 0 && !selectedNamespaces.includes(r.namespace)) return false
      if (selectedKinds.length > 0 && !selectedKinds.includes(r.kind)) return false
      if (sourceImage && r.current_image && getImageRepo(r.current_image) !== sourceImage) return false
      if (sourceVersion && r.current_image) {
        const tag = r.current_image.split(':')[1] || 'latest'
        if (tag !== sourceVersion) return false
      }
      return true
    })
    
    const seen = new Set<string>()
    const unique: typeof filtered = []
    for (const r of filtered) {
      const key = `${r.platform}|${r.namespace}|${r.kind}|${r.resource_name}`
      if (!seen.has(key)) {
        seen.add(key)
        unique.push(r)
      }
    }
    return unique
  }, [productDetail, selectedPlatforms, selectedNamespaces, selectedKinds, sourceImage, sourceVersion])

  // Filter latest images to only show those matching the selected source image repository
  const filteredLatestImages = useMemo(() => {
    if (!productDetail || !selectedProduct) return []
    // If source image is selected, only show latest versions for that repository
    if (sourceImage) {
      return productDetail.latest_images.filter(img => getImageRepo(img) === sourceImage)
    }
    // Otherwise show all images matching the product
    return productDetail.latest_images.filter(img => checkImageMatchesProduct(img, selectedProduct))
  }, [productDetail, selectedProduct, sourceImage])

  // Get target image based on selection
  const targetImage = useMemo(() => {
    if (imageSource === 'custom') return customImage.trim()
    // Use selected latest image, or first available if none selected
    if (selectedLatestImage && filteredLatestImages.includes(selectedLatestImage)) {
      return selectedLatestImage
    }
    if (filteredLatestImages.length > 0) {
      return filteredLatestImages[0]
    }
    return ''
  }, [imageSource, customImage, selectedLatestImage, filteredLatestImages])
  
  // Auto-select first latest image when filtered images change
  useEffect(() => {
    if (filteredLatestImages.length > 0 && !filteredLatestImages.includes(selectedLatestImage)) {
      setSelectedLatestImage(filteredLatestImages[0])
    }
  }, [filteredLatestImages])

  // Client-side preview calculation (uses already-loaded productDetail.resources)
  // This works for federated data since productDetail already includes all backends
  // Includes per-platform breakdown for clarity
  const clientPreview = useMemo(() => {
    if (!productDetail || !selectedProduct || !targetImage) {
      return { 
        resources: 0, 
        containers: 0, 
        willChange: 0,
        byPlatform: {} as Record<string, { resources: number, containers: number, willChange: number }>
      }
    }
    
    const targetRepo = getImageRepo(targetImage)
    const resourceSet = new Set<string>()
    let containerCount = 0
    let willChangeCount = 0
    
    // Per-platform tracking
    const byPlatform: Record<string, { 
      resourceSet: Set<string>, 
      containers: number, 
      willChange: number 
    }> = {}
    
    productDetail.resources.forEach(r => {
      // Apply platform filter
      if (selectedPlatforms.length > 0 && !selectedPlatforms.includes(r.platform)) return
      // Apply namespace filter
      if (selectedNamespaces.length > 0 && !selectedNamespaces.includes(r.namespace)) return
      // Apply kind filter
      if (selectedKinds.length > 0 && !selectedKinds.includes(r.kind)) return
      // Apply resource key filter (platform|namespace|kind|resource_name)
      if (selectedResourceKeys.length > 0) {
        const resourceKey = `${r.platform}|${r.namespace}|${r.kind}|${r.resource_name}`
        if (!selectedResourceKeys.includes(resourceKey)) return
      }
      
      if (!r.current_image) return
      
      const containerRepo = getImageRepo(r.current_image)
      
      if (sourceImage) {
        if (containerRepo !== sourceImage) return
        if (sourceVersion) {
          const tag = r.current_image.split(':')[1] || 'latest'
          if (tag !== sourceVersion) return
        }
      } else {
        if (containerRepo !== targetRepo) return
      }
      
      // This container matches - count it
      const resourceKey = `${r.platform}|${r.namespace}|${r.kind}|${r.resource_name}`
      resourceSet.add(resourceKey)
      containerCount++
      
      const willChange = r.current_image !== targetImage
      if (willChange) {
        willChangeCount++
      }
      
      // Track per-platform
      if (!byPlatform[r.platform]) {
        byPlatform[r.platform] = { resourceSet: new Set(), containers: 0, willChange: 0 }
      }
      byPlatform[r.platform].resourceSet.add(resourceKey)
      byPlatform[r.platform].containers++
      if (willChange) {
        byPlatform[r.platform].willChange++
      }
    })
    
    // Convert resourceSet to count for each platform
    const byPlatformResult: Record<string, { resources: number, containers: number, willChange: number }> = {}
    for (const [platform, data] of Object.entries(byPlatform)) {
      byPlatformResult[platform] = {
        resources: data.resourceSet.size,
        containers: data.containers,
        willChange: data.willChange,
      }
    }
    
    return {
      resources: resourceSet.size,
      containers: containerCount,
      willChange: willChangeCount,
      byPlatform: byPlatformResult,
    }
  }, [productDetail, selectedProduct, targetImage, sourceImage, sourceVersion, selectedPlatforms, selectedNamespaces, selectedKinds, selectedResourceKeys])

  // Filtered jobs based on filter criteria
  const filteredJobs = useMemo(() => {
    return jobs.filter(job => {
      // Product filter
      if (jobFilterProduct && !job.product_name.toLowerCase().includes(jobFilterProduct.toLowerCase())) {
        return false
      }
      // Image filter
      if (jobFilterImage && !job.target_image.toLowerCase().includes(jobFilterImage.toLowerCase())) {
        return false
      }
      // Status filter
      if (jobFilterStatus && job.status !== jobFilterStatus) {
        return false
      }
      // Platform filter
      if (jobFilterPlatform && job.target_platforms) {
        const hasPlatform = job.target_platforms.some(p => 
          p.toLowerCase().includes(jobFilterPlatform.toLowerCase())
        )
        if (!hasPlatform) return false
      }
      // Result filter (based on results_summary)
      if (jobFilterResult && job.results_summary) {
        const r = job.results_summary
        if (jobFilterResult === 'success' && r.success === 0) return false
        if (jobFilterResult === 'failed' && r.failed === 0) return false
        if (jobFilterResult === 'rolled_back' && r.rolled_back === 0) return false
        if (jobFilterResult === 'skipped' && r.skipped === 0) return false
      }
      // Date range filter
      if (jobFilterDateFrom) {
        const jobDate = new Date(job.created_at)
        const fromDate = new Date(jobFilterDateFrom)
        fromDate.setHours(0, 0, 0, 0)
        if (jobDate < fromDate) return false
      }
      if (jobFilterDateTo) {
        const jobDate = new Date(job.created_at)
        const toDate = new Date(jobFilterDateTo)
        toDate.setHours(23, 59, 59, 999)
        if (jobDate > toDate) return false
      }
      return true
    })
  }, [jobs, jobFilterProduct, jobFilterImage, jobFilterStatus, jobFilterPlatform, jobFilterResult, jobFilterDateFrom, jobFilterDateTo])

  // Get unique values for filter dropdowns
  const jobFilterOptions = useMemo(() => {
    const statuses = new Set<string>()
    const platforms = new Set<string>()
    const products = new Set<string>()
    
    jobs.forEach(job => {
      if (job.status) statuses.add(job.status)
      if (job.product_name) products.add(job.product_name)
      if (job.target_platforms) {
        job.target_platforms.forEach(p => platforms.add(p))
      }
    })
    
    return {
      statuses: Array.from(statuses).sort(),
      platforms: Array.from(platforms).sort(),
      products: Array.from(products).sort()
    }
  }, [jobs])

  // Download jobs as CSV
  const downloadJobsCSV = () => {
    const dataToExport = filteredJobs
    if (dataToExport.length === 0) return
    
    const headers = ['ID', 'Product', 'Target Image', 'Status', 'Created', 'Finished', 'Success', 'Failed', 'Rolled Back', 'Skipped']
    const rows = dataToExport.map(job => [
      job.id,
      job.product_name,
      job.target_image,
      job.status,
      new Date(job.created_at).toLocaleString(),
      job.finished_at ? new Date(job.finished_at).toLocaleString() : '-',
      job.results_summary?.success ?? '-',
      job.results_summary?.failed ?? '-',
      job.results_summary?.rolled_back ?? '-',
      job.results_summary?.skipped ?? '-'
    ])
    
    const csv = [headers, ...rows].map(row => 
      row.map(cell => {
        const str = String(cell)
        return str.includes(',') || str.includes('"') || str.includes('\n') 
          ? `"${str.replace(/"/g, '""')}"` 
          : str
      }).join(',')
    ).join('\n')
    
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `update_jobs_${new Date().toISOString().split('T')[0]}.csv`
    link.click()
  }

  // Download jobs as PDF (printable HTML)
  const downloadJobsPDF = () => {
    const dataToExport = filteredJobs
    if (dataToExport.length === 0) return
    
    const printWindow = window.open('', '_blank')
    if (!printWindow) {
      alert('Please allow popups to download PDF')
      return
    }
    
    const tableRows = dataToExport.map(job => `
      <tr>
        <td>#${job.id}</td>
        <td>${job.product_name}</td>
        <td style="max-width:200px;word-break:break-all;font-size:10px">${job.target_image}</td>
        <td style="color:${job.status === 'completed' ? '#22c55e' : job.status === 'failed' ? '#ef4444' : job.status === 'rolled_back' ? '#f59e0b' : '#666'}">${job.status.toUpperCase()}</td>
        <td>${new Date(job.created_at).toLocaleString()}</td>
        <td>${job.finished_at ? new Date(job.finished_at).toLocaleString() : '-'}</td>
        <td style="color:#22c55e">${job.results_summary?.success ?? '-'}</td>
        <td style="color:#ef4444">${job.results_summary?.failed ?? '-'}</td>
        <td style="color:#f59e0b">${job.results_summary?.rolled_back ?? '-'}</td>
        <td style="color:#6b7280">${job.results_summary?.skipped ?? '-'}</td>
      </tr>
    `).join('')
    
    // Build filter summary
    const activeFilters: string[] = []
    if (jobFilterProduct) activeFilters.push(`Product: ${jobFilterProduct}`)
    if (jobFilterImage) activeFilters.push(`Image: ${jobFilterImage}`)
    if (jobFilterStatus) activeFilters.push(`Status: ${jobFilterStatus}`)
    if (jobFilterPlatform) activeFilters.push(`Platform: ${jobFilterPlatform}`)
    if (jobFilterResult) activeFilters.push(`Result: ${jobFilterResult}`)
    if (jobFilterDateFrom) activeFilters.push(`From: ${jobFilterDateFrom}`)
    if (jobFilterDateTo) activeFilters.push(`To: ${jobFilterDateTo}`)
    const filterSummary = activeFilters.length > 0 
      ? `<p style="font-size:11px;color:#666">Filters: ${activeFilters.join(' | ')}</p>` 
      : ''
    
    const html = `
      <!DOCTYPE html>
      <html>
      <head>
        <title>Update Jobs Report - ${new Date().toLocaleDateString()}</title>
        <style>
          body { font-family: Arial, sans-serif; margin: 20px; font-size: 12px; }
          h1 { color: #1e3a5f; font-size: 18px; margin-bottom: 5px; }
          .subtitle { color: #666; font-size: 12px; margin-bottom: 20px; }
          table { width: 100%; border-collapse: collapse; font-size: 11px; }
          th { background: #1e3a5f; color: white; padding: 8px 6px; text-align: left; font-weight: 600; }
          td { padding: 6px; border-bottom: 1px solid #ddd; vertical-align: top; }
          tr:nth-child(even) { background: #f9f9f9; }
          .footer { margin-top: 20px; font-size: 10px; color: #666; text-align: center; }
          @media print { body { margin: 10px; } }
        </style>
      </head>
      <body>
        <h1>Update Jobs Report</h1>
        <p class="subtitle">Generated: ${new Date().toLocaleString()} | Total: ${dataToExport.length} jobs</p>
        ${filterSummary}
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Product</th>
              <th>Target Image</th>
              <th>Status</th>
              <th>Created</th>
              <th>Finished</th>
              <th>Success</th>
              <th>Failed</th>
              <th>Rolled Back</th>
              <th>Skipped</th>
            </tr>
          </thead>
          <tbody>${tableRows}</tbody>
        </table>
        <div class="footer">Patch Management System - Update Jobs Report</div>
      </body>
      </html>
    `
    
    printWindow.document.write(html)
    printWindow.document.close()
    setTimeout(() => printWindow.print(), 250)
  }

  // ── Manifest field-edit helpers (Update Product) ──
  // Default the container to the single matched container (when there is exactly one) so the
  // edit carries an explicit container — required for remote multi-container resources where
  // the owning backend can't infer "the matched one" from a blank value.
  const addFieldEdit = () => setFieldEdits(prev => [...prev, {
    type: 'env', op: 'set', container: matchedContainers.length === 1 ? matchedContainers[0] : '',
    name: '', value: '', kind: 'limit', key: '', target: 'pod',
  }])
  const updateFieldEdit = (i: number, patch: any) => setFieldEdits(prev => prev.map((e, idx) => idx === i ? { ...e, ...patch } : e))
  const removeFieldEdit = (i: number) => setFieldEdits(prev => prev.filter((_, idx) => idx !== i))
  // Convert the editor rows into the API's whitelisted edit objects.
  const buildFieldEditsPayload = () => fieldEdits.map((e: any) => {
    const t = e.type
    const c = (e.container || '').trim() || null
    if (t === 'env') return e.op === 'remove' ? { type: 'env', op: 'remove', container: c, name: e.name } : { type: 'env', op: 'set', container: c, name: e.name, value: e.value }
    if (t === 'resource') return { type: 'resource', op: 'set', container: c, kind: e.kind, name: e.name, value: e.value }
    if (t === 'command' || t === 'args') return { type: t, container: c, value: String(e.value || '').split(/\s+/).filter(Boolean) }
    if (t === 'label' || t === 'annotation') return e.op === 'remove' ? { type: t, op: 'remove', target: e.target, key: e.key } : { type: t, op: 'set', target: e.target, key: e.key, value: e.value }
    if (t === 'affinity') {
      const subkey = e.subkey || 'all'
      // JSON.parse may throw on invalid input — createJob catches it and shows the error.
      return e.op === 'remove' ? { type: 'affinity', op: 'remove', subkey } : { type: 'affinity', op: 'set', subkey, value: JSON.parse(e.value || '{}') }
    }
    return e
  })

  const createJob = async () => {
    setError('')
    setSuccess('')

    if (!selectedProduct) {
      setError('Please select a product')
      return
    }
    if (updateMode === 'helm') {
      setError('Helm-managed products are upgraded by an administrator via the helm CLI — this tool does not run helm upgrade')
      return
    }
    if (updateMode === 'image' && !targetImage) {
      setError('Specify a target image')
      return
    }
    if (updateMode === 'fields' && fieldEdits.length === 0) {
      setError('Add at least one manifest edit')
      return
    }
    // Build the field-edit payload up front so an invalid affinity JSON is reported clearly.
    let fieldEditsPayload: any = null
    if (updateMode === 'fields' && fieldEdits.length) {
      try {
        fieldEditsPayload = buildFieldEditsPayload()
      } catch (e: any) {
        setError('Invalid affinity JSON: ' + (e.message || 'parse error'))
        return
      }
    }

    try {
      const resp = await fetch('/api/image-updates/jobs', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          product_name: selectedProduct,
          source_image: sourceImage ? (sourceVersion ? `${sourceImage}:${sourceVersion}` : sourceImage) : null,
          target_image: updateMode === 'image' ? (targetImage || null) : null,
          image_source: imageSource,
          target_platforms: selectedPlatforms.length > 0 ? selectedPlatforms : null,
          target_namespaces: (() => {
            if (sourceVersion && sourceImage) {
              const vInfo = availableSourceVersions.find(v => v.tag === sourceVersion)
              if (vInfo) {
                const tagNs = new Set(vInfo.namespaces)
                if (selectedNamespaces.length > 0) {
                  return selectedNamespaces.filter(ns => tagNs.has(ns))
                }
                return vInfo.namespaces
              }
            }
            return selectedNamespaces.length > 0 ? selectedNamespaces : null
          })(),
          // Send resource keys instead of IDs for cross-backend compatibility
          target_resource_keys: selectedResourceKeys.length > 0 ? selectedResourceKeys : null,
          scheduled_at: scheduleType === 'scheduled' && scheduledAt ? new Date(scheduledAt).toISOString() : null,
          approval_required: approvalRequired,
          health_check_mode: monitorRollout ? 'smart_watch' : 'off',
          field_edits: fieldEditsPayload,
          helm_managed_ack: helmManagedAck,
          notes: notes || null,
        })
      })

      if (resp.ok) {
        setSuccess('Update job created successfully!')
        setShowCreateModal(false)
        // Reset form immediately
        setSelectedProduct('')
        setCustomImage('')
        setNotes('')
        setSelectedResourceKeys([])
        setMonitorRollout(true)
        setFieldEdits([])
        setHelmManagedAck(false)
        setHelmManagedTargets([])
        setUpdateMode('image')
        // Refresh data in background (non-blocking)
        loadJobs()
        onResourcesChanged?.()
      } else {
        const data = await resp.json()
        const detail = data.detail
        if (resp.status === 409 && detail && typeof detail === 'object' && detail.error === 'helm_managed_ack_required') {
          // Helm-managed targets need an explicit second confirmation; surface the list + checkbox.
          setHelmManagedTargets(Array.isArray(detail.helm_managed) ? detail.helm_managed : [])
          setError(detail.message || 'Some targets are Helm-managed; acknowledge to proceed.')
        } else {
          setError(typeof detail === 'string' ? detail : (detail?.message || 'Failed to create job'))
        }
      }
    } catch (e) {
      setError('Failed to create job')
    }
  }

  const approveJob = async (jobId: number, approved: boolean) => {
    try {
      const resp = await fetch(`/api/image-updates/jobs/${jobId}/approve`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ approved, notes: null })
      })
      if (resp.ok) {
        loadJobs()
        if (showJobDetailModal && showJobDetailModal.id === jobId) {
          // Refresh the detail modal
          const detailResp = await fetch(`/api/image-updates/jobs/${jobId}`, {
            headers: { Authorization: `Bearer ${token}` }
          })
          if (detailResp.ok) {
            setShowJobDetailModal(await detailResp.json())
          }
        }
      }
    } catch (e) {
      console.error('Failed to approve job:', e)
    }
  }

  const dryRunJob = async (jobId: number) => {
    setActionLoading('dry_run')
    setDryRunReport(null)
    
    try {
      console.log('[DryRun] Starting dry run for job:', jobId)
      const resp = await fetch(`/api/image-updates/jobs/${jobId}/dry-run`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      })
      
      console.log('[DryRun] Response status:', resp.status)
      
      if (resp.ok) {
        const data = await resp.json()
        console.log('[DryRun] Response data:', data)
        console.log('[DryRun] Report:', data.report)
        
        if (data.report) {
          setDryRunReport(data.report)
          setShowDryRunModal(true)
          console.log('[DryRun] Modal should be visible now')
        } else {
          console.error('[DryRun] No report in response')
          setError('Dry run completed but no report returned')
        }
      } else {
        const data = await resp.json()
        console.error('[DryRun] Error response:', data)
        setError(data.detail || 'Failed to run dry run')
      }
    } catch (e) {
      console.error('[DryRun] Exception:', e)
      setError('Failed to perform dry run')
    } finally {
      setActionLoading('')
    }
  }

  const downloadDryRunCSV = (report: DryRunReport) => {
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
    const filename = `DryRun_Job${report.job_id}_${report.product}_${timestamp}.csv`
    
    // CSV header
    const headers = ['Report Type', 'Status', 'Platform', 'Namespace', 'Resource', 'Kind', 'Current Image', 'Target Image', 'Will Update', 'Message']
    
    // CSV rows
    const rows = report.details.map(d => {
      const willUpdate = d.containers?.some(c => c.will_update) ? 'Yes' : 'No'
      return [
        'DRY-RUN',  // Report type column
        d.status.toUpperCase(),
        d.platform,
        d.namespace,
        d.resource,
        d.kind,
        d.current_image || '',
        report.target_image,
        willUpdate,
        d.message
      ]
    })
    
    // Add summary rows at the end
    rows.push([])  // Empty row
    rows.push(['=== DRY-RUN SUMMARY ===', '', '', '', '', '', '', '', '', ''])
    rows.push(['Job ID', String(report.job_id), '', '', '', '', '', '', '', ''])
    rows.push(['Product', report.product, '', '', '', '', '', '', '', ''])
    rows.push(['Target Image', report.target_image, '', '', '', '', '', '', '', ''])
    rows.push(['Total Resources', String(report.summary.total), '', '', '', '', '', '', '', ''])
    rows.push(['Ready', String(report.summary.ready), '', '', '', '', '', '', '', ''])
    rows.push(['Warning', String(report.summary.warning), '', '', '', '', '', '', '', ''])
    rows.push(['Error', String(report.summary.error), '', '', '', '', '', '', '', ''])
    rows.push(['Generated At', new Date().toISOString(), '', '', '', '', '', '', '', ''])
    
    const csvContent = [
      headers.join(','),
      ...rows.map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(','))
    ].join('\n')
    
    const blob = new Blob(['\ufeff' + csvContent], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  const downloadDryRunPDF = (report: DryRunReport) => {
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
    
    // Generate table rows
    const tableRows = report.details.map(d => {
      const statusColor = d.status === 'ready' ? '#22c55e' : d.status === 'warning' ? '#f59e0b' : '#ef4444'
      const statusText = d.status === 'ready' ? '✓ READY' : d.status === 'warning' ? '⚠ WARNING' : '✕ ERROR'
      const willUpdate = d.containers?.some(c => c.will_update) ? 'Yes' : 'No'
      
      return `
        <tr>
          <td style="color: ${statusColor}; font-weight: 600;">${statusText}</td>
          <td>${d.platform}</td>
          <td>${d.namespace}</td>
          <td>${d.resource}<br><small style="color:#666">${d.kind}</small></td>
          <td style="font-size:9px;word-break:break-all">${d.current_image || '-'}</td>
          <td>${willUpdate}</td>
          <td style="font-size:9px">${d.message}</td>
        </tr>
      `
    }).join('')
    
    // Platform summary
    const platformSummary = Object.entries(report.by_platform).map(([platform, stats]) => `
      <div style="display:inline-block;padding:4px 10px;background:#f5f5f5;border-radius:4px;margin-right:8px;margin-bottom:4px;font-size:11px">
        <strong>${platform}</strong>: 
        <span style="color:#22c55e">${stats.ready}✓</span>
        ${stats.warning > 0 ? `<span style="color:#f59e0b;margin-left:4px">${stats.warning}⚠</span>` : ''}
        ${stats.error > 0 ? `<span style="color:#ef4444;margin-left:4px">${stats.error}✕</span>` : ''}
      </div>
    `).join('')
    
    const html = `
      <!DOCTYPE html>
      <html>
      <head>
        <title>Dry Run Report - Job #${report.job_id}</title>
        <style>
          body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 20px; font-size: 12px; }
          h1 { color: #1e3a5f; margin-bottom: 5px; }
          .watermark { background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); color: white; padding: 8px 16px; border-radius: 6px; display: inline-block; margin-bottom: 15px; font-weight: 600; }
          .subtitle { color: #666; margin-bottom: 15px; }
          .info-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 15px; }
          .info-item { background: #f5f5f5; padding: 8px; border-radius: 4px; }
          .info-label { color: #666; font-size: 10px; margin-bottom: 2px; }
          .info-value { font-weight: 600; word-break: break-all; font-size: 11px; }
          .stats { display: flex; gap: 15px; margin-bottom: 15px; }
          .stat { padding: 10px 15px; border-radius: 6px; text-align: center; }
          .stat.ready { background: #dcfce7; border-left: 4px solid #22c55e; }
          .stat.warning { background: #fef3c7; border-left: 4px solid #f59e0b; }
          .stat.error { background: #fee2e2; border-left: 4px solid #ef4444; }
          .stat.total { background: #f3f4f6; border-left: 4px solid #6b7280; }
          .stat-value { font-size: 22px; font-weight: 700; }
          .stat-label { font-size: 10px; color: #666; }
          .platforms { margin-bottom: 15px; }
          table { width: 100%; border-collapse: collapse; font-size: 10px; margin-top: 10px; }
          th { background: #1e3a5f; color: white; padding: 8px 6px; text-align: left; font-weight: 600; }
          td { padding: 6px; border-bottom: 1px solid #ddd; vertical-align: top; }
          tr:nth-child(even) { background: #f9f9f9; }
          .footer { margin-top: 20px; font-size: 10px; color: #666; text-align: center; border-top: 1px solid #ddd; padding-top: 10px; }
          @media print {
            body { margin: 10px; }
            .no-print { display: none; }
          }
        </style>
      </head>
      <body>
        <div class="watermark">🔍 DRY-RUN REPORT</div>
        <h1>Product Update Job #${report.job_id}</h1>
        <div class="subtitle">Product: ${report.product} | Generated: ${new Date().toLocaleString()}</div>
        
        <div class="info-grid">
          <div class="info-item">
            <div class="info-label">Target Image</div>
            <div class="info-value">${report.target_image}</div>
          </div>
          <div class="info-item">
            <div class="info-label">Source Filter</div>
            <div class="info-value">${report.source_image || 'All matching'}</div>
          </div>
          <div class="info-item">
            <div class="info-label">Report Type</div>
            <div class="info-value" style="color:#6366f1">DRY-RUN (No changes made)</div>
          </div>
          <div class="info-item">
            <div class="info-label">Status</div>
            <div class="info-value">${report.summary.error > 0 ? '⚠ Has Errors' : report.summary.warning > 0 ? '⚠ Has Warnings' : '✓ Ready to Execute'}</div>
          </div>
        </div>
        
        <div class="stats">
          <div class="stat ready">
            <div class="stat-value" style="color:#22c55e">${report.summary.ready}</div>
            <div class="stat-label">Ready</div>
          </div>
          <div class="stat warning">
            <div class="stat-value" style="color:#f59e0b">${report.summary.warning}</div>
            <div class="stat-label">Warning</div>
          </div>
          <div class="stat error">
            <div class="stat-value" style="color:#ef4444">${report.summary.error}</div>
            <div class="stat-label">Error</div>
          </div>
          <div class="stat total">
            <div class="stat-value">${report.summary.total}</div>
            <div class="stat-label">Total</div>
          </div>
        </div>
        
        ${Object.keys(report.by_platform).length > 1 ? `
          <div class="platforms">
            <strong style="font-size:11px;color:#666">By Platform:</strong><br>
            ${platformSummary}
          </div>
        ` : ''}
        
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Platform</th>
              <th>Namespace</th>
              <th>Resource</th>
              <th>Current Image</th>
              <th>Will Update</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            ${tableRows}
          </tbody>
        </table>
        
        <div class="footer">
          <strong>⚠ DRY-RUN REPORT - NO CHANGES WERE MADE</strong><br>
          This report shows what would happen if the job is executed. Review carefully before proceeding.<br>
          Generated: ${new Date().toISOString()}
        </div>
        
        <div class="no-print" style="margin-top:20px;text-align:center">
          <button onclick="window.print()" style="padding:10px 20px;font-size:14px;cursor:pointer">🖨️ Print / Save as PDF</button>
        </div>
      </body>
      </html>
    `
    
    const printWindow = window.open('', '_blank')
    if (printWindow) {
      printWindow.document.write(html)
      printWindow.document.close()
    }
  }

  const requestExecuteJob = async (jobId: number) => {
    setExecuteConfirmLoading(true)
    setExecuteConfirmJobId(jobId)
    try {
      const resp = await fetch('/api/admin/proxy-settings', {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (resp.ok) {
        const data = await resp.json()
        setExecuteConfirmParams({
          stuck_detection: data.stuck_detection_seconds ?? 300,
          crash_tolerance: data.crash_tolerance_seconds ?? 120,
          batch_size: data.patch_batch_size ?? 10,
          batch_pause: data.patch_batch_pause_seconds ?? 2,
        })
      } else {
        setExecuteConfirmParams({
          stuck_detection: adminStuckDetection,
          crash_tolerance: adminCrashTolerance,
          batch_size: adminBatchSize,
          batch_pause: adminBatchPause,
        })
      }
    } catch {
      setExecuteConfirmParams({
        stuck_detection: adminStuckDetection,
        crash_tolerance: adminCrashTolerance,
        batch_size: adminBatchSize,
        batch_pause: adminBatchPause,
      })
    }
    setExecuteConfirmLoading(false)
    setShowExecuteConfirm(true)
  }

  const executeJob = async (jobId: number) => {
    setActionLoading('executing')
    
    try {
      // Optimistically update UI to show "executing" status
      setJobs(prevJobs => prevJobs.map(j => 
        j.id === jobId ? { ...j, status: 'executing' } : j
      ))
      if (showJobDetailModal?.id === jobId) {
        setShowJobDetailModal(prev => prev ? { ...prev, status: 'executing' } : null)
      }

      const resp = await fetch(`/api/image-updates/jobs/${jobId}/execute`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      })
      
      setActionLoading('')  // Clear loading after initial response
      
      if (resp.ok) {
        setSuccess('Job execution started! Monitoring progress...')
        loadJobs()
      } else {
        const data = await resp.json()
        setError(data.detail || 'Failed to execute job')
        setActionLoading('')
        loadJobs() // Reload to get actual status
      }
    } catch (e) {
      setError('Failed to execute job')
      setActionLoading('')
      loadJobs()
    }
  }

  const cancelJob = async (jobId: number) => {
    setActionLoading('cancelling')
    
    try {
      const resp = await fetch(`/api/image-updates/jobs/${jobId}/cancel`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      })
      const data = await resp.json()
      if (resp.ok) {
        if (data.status === 'cancel_requested') {
          setSuccess(data.message || 'Cancellation requested. Click cancel again to force-cancel.')
          setJobs(prevJobs => prevJobs.map(j =>
            j.id === jobId ? { ...j, cancel_requested: true } : j
          ))
          if (showJobDetailModal?.id === jobId) {
            setShowJobDetailModal(prev => prev ? { ...prev, cancel_requested: true } : null)
          }
        } else {
          setSuccess('Job cancelled successfully')
          setJobs(prevJobs => prevJobs.map(j =>
            j.id === jobId ? { ...j, status: data.status || 'cancelled' } : j
          ))
          setShowJobDetailModal(null)
          onResourcesChanged?.()
        }
        loadJobs()
      } else {
        setError(data.detail || 'Failed to cancel job')
        loadJobs()
      }
    } catch (e) {
      console.error('Failed to cancel job:', e)
      setError('Failed to cancel job')
      loadJobs()
    } finally {
      setActionLoading('')
    }
  }

  const retryJob = async (jobId: number) => {
    setActionLoading('retrying')
    
    try {
      const resp = await fetch(`/api/image-updates/jobs/${jobId}/retry`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      })
      const data = await resp.json()
      if (resp.ok) {
        setSuccess(data.message || `Job reset to retry ${data.retry_count || 'failed'} resources.`)
        loadJobs()
        // Refresh the detail modal
        const detailResp = await fetch(`/api/image-updates/jobs/${jobId}`, {
          headers: { Authorization: `Bearer ${token}` }
        })
        if (detailResp.ok) {
          setShowJobDetailModal(await detailResp.json())
        }
      } else {
        setError(data.detail || 'Failed to retry job')
      }
    } catch (e) {
      setError('Failed to retry job')
    } finally {
      setActionLoading('')
    }
  }

  const requestRollback = async (jobId: number) => {
    setRollbackConfirmLoading(true)
    setError('')
    try {
      const resp = await fetch(`/api/image-updates/jobs/${jobId}/rollback?dry_run=true`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (!resp.ok) {
        const data = await resp.json()
        setError(data.detail || 'Failed to preview rollback')
        setRollbackConfirmLoading(false)
        return
      }
      const data = await resp.json()
      setRollbackPreview({
        job_id: data.job_id,
        product_name: data.product_name,
        total: data.total_to_rollback,
        items: data.items,
      })
      setShowRollbackConfirm(true)
    } catch {
      setError('Failed to preview rollback')
    }
    setRollbackConfirmLoading(false)
  }

  const confirmRollback = async () => {
    if (!rollbackPreview) return
    const jobId = rollbackPreview.job_id

    setShowRollbackConfirm(false)
    setRollbackPreview(null)
    setActionLoading('rolling_back')

    setJobs(prevJobs => prevJobs.map(j =>
      j.id === jobId ? { ...j, status: 'rolling_back' } : j
    ))
    if (showJobDetailModal?.id === jobId) {
      setShowJobDetailModal(prev => prev ? { ...prev, status: 'rolling_back' } : null)
    }

    try {
      const resp = await fetch(`/api/image-updates/jobs/${jobId}/rollback`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      })
      setActionLoading('')
      if (resp.ok) {
        setSuccess('Rollback started! Monitoring progress...')
        loadJobs()
      } else {
        const data = await resp.json()
        loadJobs()
        setError(data.detail || 'Failed to start rollback')
      }
    } catch {
      loadJobs()
      setError('Failed to rollback job')
      setActionLoading('')
    }
  }

  const getStatusBadge = (status: string) => {
    const colors: Record<string, string> = {
      pending_approval: 'rgba(245, 158, 11, 0.2)',
      approved: 'rgba(59, 130, 246, 0.2)',
      executing: 'rgba(139, 92, 246, 0.3)',
      rolling_back: 'rgba(245, 158, 11, 0.3)',
      cancelling: 'rgba(107, 114, 128, 0.3)',
      rolled_back: 'rgba(168, 85, 247, 0.2)',
      completed: 'rgba(34, 197, 94, 0.2)',
      completed_with_rollbacks: 'rgba(245, 158, 11, 0.2)',
      failed: 'rgba(239, 68, 68, 0.2)',
      cancelled: 'rgba(107, 114, 128, 0.2)',
    }
    const textColors: Record<string, string> = {
      pending_approval: '#f59e0b',
      approved: '#3b82f6',
      executing: '#a855f7',
      rolling_back: '#f59e0b',
      cancelling: '#6b7280',
      rolled_back: '#a855f7',
      completed: '#22c55e',
      completed_with_rollbacks: '#f59e0b',
      failed: '#ef4444',
      cancelled: '#6b7280',
    }
    const labels: Record<string, string> = {
      pending_approval: 'PENDING APPROVAL',
      approved: 'APPROVED',
      executing: '⏳ PROCESSING...',
      rolling_back: '⏳ ROLLING BACK...',
      cancelling: '⏳ CANCELLING...',
      rolled_back: 'ROLLED BACK',
      completed: 'COMPLETED',
      completed_with_rollbacks: '⚠️ COMPLETED (ROLLBACKS)',
      failed: 'FAILED',
      cancelled: 'CANCELLED',
    }
    return (
      <span style={{
        padding: '2px 8px',
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        background: colors[status] || 'rgba(107, 114, 128, 0.2)',
        color: textColors[status] || '#6b7280',
        ...(status === 'executing' ? { animation: 'pulse 1.5s infinite' } : {}),
      }}>
        {labels[status] || status.replace('_', ' ').toUpperCase()}
      </span>
    )
  }

  if (loading) {
    return (
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '60vh',
        gap: 24
      }}>
        <style>{`
          @keyframes spin {
            to { transform: rotate(360deg); }
          }
        `}</style>
        <div style={{
          width: 56,
          height: 56,
          border: '4px solid rgba(139, 92, 246, 0.15)',
          borderTopColor: '#8b5cf6',
          borderRadius: '50%',
          animation: 'spin 1s linear infinite'
        }} />
        <div style={{ textAlign: 'center' }}>
          <p style={{ margin: 0, fontSize: 16, color: '#e2e8f0' }}>Loading Update Product Data</p>
          <p className="muted" style={{ margin: '8px 0 0 0', fontSize: 12 }}>
            Fetching products from all federated backends...
          </p>
        </div>
      </div>
    )
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div>
          <h2 style={{ margin: 0 }}>Update Product</h2>
          <p className="muted" style={{ margin: '8px 0 0 0' }}>
            Patch container images across your products and platforms
          </p>
        </div>
        <button className="btn" onClick={() => {
          setShowCreateModal(true)
          // Start every create session clean — clear field edits / Helm-managed warning /
          // messages / mode so a prior (cancelled) session can't leak into a new product.
          setUpdateMode('image')
          setFieldEdits([])
          setHelmManagedAck(false)
          setHelmManagedTargets([])
          setError('')
          setSuccess('')
          // Reload platforms when modal opens (in case they weren't loaded)
          if (availablePlatforms.length === 0) {
            loadPlatforms()
          }
        }}>
          + New Update Job
        </button>
      </div>

      {error && (
        <div className="card" style={{ marginBottom: 16, borderColor: 'rgba(239, 68, 68, 0.4)', color: '#fca5a5' }}>
          {error}
          <button className="btn secondary" onClick={() => setError('')} style={{ marginLeft: 12, fontSize: 10, padding: '2px 8px' }}>
            Dismiss
          </button>
        </div>
      )}

      {success && (
        <div className="card" style={{ marginBottom: 16, borderColor: 'rgba(34, 197, 94, 0.4)', color: '#86efac' }}>
          {success}
          <button className="btn secondary" onClick={() => setSuccess('')} style={{ marginLeft: 12, fontSize: 10, padding: '2px 8px' }}>
            Dismiss
          </button>
        </div>
      )}

      {/* Quick Stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16, marginBottom: 24 }}>
        <div className="card" style={{ borderLeft: '4px solid #8b5cf6' }}>
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>Total Products</div>
          <div style={{ fontSize: 28, fontWeight: 600, color: '#8b5cf6' }}>{products.length}</div>
        </div>
        <div className="card" style={{ borderLeft: '4px solid #f59e0b' }}>
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>Pending Approval</div>
          <div style={{ fontSize: 28, fontWeight: 600, color: '#f59e0b' }}>
            {jobs.filter(j => j.status === 'pending_approval').length}
          </div>
        </div>
        <div className="card" style={{ borderLeft: '4px solid #3b82f6' }}>
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>Ready to Execute</div>
          <div style={{ fontSize: 28, fontWeight: 600, color: '#3b82f6' }}>
            {jobs.filter(j => j.status === 'approved').length}
          </div>
        </div>
        <div className="card" style={{ borderLeft: '4px solid #22c55e' }}>
          <div style={{ fontSize: 12, color: 'var(--muted)' }}>Completed</div>
          <div style={{ fontSize: 28, fontWeight: 600, color: '#22c55e' }}>
            {jobs.filter(j => j.status === 'completed').length}
          </div>
        </div>
      </div>

      {/* Jobs Table */}
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h3 style={{ margin: 0 }}>Update Jobs</h3>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {jobs.length > 0 && (
              <span className="muted" style={{ fontSize: 11 }}>
                Showing {filteredJobs.length} of {jobs.length} jobs
              </span>
            )}
            {filteredJobs.length > 0 && (
              <>
                <button 
                  className="btn secondary" 
                  onClick={downloadJobsCSV}
                  style={{ fontSize: 10, padding: '4px 10px' }}
                  title="Download as CSV"
                >
                  📥 CSV
                </button>
                <button 
                  className="btn secondary" 
                  onClick={downloadJobsPDF}
                  style={{ fontSize: 10, padding: '4px 10px' }}
                  title="Download as PDF"
                >
                  📄 PDF
                </button>
              </>
            )}
          </div>
        </div>

        {/* Filter Row */}
        {jobs.length > 0 && (
          <div style={{ 
            display: 'flex', 
            flexWrap: 'wrap', 
            gap: 12, 
            marginBottom: 16, 
            padding: 12, 
            background: 'rgba(30, 41, 59, 0.5)', 
            borderRadius: 8 
          }}>
            {/* Product Filter */}
            <div style={{ minWidth: 140 }}>
              <label style={{ fontSize: 10, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Product</label>
              <select
                value={jobFilterProduct}
                onChange={(e) => setJobFilterProduct(e.target.value)}
                style={{ fontSize: 11, padding: '4px 8px', width: '100%', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 4, color: 'inherit' }}
              >
                <option value="">All Products</option>
                {jobFilterOptions.products.map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>

            {/* Image Filter */}
            <div style={{ minWidth: 160 }}>
              <label style={{ fontSize: 10, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Image</label>
              <input
                type="text"
                placeholder="Search image..."
                value={jobFilterImage}
                onChange={(e) => setJobFilterImage(e.target.value)}
                style={{ fontSize: 11, padding: '4px 8px', width: '100%', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 4, color: 'inherit' }}
              />
            </div>

            {/* Status Filter */}
            <div style={{ minWidth: 130 }}>
              <label style={{ fontSize: 10, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Status</label>
              <select
                value={jobFilterStatus}
                onChange={(e) => setJobFilterStatus(e.target.value)}
                style={{ fontSize: 11, padding: '4px 8px', width: '100%', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 4, color: 'inherit' }}
              >
                <option value="">All Statuses</option>
                {jobFilterOptions.statuses.map(s => (
                  <option key={s} value={s}>{s.replace('_', ' ').toUpperCase()}</option>
                ))}
              </select>
            </div>

            {/* Platform Filter */}
            <div style={{ minWidth: 140 }}>
              <label style={{ fontSize: 10, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Platform</label>
              <select
                value={jobFilterPlatform}
                onChange={(e) => setJobFilterPlatform(e.target.value)}
                style={{ fontSize: 11, padding: '4px 8px', width: '100%', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 4, color: 'inherit' }}
              >
                <option value="">All Platforms</option>
                {jobFilterOptions.platforms.map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>

            {/* Result Filter */}
            <div style={{ minWidth: 120 }}>
              <label style={{ fontSize: 10, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Result</label>
              <select
                value={jobFilterResult}
                onChange={(e) => setJobFilterResult(e.target.value)}
                style={{ fontSize: 11, padding: '4px 8px', width: '100%', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 4, color: 'inherit' }}
              >
                <option value="">All Results</option>
                <option value="success">Has Success</option>
                <option value="failed">Has Failed</option>
                <option value="rolled_back">Has Rolled Back</option>
                <option value="skipped">Has Skipped</option>
              </select>
            </div>

            {/* Date From Filter */}
            <div style={{ minWidth: 130 }}>
              <label style={{ fontSize: 10, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Date From</label>
              <input
                type="date"
                value={jobFilterDateFrom}
                onChange={(e) => setJobFilterDateFrom(e.target.value)}
                style={{ fontSize: 11, padding: '4px 8px', width: '100%', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 4, color: 'inherit' }}
              />
            </div>

            {/* Date To Filter */}
            <div style={{ minWidth: 130 }}>
              <label style={{ fontSize: 10, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Date To</label>
              <input
                type="date"
                value={jobFilterDateTo}
                onChange={(e) => setJobFilterDateTo(e.target.value)}
                style={{ fontSize: 11, padding: '4px 8px', width: '100%', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 4, color: 'inherit' }}
              />
            </div>

            {/* Clear Filters Button */}
            {(jobFilterProduct || jobFilterImage || jobFilterStatus || jobFilterPlatform || jobFilterResult || jobFilterDateFrom || jobFilterDateTo) && (
              <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                <button
                  className="btn secondary"
                  onClick={() => {
                    setJobFilterProduct('')
                    setJobFilterImage('')
                    setJobFilterStatus('')
                    setJobFilterPlatform('')
                    setJobFilterResult('')
                    setJobFilterDateFrom('')
                    setJobFilterDateTo('')
                  }}
                  style={{ fontSize: 10, padding: '4px 10px' }}
                >
                  Clear Filters
                </button>
              </div>
            )}
          </div>
        )}

        {jobs.length === 0 ? (
          <p className="muted">No update jobs created yet. Click "New Update Job" to create one.</p>
        ) : filteredJobs.length === 0 ? (
          <p className="muted">No jobs match the selected filters.</p>
        ) : (
          <div className="tableWrap">
            <table style={{ width: '100%', fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <th style={{ textAlign: 'left', padding: 8 }}>ID</th>
                  <th style={{ textAlign: 'left', padding: 8 }}>Product</th>
                  <th style={{ textAlign: 'left', padding: 8 }}>Target Image</th>
                  <th style={{ textAlign: 'left', padding: 8 }}>Status</th>
                  <th style={{ textAlign: 'left', padding: 8 }}>Created</th>
                  <th style={{ textAlign: 'left', padding: 8 }}>Finished</th>
                  <th style={{ textAlign: 'left', padding: 8 }}>Results</th>
                  <th style={{ textAlign: 'center', padding: 8 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredJobs.map(job => (
                  <tr key={job.id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: 8 }}>#{job.id}</td>
                    <td style={{ padding: 8 }}>{job.product_name}</td>
                    <td style={{ padding: 8, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={job.target_image}>
                      {job.target_image}
                    </td>
                    <td style={{ padding: 8 }}>
                      {getStatusBadge(job.status)}
                      {/* Show progress for executing jobs */}
                      {job.status === 'executing' && job.progress_total && job.progress_total > 0 && (
                        <div style={{ marginTop: 4 }}>
                          <div style={{ 
                            width: '100%', 
                            height: 4, 
                            background: 'rgba(139, 92, 246, 0.2)', 
                            borderRadius: 2,
                            overflow: 'hidden'
                          }}>
                            <div style={{ 
                              width: `${Math.round((job.progress_current || 0) / job.progress_total * 100)}%`,
                              height: '100%',
                              background: '#a855f7',
                              transition: 'width 0.3s ease'
                            }} />
                          </div>
                          <div style={{ fontSize: 9, color: '#a855f7', marginTop: 2 }}>
                            {job.progress_current || 0}/{job.progress_total}
                            {job.progress_message?.startsWith('Health check') || job.progress_message?.startsWith('Verifying') ? ' verifications' : ' resources'}
                          </div>
                        </div>
                      )}
                    </td>
                    <td style={{ padding: 8 }}>{new Date(job.created_at).toLocaleString()}</td>
                    <td style={{ padding: 8, color: 'var(--muted)' }}>{job.finished_at ? new Date(job.finished_at).toLocaleString() : '-'}</td>
                    <td style={{ padding: 8 }}>
                      {job.results_summary ? (
                        <span>
                          <span style={{ color: '#22c55e' }}>{job.results_summary.success}✓</span>
                          {job.results_summary.failed > 0 && <span style={{ color: '#ef4444', marginLeft: 8 }}>{job.results_summary.failed}✗</span>}
                          {job.results_summary.rolled_back > 0 && <span style={{ color: '#f59e0b', marginLeft: 8 }}>{job.results_summary.rolled_back}↩</span>}
                          {job.results_summary.skipped > 0 && <span style={{ color: '#6b7280', marginLeft: 8 }}>{job.results_summary.skipped}⊘</span>}
                        </span>
                      ) : '-'}
                    </td>
                    <td style={{ padding: 8, textAlign: 'center' }}>
                      <button className="btn secondary" onClick={() => openJobDetail(job)} style={{ fontSize: 10, padding: '2px 8px' }}>
                        Details
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Global keyframes for animations */}
      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>

      {/* Create Job Modal */}
      {showCreateModal && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)', display: 'flex', alignItems: 'flex-start', justifyContent: 'center', zIndex: 1000, padding: 20, overflow: 'auto' }}>
          <div className="card" style={{ maxWidth: 800, width: '100%', maxHeight: 'calc(100vh - 40px)', overflow: 'hidden', display: 'flex', flexDirection: 'column', background: 'var(--panel)', margin: 'auto 0', flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingBottom: 16, borderBottom: '1px solid rgba(255,255,255,0.1)', flexShrink: 0 }}>
              <h2 style={{ margin: 0 }}>Create Update Job</h2>
              <button className="btn secondary" onClick={() => {
                setShowCreateModal(false)
                setWizardStep(0)
                setPreSelectedPlatforms([])
                setProducts([])
                setSelectedProduct('')
                setProductDetail(null)
                setNamespaceSearch('')
              }}>Close</button>
            </div>

            <div style={{ flex: 1, overflowY: 'auto', paddingTop: 16, minHeight: 0 }}>
            {/* Step 0: Select Platforms First (NEW - Optimized Flow) */}
            {wizardStep === 0 && (
            <div style={{ marginBottom: 24 }}>
                <h3 style={{ margin: '0 0 12px 0', fontSize: 14, color: '#8b5cf6' }}>1. Select Target Platforms</h3>
                <div className="muted" style={{ fontSize: 11, marginBottom: 16 }}>
                  Select the platforms you want to update. This will speed up product loading by only querying selected platforms.
                </div>
                
                {/* Loading state for platforms */}
                {loadingPlatforms && (
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: '40px 20px',
                    gap: 12
                  }}>
                    <div style={{
                      width: 24,
                      height: 24,
                      border: '3px solid rgba(139, 92, 246, 0.2)',
                      borderTopColor: '#8b5cf6',
                      borderRadius: '50%',
                      animation: 'spin 1s linear infinite'
                    }} />
                    <span className="muted">Loading platforms...</span>
                  </div>
                )}

                {/* Empty state */}
                {!loadingPlatforms && availablePlatforms.length === 0 && (
                  <div style={{
                    padding: '20px',
                    background: 'rgba(239, 68, 68, 0.1)',
                    borderRadius: 8,
                    marginBottom: 16,
                    border: '1px solid rgba(239, 68, 68, 0.3)',
                    textAlign: 'center'
                  }}>
                    <p style={{ color: '#fca5a5', margin: '0 0 12px 0' }}>
                      No platforms available. Please check your backend configuration.
                    </p>
                    <button 
                      className="btn secondary" 
                      onClick={loadPlatforms}
                      style={{ fontSize: 12 }}
                    >
                      🔄 Retry Loading Platforms
                    </button>
                  </div>
                )}

                {/* Platform selection */}
                {!loadingPlatforms && availablePlatforms.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 20 }}>
                    {availablePlatforms.map(p => (
                      <label 
                        key={p.platform} 
                        style={{ 
                          display: 'flex', 
                          alignItems: 'center', 
                          gap: 8, 
                          cursor: 'pointer', 
                          padding: '10px 16px', 
                          background: preSelectedPlatforms.includes(p.platform) 
                            ? 'rgba(139, 92, 246, 0.3)' 
                            : 'rgba(30, 41, 59, 0.5)', 
                          borderRadius: 8,
                          border: preSelectedPlatforms.includes(p.platform) 
                            ? '2px solid #8b5cf6' 
                            : '2px solid transparent',
                          fontSize: 13,
                          transition: 'all 0.2s'
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={preSelectedPlatforms.includes(p.platform)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setPreSelectedPlatforms([...preSelectedPlatforms, p.platform])
                            } else {
                              setPreSelectedPlatforms(preSelectedPlatforms.filter(x => x !== p.platform))
                            }
                          }}
                          style={{ width: 16, height: 16 }}
                        />
                        <span style={{ fontWeight: preSelectedPlatforms.includes(p.platform) ? 600 : 400 }}>
                          {p.platform}
                        </span>
                      </label>
                    ))}
                  </div>
                )}

                {preSelectedPlatforms.length > 0 && (
                  <div style={{ 
                    padding: '12px 16px', 
                    background: 'rgba(34, 197, 94, 0.1)', 
                    borderRadius: 8, 
                    marginBottom: 16,
                    border: '1px solid rgba(34, 197, 94, 0.3)'
                  }}>
                    <span style={{ color: '#22c55e', fontSize: 12 }}>
                      ✓ {preSelectedPlatforms.length} platform{preSelectedPlatforms.length > 1 ? 's' : ''} selected: {preSelectedPlatforms.join(', ')}
                    </span>
                  </div>
                )}

                <div style={{ display: 'flex', gap: 12 }}>
                  <button
                    className="btn"
                    disabled={preSelectedPlatforms.length === 0 || loading}
                    onClick={() => loadProducts(preSelectedPlatforms)}
                    style={{ flex: 1 }}
                  >
                    {loading ? (
                      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{
                          width: 16,
                          height: 16,
                          border: '2px solid rgba(255,255,255,0.3)',
                          borderTopColor: '#fff',
                          borderRadius: '50%',
                          animation: 'spin 1s linear infinite',
                          display: 'inline-block'
                        }} />
                        Loading Products...
                      </span>
                    ) : (
                      `Continue with ${preSelectedPlatforms.length} Platform${preSelectedPlatforms.length > 1 ? 's' : ''} →`
                    )}
                  </button>
                  <button
                    className="btn secondary"
                    disabled={loading}
                    onClick={() => {
                      setPreSelectedPlatforms(availablePlatforms.map(p => p.platform))
                      loadProducts()  // Load all
                    }}
                    style={{ whiteSpace: 'nowrap' }}
                  >
                    Load All Platforms
                  </button>
                </div>
              </div>
            )}

            {/* Loading state for products */}
            {loading && wizardStep === 0 && (
              <div style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '40px 20px',
                gap: 16
              }}>
                <div style={{
                  width: 40,
                  height: 40,
                  border: '3px solid rgba(139, 92, 246, 0.2)',
                  borderTopColor: '#8b5cf6',
                  borderRadius: '50%',
                  animation: 'spin 1s linear infinite'
                }} />
                <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                  Loading products from {preSelectedPlatforms.length > 0 ? preSelectedPlatforms.length : 'all'} platform{preSelectedPlatforms.length !== 1 ? 's' : ''}...
                </p>
              </div>
            )}

            {/* Step 1: Select Product (after platforms loaded) */}
            {wizardStep === 1 && (
              <>
                <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
                  <button 
                    className="btn secondary" 
                    onClick={() => {
                      setWizardStep(0)
                      setProducts([])
                      setSelectedProduct('')
                      setProductDetail(null)
                    }}
                    style={{ padding: '4px 12px', fontSize: 12 }}
                  >
                    ← Back to Platforms
                  </button>
                  <span className="muted" style={{ fontSize: 11 }}>
                    Platforms: {preSelectedPlatforms.length > 0 ? preSelectedPlatforms.join(', ') : 'All'}
                  </span>
                </div>

                <div style={{ marginBottom: 24 }}>
                  <h3 style={{ margin: '0 0 12px 0', fontSize: 14, color: '#8b5cf6' }}>2. Select Product</h3>
              <select
                className="input"
                value={selectedProduct}
                onChange={(e) => {
                  setSelectedProduct(e.target.value)
                  // Changing product invalidates a prior Helm-managed warning / edits.
                  setHelmManagedTargets([])
                  setHelmManagedAck(false)
                  setError('')
                }}
                style={{ width: '100%' }}
                    disabled={loadingProductDetail}
              >
                    <option value="">-- Select a product ({products.length} available) --</option>
                {products.map(p => (
                  <option key={p.product_name} value={p.product_name}>
                    {p.product_name} ({p.resources.length} resources across {p.platforms.length} platforms)
                  </option>
                ))}
              </select>
            </div>
              </>
            )}

            {/* Loading state for product details */}
            {loadingProductDetail && (
              <div style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '60px 20px',
                gap: 16
              }}>
                <div style={{
                  width: 36,
                  height: 36,
                  border: '3px solid rgba(139, 92, 246, 0.2)',
                  borderTopColor: '#8b5cf6',
                  borderRadius: '50%',
                  animation: 'spin 1s linear infinite'
                }} />
                <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                  Loading product details...
                </p>
              </div>
            )}

            {wizardStep === 1 && !loadingProductDetail && productDetail && (
              <>
                {/* Product Info - filtered by pre-selected platforms */}
                {(() => {
                  // Filter data based on pre-selected platforms
                  const filteredPlatforms = preSelectedPlatforms.length > 0
                    ? productDetail.platforms.filter(p => preSelectedPlatforms.includes(p))
                    : productDetail.platforms
                  const filteredResources = preSelectedPlatforms.length > 0
                    ? productDetail.resources.filter(r => preSelectedPlatforms.includes(r.platform))
                    : productDetail.resources
                  const filteredNamespaces = new Set(filteredResources.map(r => r.namespace))
                  
                  return (
                <div className="card" style={{ marginBottom: 24, background: 'rgba(139, 92, 246, 0.1)', borderColor: 'rgba(139, 92, 246, 0.3)' }}>
                  <h4 style={{ margin: '0 0 12px 0' }}>{productDetail.product_name}</h4>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16 }}>
                    <div>
                          <div className="muted" style={{ fontSize: 11 }}>Selected Platforms</div>
                          <div style={{ fontSize: 13 }}>{filteredPlatforms.join(', ')}</div>
                    </div>
                    <div>
                      <div className="muted" style={{ fontSize: 11 }}>Namespaces</div>
                          <div style={{ fontSize: 13 }}>{filteredNamespaces.size}</div>
                    </div>
                    <div>
                          <div className="muted" style={{ fontSize: 11 }}>Resources in Selected Platforms</div>
                          <div style={{ fontSize: 13 }}>{filteredResources.length}</div>
                    </div>
                  </div>
                </div>
                  )
                })()}

                {/* Mode: what to change. Patch = image and/or manifest fields (one job).
                    Helm is a disabled signpost: Helm-managed products are upgraded by an admin via
                    the helm CLI. Switches which inputs show below. */}
                <div style={{ marginBottom: 24 }}>
                  <h3 style={{ margin: '0 0 12px 0', fontSize: 14, color: '#8b5cf6' }}>What do you want to change?</h3>
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                    {([
                      { key: 'image', label: '🖼️ Image Update', desc: 'Change the container image' },
                      { key: 'fields', label: '📝 Manifest Edits', desc: 'env / resources / command / args / labels' },
                      { key: 'helm', label: '⎈ Helm Upgrade', desc: 'Admin runs helm upgrade (CLI)' },
                    ] as any[]).map((m: any) => {
                      const disabled = m.key === 'helm'
                      const active = updateMode === m.key
                      return (
                        <button key={m.key} type="button" disabled={disabled}
                          onClick={() => { if (!disabled) setUpdateMode(m.key) }}
                          style={{
                            flex: '1 1 180px', textAlign: 'left', padding: '10px 12px', borderRadius: 6,
                            cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
                            background: active ? 'rgba(139,92,246,0.18)' : 'rgba(255,255,255,0.04)',
                            border: `1px solid ${active ? '#8b5cf6' : 'var(--border)'}`, color: 'inherit',
                          }}>
                          <div style={{ fontWeight: 600, fontSize: 13 }}>{m.label}</div>
                          <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>{m.desc}</div>
                        </button>
                      )
                    })}
                  </div>
                </div>

                {/* Step 2: Select Targets (narrow down first) */}
                <div style={{ marginBottom: 24 }}>
                  <h3 style={{ margin: '0 0 12px 0', fontSize: 14, color: '#8b5cf6' }}>Select Targets</h3>
                  <div className="muted" style={{ fontSize: 11, marginBottom: 12 }}>
                    Narrow down which resources to update. This will filter the available source images.
                  </div>
                  
                  <div style={{ marginBottom: 16 }}>
                    <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>Platforms (only showing platforms you selected in Step 1)</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                      {/* Filter to only show pre-selected platforms */}
                      {(preSelectedPlatforms.length > 0 
                        ? productDetail.platforms.filter(p => preSelectedPlatforms.includes(p))
                        : productDetail.platforms
                      ).map(p => (
                        <label key={p} style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', padding: '4px 8px', background: 'rgba(139, 92, 246, 0.1)', borderRadius: 4, fontSize: 11 }}>
                          <input
                            type="checkbox"
                            checked={selectedPlatforms.includes(p)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setSelectedPlatforms([...selectedPlatforms, p])
                              } else {
                                setSelectedPlatforms(selectedPlatforms.filter(x => x !== p))
                              }
                              setSourceImage(''); setSourceVersion('')
                            }}
                          />
                          {p}
                        </label>
                      ))}
                    </div>
                  </div>

                  <div style={{ marginBottom: 16 }}>
                    <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
                      Namespaces (optional - leave empty for all)
                      {selectedNamespaces.length > 0 && (
                        <span style={{ marginLeft: 8, color: '#3b82f6' }}>
                          ({selectedNamespaces.length} selected)
                        </span>
                      )}
                    </div>
                    {/* Search input for namespaces */}
                    <input
                      type="text"
                      placeholder="🔍 Search namespaces..."
                      value={namespaceSearch}
                      onChange={(e) => setNamespaceSearch(e.target.value)}
                      className="input"
                      style={{ 
                        width: '100%', 
                        marginBottom: 8, 
                        padding: '6px 10px', 
                        fontSize: 11,
                        background: 'rgba(59, 130, 246, 0.05)',
                        border: '1px solid rgba(59, 130, 246, 0.2)'
                      }}
                    />
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, maxHeight: 180, overflow: 'auto' }}>
                      {availableNamespacesWithPlatform
                        .filter(nsInfo => nsInfo.namespace.toLowerCase().includes(namespaceSearch.toLowerCase()))
                        .map(nsInfo => (
                        <label key={nsInfo.namespace} style={{ 
                          display: 'flex', 
                          alignItems: 'center', 
                          gap: 6, 
                          cursor: 'pointer', 
                          padding: '4px 8px', 
                          background: selectedNamespaces.includes(nsInfo.namespace) 
                            ? 'rgba(59, 130, 246, 0.3)' 
                            : 'rgba(59, 130, 246, 0.1)', 
                          borderRadius: 4, 
                          fontSize: 11,
                          border: selectedNamespaces.includes(nsInfo.namespace) 
                            ? '1px solid rgba(59, 130, 246, 0.5)' 
                            : '1px solid transparent'
                        }}>
                          <input
                            type="checkbox"
                            checked={selectedNamespaces.includes(nsInfo.namespace)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setSelectedNamespaces([...selectedNamespaces, nsInfo.namespace])
                              } else {
                                setSelectedNamespaces(selectedNamespaces.filter(x => x !== nsInfo.namespace))
                              }
                              setSourceImage(''); setSourceVersion('')
                            }}
                          />
                          <div>
                            <div>{nsInfo.namespace}</div>
                            {selectedPlatforms.length > 1 && (
                              <div className="muted" style={{ fontSize: 9 }}>
                                {nsInfo.platforms.map(p => p.replace('-openshift', '')).join(', ')}
                              </div>
                            )}
                          </div>
                        </label>
                      ))}
                      {availableNamespacesWithPlatform.filter(nsInfo => nsInfo.namespace.toLowerCase().includes(namespaceSearch.toLowerCase())).length === 0 && (
                        <span className="muted" style={{ fontSize: 11 }}>No namespaces match "{namespaceSearch}"</span>
                      )}
                    </div>
                  </div>

                  {/* Kind filter */}
                  {availableKinds.length > 0 && (
                    <div>
                      <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>Kind (optional - leave empty for all)</div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                        {availableKinds.map(kind => (
                          <label key={kind} style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', padding: '4px 8px', background: 'rgba(249, 115, 22, 0.1)', borderRadius: 4, fontSize: 11 }}>
                            <input
                              type="checkbox"
                              checked={selectedKinds.includes(kind)}
                              onChange={(e) => {
                                if (e.target.checked) {
                                  setSelectedKinds([...selectedKinds, kind])
                                } else {
                                  setSelectedKinds(selectedKinds.filter(x => x !== kind))
                                }
                                setSourceImage(''); setSourceVersion('')
                              }}
                            />
                            <span style={{ 
                              padding: '1px 4px', 
                              borderRadius: 3, 
                              fontSize: 9,
                              background: kind === 'deployment' ? 'rgba(34, 197, 94, 0.3)' : kind === 'statefulset' ? 'rgba(59, 130, 246, 0.3)' : 'rgba(249, 115, 22, 0.3)'
                            }}>
                              {kind}
                            </span>
                          </label>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Image-mode only: source + target image selection */}
                {updateMode === 'image' && (<>
                {/* Step 3: Select Source Image (FROM) */}
                <div style={{ marginBottom: 24 }}>
                  <h3 style={{ margin: '0 0 12px 0', fontSize: 14, color: '#f59e0b' }}>4. Select Source Image (Update FROM)</h3>
                  <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
                    Select which image repository to update. Only containers using this image will be affected.
                  </div>
                  
                  {/* Show all images toggle */}
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, cursor: 'pointer', fontSize: 11 }}>
                    <input
                      type="checkbox"
                      checked={showAllSourceImages}
                      onChange={(e) => setShowAllSourceImages(e.target.checked)}
                    />
                    <span>Show all images (not just "{selectedProduct}" matching)</span>
                  </label>
                  
                  {uniqueSourceImages.length > 0 ? (
                    <>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 220, overflow: 'auto' }}>
                      {/* Option to update all images */}
                      <label style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        gap: 8, 
                        cursor: 'pointer', 
                        padding: '8px 12px', 
                        background: sourceImage === '' ? 'rgba(249, 115, 22, 0.2)' : 'rgba(107, 114, 128, 0.1)', 
                        borderRadius: 6, 
                        fontSize: 12,
                        border: sourceImage === '' ? '1px solid rgba(249, 115, 22, 0.5)' : '1px solid transparent',
                      }}>
                        <input
                          type="radio"
                          name="sourceImage"
                          checked={sourceImage === ''}
                          onChange={() => { setSourceImage(''); setSourceVersion('') }}
                        />
                        <div>
                          <div style={{ fontWeight: 500 }}>All image repositories</div>
                          <div className="muted" style={{ fontSize: 10 }}>
                            Update all {uniqueSourceImages.reduce((sum, s) => sum + s.count, 0)} containers in selected targets
                          </div>
                        </div>
                      </label>
                      
                      {uniqueSourceImages.map(src => (
                        <label key={src.repo} style={{ 
                          display: 'flex', 
                          alignItems: 'center', 
                          gap: 8, 
                          cursor: 'pointer', 
                          padding: '8px 12px', 
                          background: sourceImage === src.repo 
                            ? 'rgba(249, 115, 22, 0.2)' 
                            : src.matchesProduct 
                              ? 'rgba(34, 197, 94, 0.1)' 
                              : 'rgba(107, 114, 128, 0.1)', 
                          borderRadius: 6, 
                          fontSize: 12,
                          border: sourceImage === src.repo 
                            ? '1px solid rgba(249, 115, 22, 0.5)' 
                            : src.matchesProduct 
                              ? '1px solid rgba(34, 197, 94, 0.3)' 
                              : '1px solid transparent',
                          opacity: src.matchesProduct ? 1 : 0.7,
                        }}>
                          <input
                            type="radio"
                            name="sourceImage"
                            checked={sourceImage === src.repo}
                            onChange={() => { setSourceImage(src.repo); setSourceVersion('') }}
                          />
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontWeight: 500, wordBreak: 'break-all' }}>
                              {src.repo}
                              {src.matchesProduct && <span style={{ marginLeft: 6, fontSize: 9, color: '#22c55e' }}>✓ matches</span>}
                              {!src.matchesProduct && <span style={{ marginLeft: 6, fontSize: 9, color: '#6b7280' }}>other image</span>}
                            </div>
                            <div className="muted" style={{ fontSize: 10 }}>
                              {src.count} container{src.count > 1 ? 's' : ''} • 
                              Versions: {Array.from(src.versions).slice(0, 3).join(', ')}{src.versions.size > 3 ? '...' : ''}
                            </div>
                            {selectedPlatforms.length > 1 && src.platforms.size > 0 && (
                              <div className="muted" style={{ fontSize: 9, color: '#3b82f6' }}>
                                📍 {Array.from(src.platforms).map(p => p.replace('-openshift', '')).join(', ')}
                              </div>
                            )}
                          </div>
                        </label>
                      ))}
                    </div>

                    {sourceImage && availableSourceVersions.length >= 1 && (
                      <div style={{ marginTop: 12 }}>
                        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
                          Filter by version/tag:
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                          {availableSourceVersions.map(v => (
                            <label key={v.tag} style={{
                              display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer',
                              padding: '4px 10px', borderRadius: 4, fontSize: 11,
                              background: sourceVersion === v.tag ? 'rgba(249,115,22,0.2)' : 'rgba(107,114,128,0.1)',
                              border: sourceVersion === v.tag ? '1px solid rgba(249,115,22,0.5)' : '1px solid transparent',
                            }}>
                              <input type="radio" name="sourceVersion" checked={sourceVersion === v.tag} onChange={() => setSourceVersion(v.tag)} style={{ width: 12, height: 12 }} />
                              {v.tag} ({v.count})
                            </label>
                          ))}
                        </div>
                        {sourceVersion && (() => {
                          const sel = availableSourceVersions.find(v => v.tag === sourceVersion)
                          if (!sel || sel.namespaces.length === 0) return null
                          const allSelected = sel.namespaces.every(ns => selectedNamespaces.includes(ns))
                          return (
                            <div style={{ marginTop: 8, padding: '8px 10px', background: 'rgba(59,130,246,0.1)', borderRadius: 4 }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                                <span style={{ fontSize: 10, color: '#3b82f6', fontWeight: 500 }}>
                                  Namespaces with :{sourceVersion} ({sel.namespaces.length})
                                  {selectedNamespaces.length > 0 && (
                                    <span style={{ marginLeft: 6, color: '#f59e0b' }}>
                                      {sel.namespaces.filter(ns => selectedNamespaces.includes(ns)).length} selected
                                    </span>
                                  )}
                                </span>
                                <div style={{ display: 'flex', gap: 6 }}>
                                  <button
                                    onClick={() => setSelectedNamespaces(allSelected ? [] : [...sel.namespaces])}
                                    style={{ padding: '2px 8px', fontSize: 9, borderRadius: 3, border: '1px solid rgba(59,130,246,0.3)', background: 'rgba(59,130,246,0.15)', color: '#3b82f6', cursor: 'pointer' }}
                                  >
                                    {allSelected ? 'Clear all' : 'Select all'}
                                  </button>
                                </div>
                              </div>
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, maxHeight: 120, overflow: 'auto' }}>
                                {sel.namespaces.map(ns => {
                                  const checked = selectedNamespaces.includes(ns)
                                  return (
                                    <span
                                      key={ns}
                                      onClick={() => {
                                        if (checked) {
                                          setSelectedNamespaces(selectedNamespaces.filter(x => x !== ns))
                                        } else {
                                          setSelectedNamespaces([...selectedNamespaces, ns])
                                        }
                                      }}
                                      style={{
                                        padding: '2px 8px', borderRadius: 3, fontSize: 10, cursor: 'pointer',
                                        background: checked ? 'rgba(59,130,246,0.3)' : 'rgba(107,114,128,0.15)',
                                        border: checked ? '1px solid rgba(59,130,246,0.5)' : '1px solid transparent',
                                        color: checked ? '#60a5fa' : 'inherit',
                                      }}
                                    >
                                      {checked ? '✓ ' : ''}{ns}
                                    </span>
                                  )
                                })}
                              </div>
                            </div>
                          )
                        })()}
                      </div>
                    )}
                    </>
                  ) : (
                    <div>
                      <div className="muted" style={{ marginBottom: 8 }}>No source images found for selected targets</div>
                      {filterDebugInfo && (
                        <div style={{ padding: '8px 12px', background: 'rgba(239, 68, 68, 0.1)', borderRadius: 6, fontSize: 10 }}>
                          <div className="muted" style={{ marginBottom: 4 }}>Debug info:</div>
                          <div>• Total resources: {filterDebugInfo.total}</div>
                          <div>• After platform filter: {filterDebugInfo.afterPlatform}</div>
                          <div>• After namespace filter: {filterDebugInfo.afterNs}</div>
                          <div>• After kind filter: {filterDebugInfo.afterKind}</div>
                          <div>• Matching "{selectedProduct}" images: {filterDebugInfo.afterProduct}</div>
                          
                          {filterDebugInfo.sampleImages && filterDebugInfo.sampleImages.length > 0 && (
                            <div style={{ marginTop: 8, padding: '6px 8px', background: 'rgba(0,0,0,0.2)', borderRadius: 4 }}>
                              <div className="muted" style={{ marginBottom: 4 }}>Sample resources found (current_image values):</div>
                              {filterDebugInfo.sampleImages.map((s, i) => (
                                <div key={i} style={{ 
                                  marginBottom: 4, 
                                  padding: '4px 6px', 
                                  background: s.matches ? 'rgba(34, 197, 94, 0.2)' : 'rgba(239, 68, 68, 0.2)',
                                  borderRadius: 3,
                                  wordBreak: 'break-all'
                                }}>
                                  <div><strong>{s.resource}</strong> / {s.container}</div>
                                  <div style={{ fontFamily: 'monospace', fontSize: 9 }}>
                                    {s.image} {s.matches ? '✅' : '❌'}
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                          
                          {filterDebugInfo.afterKind > 0 && filterDebugInfo.afterProduct === 0 && (
                            <div style={{ marginTop: 8, color: '#f59e0b' }}>
                              ⚠️ Resources found but no images match product name "{selectedProduct}". 
                              The current_image field might be empty or formatted differently.
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Step 4: Select Target Image (TO) */}
                <div style={{ marginBottom: 24 }}>
                  <h3 style={{ margin: '0 0 12px 0', fontSize: 14, color: '#22c55e' }}>5. Select Target Image (Update TO)</h3>
                  <div style={{ display: 'flex', gap: 16, marginBottom: 12 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                      <input
                        type="radio"
                        checked={imageSource === 'latest'}
                        onChange={() => setImageSource('latest')}
                      />
                      <span>Use Latest Available</span>
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                      <input
                        type="radio"
                        checked={imageSource === 'custom'}
                        onChange={() => setImageSource('custom')}
                      />
                      <span>Custom Image</span>
                    </label>
                  </div>

                  {imageSource === 'latest' ? (
                    <div>
                      <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
                        {sourceImage ? `Select target version for ${sourceImage}:` : 'Select target image:'}
                      </div>
                      {filteredLatestImages.length > 0 ? (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                          {filteredLatestImages.slice(0, 8).map((img, idx) => (
                            <label 
                              key={idx} 
                              style={{ 
                                display: 'flex', 
                                alignItems: 'center', 
                                gap: 8,
                                padding: '6px 10px', 
                                background: selectedLatestImage === img ? 'rgba(34, 197, 94, 0.2)' : 'rgba(107, 114, 128, 0.1)', 
                                borderRadius: 6, 
                                fontSize: 12,
                                cursor: 'pointer',
                                border: selectedLatestImage === img ? '1px solid rgba(34, 197, 94, 0.5)' : '1px solid transparent',
                              }}
                            >
                              <input
                                type="radio"
                                name="latestImage"
                                checked={selectedLatestImage === img}
                                onChange={() => setSelectedLatestImage(img)}
                              />
                              <span style={{ wordBreak: 'break-all' }}>{img}</span>
                              {idx === 0 && <span style={{ fontSize: 9, color: '#22c55e', marginLeft: 'auto' }}>recommended</span>}
                            </label>
                          ))}
                        </div>
                      ) : (
                        <div className="muted">
                          {sourceImage 
                            ? `No latest image detected for ${sourceImage}` 
                            : `No latest images detected for ${selectedProduct}`}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div>
                      <input
                        className="input"
                        type="text"
                        placeholder={sourceImage ? `${sourceImage}:v2.0.0` : "e.g., registry.example.com/myapp:v2.0.0"}
                        value={customImage}
                        onChange={(e) => setCustomImage(e.target.value.trim())}
                        style={{ width: '100%' }}
                      />
                      <div className="muted" style={{ fontSize: 10, marginTop: 4 }}>
                        {sourceImage 
                          ? `Will update all containers using ${sourceImage} to this image`
                          : `Current images: ${productDetail.current_images.slice(0, 3).join(', ')}`}
                      </div>
                    </div>
                  )}
                </div>
                </>)}

                {/* Step 5: Specific Resources (optional) - grouped by platform */}
                {availableResources.length > 0 && (
                  <div style={{ marginBottom: 24 }}>
                    <h3 style={{ margin: '0 0 12px 0', fontSize: 14, color: '#8b5cf6' }}>6. Specific Resources (Optional)</h3>
                    <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
                      Leave empty to update all {availableResources.length} matching resources, or select specific ones.
                    </div>
                    
                    {/* Group resources by platform */}
                    {(() => {
                      const byPlatform = new Map<string, typeof availableResources>()
                      availableResources.forEach(r => {
                        if (!byPlatform.has(r.platform)) {
                          byPlatform.set(r.platform, [])
                        }
                        byPlatform.get(r.platform)!.push(r)
                      })
                      const platforms = Array.from(byPlatform.keys()).sort()
                      const showPlatformHeaders = platforms.length > 1
                      
                      return (
                        <div style={{ maxHeight: 250, overflow: 'auto' }}>
                          {platforms.map(platform => (
                            <div key={platform} style={{ marginBottom: showPlatformHeaders ? 12 : 0 }}>
                              {showPlatformHeaders && (
                                <div style={{ 
                                  fontSize: 10, 
                                  fontWeight: 600, 
                                  marginBottom: 6, 
                                  color: '#60a5fa',
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: 8,
                                }}>
                                  <span>{platform}</span>
                                  <span className="muted" style={{ fontWeight: 400 }}>
                                    ({byPlatform.get(platform)!.length} resources)
                                  </span>
                                </div>
                              )}
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                                {byPlatform.get(platform)!.map(r => {
                                  const resourceKey = `${r.platform}|${r.namespace}|${r.kind}|${r.resource_name}`
                                  const isSelected = selectedResourceKeys.includes(resourceKey)
                                  return (
                        <label 
                                      key={resourceKey} 
                          style={{ 
                            display: 'flex', 
                            alignItems: 'center', 
                            gap: 6, 
                            cursor: 'pointer', 
                            padding: '4px 8px', 
                                        background: isSelected ? 'rgba(34, 197, 94, 0.2)' : 'rgba(107, 114, 128, 0.1)', 
                            borderRadius: 4, 
                            fontSize: 10,
                                        border: isSelected ? '1px solid rgba(34, 197, 94, 0.5)' : '1px solid transparent',
                          }}
                          title={`${r.kind}: ${r.resource_name}\nNamespace: ${r.namespace}\nPlatform: ${r.platform}\nImage: ${r.current_image}`}
                        >
                          <input
                            type="checkbox"
                                        checked={isSelected}
                            onChange={(e) => {
                              if (e.target.checked) {
                                            setSelectedResourceKeys([...selectedResourceKeys, resourceKey])
                              } else {
                                            setSelectedResourceKeys(selectedResourceKeys.filter(x => x !== resourceKey))
                              }
                            }}
                          />
                          <span style={{ 
                            padding: '1px 4px', 
                            borderRadius: 3, 
                            fontSize: 8,
                            marginRight: 4,
                            background: r.kind === 'deployment' ? 'rgba(34, 197, 94, 0.3)' : r.kind === 'statefulset' ? 'rgba(59, 130, 246, 0.3)' : 'rgba(249, 115, 22, 0.3)'
                          }}>
                            {r.kind?.charAt(0).toUpperCase()}
                          </span>
                          <span title={r.resource_name} style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {r.resource_name}
                          </span>
                        </label>
                                  )
                                })}
                              </div>
                            </div>
                      ))}
                    </div>
                      )
                    })()}
                    {selectedResourceKeys.length > 0 && (
                      <div style={{ marginTop: 8, fontSize: 11 }}>
                        <span style={{ color: '#22c55e' }}>{selectedResourceKeys.length} resource(s) selected</span>
                        <button 
                          className="btn secondary" 
                          onClick={() => setSelectedResourceKeys([])} 
                          style={{ marginLeft: 8, fontSize: 9, padding: '2px 6px' }}
                        >
                          Clear
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {/* Step 6: Schedule */}
                <div style={{ marginBottom: 24 }}>
                  <h3 style={{ margin: '0 0 12px 0', fontSize: 14, color: '#8b5cf6' }}>7. Schedule</h3>
                  <div style={{ display: 'flex', gap: 16, marginBottom: 12 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                      <input
                        type="radio"
                        checked={scheduleType === 'now'}
                        onChange={() => setScheduleType('now')}
                      />
                      <span>Execute Now (after approval)</span>
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                      <input
                        type="radio"
                        checked={scheduleType === 'scheduled'}
                        onChange={() => setScheduleType('scheduled')}
                      />
                      <span>Schedule for Later</span>
                    </label>
                  </div>

                  {scheduleType === 'scheduled' && (
                    <div>
                      <input
                        className="input"
                        type="datetime-local"
                        value={scheduledAt}
                        onChange={(e) => setScheduledAt(e.target.value)}
                        style={{ colorScheme: 'dark' }}
                      />
                      <div className="muted" style={{ fontSize: 10, marginTop: 6 }}>
                        🕐 Enter time in your local timezone ({Intl.DateTimeFormat().resolvedOptions().timeZone})
                      </div>
                      {scheduledAt && (
                        <div style={{ fontSize: 11, marginTop: 4, padding: '6px 8px', background: 'rgba(59, 130, 246, 0.1)', borderRadius: 4 }}>
                          <span className="muted">Will execute at: </span>
                          <span style={{ color: '#3b82f6' }}>
                            {new Date(scheduledAt).toLocaleString()} (local)
                          </span>
                          <span className="muted"> = </span>
                          <span style={{ color: '#22c55e' }}>
                            {new Date(scheduledAt).toISOString().replace('T', ' ').slice(0, 19)} UTC
                          </span>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Step 7: Approval & Health Check */}
                <div style={{ marginBottom: 24 }}>
                  <h3 style={{ margin: '0 0 12px 0', fontSize: 14, color: '#8b5cf6' }}>7. Options</h3>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginBottom: 8 }}>
                    <input
                      type="checkbox"
                      checked={approvalRequired}
                      onChange={(e) => setApprovalRequired(e.target.checked)}
                    />
                    <span>Require approval before execution</span>
                  </label>
                  <div className="muted" style={{ fontSize: 10, marginTop: 0, marginBottom: 12 }}>
                    When enabled, the job will wait for manual approval or Azure DevOps pipeline trigger before executing.
                  </div>

                  <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 6 }}>Rollout Monitoring</div>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                    <input type="checkbox"
                      checked={monitorRollout}
                      onChange={(e) => setMonitorRollout(e.target.checked)}
                    />
                    <span>Smart Watch — monitor rollout after patching</span>
                  </label>
                  <div className="muted" style={{ fontSize: 10, marginTop: 6 }}>
                    {monitorRollout
                      ? 'All resources monitored concurrently. CrashLoopBackOff/ImagePullBackOff tolerated briefly. Auto-rollback on persistent failures.'
                      : 'No monitoring. Patches applied without verification.'}
                  </div>
                  {monitorRollout && (
                    <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 6, padding: '6px 8px', background: 'rgba(255,255,255,0.04)', borderRadius: 4, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2px 16px' }}>
                      <span>Stuck Detection: <strong>{adminStuckDetection}s</strong></span>
                      <span>Crash Tolerance: <strong>{adminCrashTolerance}s</strong></span>
                      <span>Batch Size: <strong>{adminBatchSize}</strong></span>
                      <span>Batch Pause: <strong>{adminBatchPause}s</strong></span>
                      <span style={{ gridColumn: '1 / -1', opacity: 0.7, marginTop: 2 }}>Source: Admin Settings</span>
                    </div>
                  )}
                </div>

                {/* Notes */}
                <div style={{ marginBottom: 24 }}>
                  <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>Notes (optional)</div>
                  <textarea
                    className="input"
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder="Add any notes about this update..."
                    rows={2}
                    style={{ width: '100%', resize: 'vertical' }}
                  />
                </div>

                {/* Preview - calculated client-side from already-loaded data */}
                  <div className="card" style={{ marginBottom: 24, background: 'rgba(59, 130, 246, 0.1)', borderColor: 'rgba(59, 130, 246, 0.3)' }}>
                    <h4 style={{ margin: '0 0 12px 0' }}>Preview</h4>
                  
                  {/* Total summary */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 12, marginBottom: Object.keys(clientPreview.byPlatform).length > 1 ? 16 : 0 }}>
                      <div>
                      <div className="muted" style={{ fontSize: 10 }}>Total Resources</div>
                      <div style={{ fontSize: 18, fontWeight: 600 }}>{clientPreview.resources}</div>
                      </div>
                      <div>
                      <div className="muted" style={{ fontSize: 10 }}>Total Containers</div>
                      <div style={{ fontSize: 18, fontWeight: 600 }}>{clientPreview.containers}</div>
                      </div>
                      <div>
                        <div className="muted" style={{ fontSize: 10 }}>Will Change</div>
                      <div style={{ fontSize: 18, fontWeight: 600, color: '#f59e0b' }}>{clientPreview.willChange}</div>
                      </div>
                    </div>
                  
                  {/* Per-platform breakdown (only show if multiple platforms) */}
                  {Object.keys(clientPreview.byPlatform).length > 1 && (
                    <div style={{ borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: 12 }}>
                      <div className="muted" style={{ fontSize: 10, marginBottom: 8 }}>Breakdown by Platform</div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                        {Object.entries(clientPreview.byPlatform).map(([platform, data]) => (
                          <div key={platform} style={{ 
                            display: 'flex', 
                            alignItems: 'center', 
                            justifyContent: 'space-between',
                            padding: '6px 10px',
                            background: 'rgba(0,0,0,0.2)',
                            borderRadius: 4,
                            fontSize: 11,
                          }}>
                            <span style={{ fontWeight: 500 }}>{platform}</span>
                            <div style={{ display: 'flex', gap: 16 }}>
                              <span><span className="muted">Resources:</span> {data.resources}</span>
                              <span><span className="muted">Containers:</span> {data.containers}</span>
                              <span style={{ color: data.willChange > 0 ? '#f59e0b' : 'inherit' }}>
                                <span className="muted">Change:</span> {data.willChange}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                  </div>
                )}
                </div>

                {/* Manifest edits — shown only in 'fields' mode */}
                {updateMode === 'fields' && (
                <div style={{ marginBottom: 24 }}>
                  <h3 style={{ margin: '0 0 8px 0', fontSize: 14, color: '#8b5cf6' }}>Manifest Edits</h3>
                  <div className="muted" style={{ fontSize: 10, marginBottom: 8 }}>
                    Structured, whitelisted edits applied in one rollout (no image change).
                    env/resources/command/args target the matched container; labels/annotations target the pod template or workload; affinity (JSON) targets the pod template.
                  </div>
                  {fieldEdits.map((e: any, i: number) => (
                    <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 6, padding: 6, background: 'rgba(255,255,255,0.03)', borderRadius: 4 }}>
                      <select className="input" style={{ width: 110, fontSize: 11 }} value={e.type} onChange={ev => updateFieldEdit(i, { type: ev.target.value })}>
                        <option value="env">env</option>
                        <option value="resource">resource</option>
                        <option value="command">command</option>
                        <option value="args">args</option>
                        <option value="label">label</option>
                        <option value="annotation">annotation</option>
                        <option value="affinity">affinity</option>
                      </select>
                      {(e.type === 'env' || e.type === 'resource' || e.type === 'command' || e.type === 'args') && (
                        <select className="input" style={{ width: 160, fontSize: 11 }} value={e.container}
                          onChange={ev => updateFieldEdit(i, { container: ev.target.value })}
                          title="Container to edit — only product-matched containers. '(matched container)' targets the one carrying the product image.">
                          <option value="">(matched container)</option>
                          {matchedContainers.map((cn: string) => <option key={cn} value={cn}>{cn}</option>)}
                        </select>
                      )}
                      {e.type === 'env' && (<>
                        <select className="input" style={{ width: 84, fontSize: 11 }} value={e.op} onChange={ev => updateFieldEdit(i, { op: ev.target.value })}><option value="set">set</option><option value="remove">remove</option></select>
                        <input className="input" style={{ width: 140, fontSize: 11 }} placeholder="NAME" value={e.name} onChange={ev => updateFieldEdit(i, { name: ev.target.value })} />
                        {e.op !== 'remove' && <input className="input" style={{ width: 160, fontSize: 11 }} placeholder="value" value={e.value} onChange={ev => updateFieldEdit(i, { value: ev.target.value })} />}
                      </>)}
                      {e.type === 'resource' && (<>
                        <select className="input" style={{ width: 92, fontSize: 11 }} value={e.kind} onChange={ev => updateFieldEdit(i, { kind: ev.target.value })}><option value="request">request</option><option value="limit">limit</option></select>
                        <select className="input" style={{ width: 92, fontSize: 11 }} value={e.name} onChange={ev => updateFieldEdit(i, { name: ev.target.value })}><option value="">cpu/mem…</option><option value="cpu">cpu</option><option value="memory">memory</option></select>
                        <input className="input" style={{ width: 110, fontSize: 11 }} placeholder="e.g. 512Mi" value={e.value} onChange={ev => updateFieldEdit(i, { value: ev.target.value })} />
                      </>)}
                      {(e.type === 'command' || e.type === 'args') && (
                        <input className="input" style={{ flex: 1, minWidth: 200, fontSize: 11 }} placeholder="space-separated, e.g. --port=8080 --verbose" value={e.value} onChange={ev => updateFieldEdit(i, { value: ev.target.value })} />
                      )}
                      {(e.type === 'label' || e.type === 'annotation') && (<>
                        <select className="input" style={{ width: 92, fontSize: 11 }} value={e.target} onChange={ev => updateFieldEdit(i, { target: ev.target.value })}><option value="pod">pod</option><option value="workload">workload</option></select>
                        <select className="input" style={{ width: 84, fontSize: 11 }} value={e.op} onChange={ev => updateFieldEdit(i, { op: ev.target.value })}><option value="set">set</option><option value="remove">remove</option></select>
                        <input className="input" style={{ width: 150, fontSize: 11 }} placeholder="key" value={e.key} onChange={ev => updateFieldEdit(i, { key: ev.target.value })} />
                        {e.op !== 'remove' && <input className="input" style={{ width: 150, fontSize: 11 }} placeholder="value" value={e.value} onChange={ev => updateFieldEdit(i, { value: ev.target.value })} />}
                      </>)}
                      {e.type === 'affinity' && (<>
                        <select className="input" style={{ width: 150, fontSize: 11 }} value={e.subkey || 'all'} onChange={ev => updateFieldEdit(i, { subkey: ev.target.value })}>
                          <option value="all">all (full affinity)</option>
                          <option value="podAntiAffinity">podAntiAffinity</option>
                          <option value="podAffinity">podAffinity</option>
                          <option value="nodeAffinity">nodeAffinity</option>
                        </select>
                        <select className="input" style={{ width: 84, fontSize: 11 }} value={e.op} onChange={ev => updateFieldEdit(i, { op: ev.target.value })}><option value="set">set</option><option value="remove">remove</option></select>
                        {e.op !== 'remove' && <textarea className="input" style={{ flex: 1, minWidth: 280, fontSize: 11, fontFamily: 'monospace', minHeight: 64, resize: 'vertical' }}
                          placeholder={(e.subkey || 'all') === 'all' ? '{ "podAntiAffinity": { ... } }  (full affinity JSON)' : 'sub-block JSON, e.g. { "requiredDuringSchedulingIgnoredDuringExecution": [ ... ] }'}
                          value={e.value} onChange={ev => updateFieldEdit(i, { value: ev.target.value })} />}
                      </>)}
                      <button className="btn secondary" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => removeFieldEdit(i)}>✕</button>
                    </div>
                  ))}
                  <button className="btn secondary" style={{ fontSize: 11, padding: '3px 10px' }} onClick={addFieldEdit}>+ Add edit</button>
                </div>
                )}

                {/* Helm-managed acknowledgment — shown proactively for field-edit jobs whose
                    selected targets are Helm-managed (incl. remote), plus the API 409 fallback. */}
                {((updateMode === 'fields' && helmManagedSelected.length > 0) || helmManagedTargets.length > 0) && (
                  <div className="card" style={{ marginBottom: 24, background: 'rgba(245, 158, 11, 0.12)', borderColor: 'rgba(245, 158, 11, 0.4)' }}>
                    <h4 style={{ margin: '0 0 6px 0', color: '#f59e0b' }}>⚠️ Helm-managed targets</h4>
                    <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
                      A direct patch to these is reverted by the next <code>helm upgrade</code> (for a durable change, an administrator should run <code>helm upgrade</code> via the CLI). Acknowledge to proceed anyway.
                    </div>
                    <ul style={{ margin: '0 0 8px 0', paddingLeft: 18, fontSize: 11 }}>
                      {(helmManagedSelected.length > 0 ? helmManagedSelected : helmManagedTargets).map((t: any, i: number) => (
                        <li key={i}>{t.platform}/{t.namespace}/{t.kind}/{t.resource_name}{t.release ? ` (release: ${t.release})` : ''}</li>
                      ))}
                    </ul>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                      <input type="checkbox" checked={helmManagedAck} onChange={ev => setHelmManagedAck(ev.target.checked)} />
                      <span>I understand — apply the direct patch to Helm-managed targets anyway</span>
                    </label>
                  </div>
                )}

                {/* Actions */}
                <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
                  <button className="btn secondary" onClick={() => setShowCreateModal(false)}>Cancel</button>
                  <button className="btn" onClick={createJob} disabled={updateMode === 'helm' || (updateMode === 'image' && !targetImage) || (updateMode === 'fields' && (fieldEdits.length === 0 || (helmManagedSelected.length > 0 && !helmManagedAck)))}>
                    Create Update Job
                  </button>
                </div>
              </>
            )}
            </div>{/* end scrollable body */}
          </div>
        </div>
      )}

      {/* Job Detail Modal */}
      {showJobDetailModal && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)', display: 'flex', alignItems: 'flex-start', justifyContent: 'center', zIndex: 1000, padding: 20, overflow: 'auto' }}>
          <div className="card" style={{ maxWidth: 700, width: '100%', maxHeight: 'calc(100vh - 40px)', overflow: 'hidden', display: 'flex', flexDirection: 'column', background: 'var(--panel)', margin: 'auto 0', flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingBottom: 16, borderBottom: '1px solid rgba(255,255,255,0.1)', flexShrink: 0 }}>
              <h2 style={{ margin: 0 }}>Job #{showJobDetailModal.id} Details</h2>
              <button className="btn secondary" onClick={() => setShowJobDetailModal(null)}>Close</button>
            </div>

            <div style={{ flex: 1, overflowY: 'auto', paddingTop: 16, minHeight: 0 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 24 }}>
              <div>
                <div className="muted" style={{ fontSize: 10 }}>Product</div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{showJobDetailModal.product_name}</div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10 }}>Status</div>
                <div>{getStatusBadge(showJobDetailModal.status)}</div>
              </div>
              
              {/* Live Progress for executing jobs */}
              {showJobDetailModal.status === 'executing' && (
                <div style={{ gridColumn: '1 / -1' }}>
                  <div className="card" style={{ background: 'rgba(139, 92, 246, 0.15)', borderColor: 'rgba(139, 92, 246, 0.4)', padding: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                      <div className="muted" style={{ fontSize: 11 }}>Live Progress</div>
                      {!showJobDetailModal.cancel_requested && (
                        <button 
                          className="btn" 
                          onClick={async () => {
                            if (confirm('Are you sure you want to cancel this job? It will stop at the next resource.')) {
                              try {
                                const resp = await fetch(`/api/image-updates/jobs/${showJobDetailModal.id}/request-cancel`, {
                                  method: 'POST',
                                  headers: { 'Authorization': `Bearer ${token}` }
                                })
                                if (resp.ok) {
                                  setSuccess('Cancellation requested. Job will stop at the next safe point.')
                                  setShowJobDetailModal(prev => prev ? { ...prev, cancel_requested: true } : null)
                                } else {
                                  const data = await resp.json()
                                  setError(data.detail || 'Failed to request cancellation')
                                }
                              } catch (e) {
                                setError('Failed to request cancellation')
                              }
                            }
                          }}
                          style={{ fontSize: 11, padding: '4px 12px', background: 'rgba(239, 68, 68, 0.2)', color: '#ef4444', border: '1px solid rgba(239, 68, 68, 0.4)' }}
                        >
                          ⏹ Cancel Job
                        </button>
                      )}
                      {showJobDetailModal.cancel_requested && (
                        <span style={{ fontSize: 11, color: '#f59e0b', fontWeight: 600 }}>
                          ⏳ Cancellation requested...
                        </span>
                      )}
                    </div>
                    
                    {/* Progress Bar */}
                    <div style={{ 
                      width: '100%', 
                      height: 8, 
                      background: 'rgba(139, 92, 246, 0.2)', 
                      borderRadius: 4,
                      overflow: 'hidden',
                      marginBottom: 8
                    }}>
                      <div style={{ 
                        width: showJobDetailModal.progress_total 
                          ? `${Math.round((showJobDetailModal.progress_current || 0) / showJobDetailModal.progress_total * 100)}%`
                          : '0%',
                        height: '100%',
                        background: 'linear-gradient(90deg, #8b5cf6, #a855f7)',
                        transition: 'width 0.5s ease',
                        animation: 'pulse 1.5s infinite'
                      }} />
                    </div>
                    
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                      <span style={{ fontSize: 12, fontWeight: 600, color: '#a855f7' }}>
                        {showJobDetailModal.progress_current || 0} / {showJobDetailModal.progress_total || '?'}
                        {showJobDetailModal.progress_message?.startsWith('Health check') || showJobDetailModal.progress_message?.startsWith('Verifying') ? ' verifications' : ' resources'}
                      </span>
                      <span style={{ fontSize: 11, color: '#a855f7' }}>
                        {showJobDetailModal.progress_total 
                          ? `${Math.round((showJobDetailModal.progress_current || 0) / showJobDetailModal.progress_total * 100)}%`
                          : 'Calculating...'}
                      </span>
                    </div>
                    
                    {/* Live Results Table */}
                    {jobResults.length > 0 && (
                      <div style={{ marginTop: 12 }}>
                        <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 8 }}>
                          Processing Results ({jobResults.length} completed)
                        </div>
                        <div style={{ maxHeight: 250, overflow: 'auto', borderRadius: 4, border: '1px solid rgba(139, 92, 246, 0.3)' }}>
                          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
                            <thead>
                              <tr style={{ background: 'rgba(0,0,0,0.3)', position: 'sticky', top: 0 }}>
                                <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(139, 92, 246, 0.3)' }}>Platform</th>
                                <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(139, 92, 246, 0.3)' }}>Namespace</th>
                                <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(139, 92, 246, 0.3)' }}>Resource</th>
                                <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(139, 92, 246, 0.3)' }}>Container</th>
                                <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(139, 92, 246, 0.3)' }}>Source Image</th>
                                <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(139, 92, 246, 0.3)' }}>Target Image</th>
                                <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(139, 92, 246, 0.3)' }}>State</th>
                              </tr>
                            </thead>
                            <tbody>
                              {jobResults.map((r, idx) => {
                                // Extract version from image for cleaner display
                                const sourceVersion = r.old_image?.split(':').pop() || '-'
                                const targetVersion = r.new_image?.split(':').pop() || '-'
                                return (
                                  <tr key={idx} style={{ background: idx % 2 === 0 ? 'rgba(0,0,0,0.2)' : 'transparent' }}>
                                    <td style={{ padding: '6px 8px', color: '#60a5fa', whiteSpace: 'nowrap' }}>{r.platform}</td>
                                    <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>{r.namespace}</td>
                                    <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }} title={r.resource_name}>{r.resource_name}</td>
                                    <td style={{ padding: '6px 8px', color: '#22c55e', whiteSpace: 'nowrap' }}>{r.container_name || '-'}</td>
                                    <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 9, color: '#9ca3af' }}>{sourceVersion}</td>
                                    <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 9, color: '#22c55e' }}>{targetVersion}</td>
                                    <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>
                                      <span style={{ 
                                        padding: '2px 6px', 
                                        borderRadius: 4, 
                                        fontSize: 9,
                                        fontWeight: 600,
                                        background: r.status === 'success' ? 'rgba(34, 197, 94, 0.2)' : 
                                                    r.status === 'failed' ? 'rgba(239, 68, 68, 0.2)' :
                                                    r.status === 'skipped' ? 'rgba(107, 114, 128, 0.2)' :
                                                    r.status === 'rolled_back' ? 'rgba(168, 85, 247, 0.2)' :
                                                    'rgba(139, 92, 246, 0.2)',
                                        color: r.status === 'success' ? '#22c55e' : 
                                               r.status === 'failed' ? '#ef4444' :
                                               r.status === 'skipped' ? '#9ca3af' :
                                               r.status === 'rolled_back' ? '#a855f7' :
                                               '#a855f7'
                                      }}>
                                        {r.status === 'success' ? '✓ Done' : 
                                         r.status === 'failed' ? '✗ Failed' :
                                         r.status === 'skipped' ? '○ Skipped' :
                                         r.status === 'rolled_back' ? '↩ Rolled Back' :
                                         '⟳ Updating'}
                                      </span>
                                    </td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                    
                    {/* Currently processing message */}
                    {showJobDetailModal.progress_message && (
                      <div style={{ 
                        fontSize: 10, 
                        marginTop: 8, 
                        padding: '6px 8px', 
                        background: 'rgba(139, 92, 246, 0.2)', 
                        borderRadius: 4, 
                        fontFamily: 'monospace',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8
                      }}>
                        <span style={{ color: '#a855f7', animation: 'pulse 1s infinite' }}>⟳</span>
                        <span style={{ color: '#a855f7', wordBreak: 'break-all' }}>
                          {showJobDetailModal.progress_message}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              )}
              
              {/* Show actual source version from results, or source_image filter */}
              {(jobResults.length > 0 && jobResults[0].old_image) ? (
                <div style={{ gridColumn: '1 / -1' }}>
                  <div className="muted" style={{ fontSize: 10 }}>Source Image (Updated From)</div>
                  <div style={{ fontSize: 12, fontFamily: 'monospace', background: 'rgba(0,0,0,0.3)', padding: 8, borderRadius: 4, wordBreak: 'break-all' }}>
                    {jobResults[0].old_image}
                  </div>
                </div>
              ) : showJobDetailModal.source_image && (
                <div style={{ gridColumn: '1 / -1' }}>
                  <div className="muted" style={{ fontSize: 10 }}>
                    Source Repository Filter
                    <span style={{ marginLeft: 8, fontSize: 9, opacity: 0.7 }}>
                      (exact version shown after completion)
                    </span>
                  </div>
                  <div style={{ fontSize: 12, fontFamily: 'monospace', background: 'rgba(0,0,0,0.3)', padding: 8, borderRadius: 4, wordBreak: 'break-all' }}>
                    {showJobDetailModal.source_image}
                  </div>
                </div>
              )}
              <div style={{ gridColumn: '1 / -1' }}>
                <div className="muted" style={{ fontSize: 10 }}>Target Image</div>
                <div style={{ fontSize: 12, fontFamily: 'monospace', background: 'rgba(0,0,0,0.3)', padding: 8, borderRadius: 4, wordBreak: 'break-all' }}>
                  {showJobDetailModal.target_image}
                </div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10 }}>Created By</div>
                <div>{showJobDetailModal.created_by}</div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10 }}>Created At</div>
                <div>{new Date(showJobDetailModal.created_at).toLocaleString()}</div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10 }}>Monitoring</div>
                <div style={{ color: showJobDetailModal.health_check_enabled ? '#22c55e' : '#f59e0b' }}>
                  {showJobDetailModal.health_check_enabled ? '✓ Smart Watch' : '✗ Off'}
                </div>
              </div>
              {showJobDetailModal.approved_by && (
                <>
                  <div>
                    <div className="muted" style={{ fontSize: 10 }}>Approved By</div>
                    <div>{showJobDetailModal.approved_by}</div>
                  </div>
                  <div>
                    <div className="muted" style={{ fontSize: 10 }}>Approved At</div>
                    <div>{showJobDetailModal.approved_at ? new Date(showJobDetailModal.approved_at).toLocaleString() : '-'}</div>
                  </div>
                </>
              )}
              {showJobDetailModal.finished_at && (
                <div>
                  <div className="muted" style={{ fontSize: 10 }}>Finished At</div>
                  <div>{new Date(showJobDetailModal.finished_at).toLocaleString()}</div>
                </div>
              )}
              {showJobDetailModal.cancelled_at && (
                <div>
                  <div className="muted" style={{ fontSize: 10, color: '#ef4444' }}>Cancelled At</div>
                  <div style={{ color: '#ef4444' }}>{new Date(showJobDetailModal.cancelled_at).toLocaleString()}</div>
                </div>
              )}
              {showJobDetailModal.scheduled_at && (
                <div>
                  <div className="muted" style={{ fontSize: 10 }}>Scheduled For</div>
                  <div>{new Date(showJobDetailModal.scheduled_at).toLocaleString()}</div>
                </div>
              )}
              <div>
                <div className="muted" style={{ fontSize: 10 }}>Target Platforms</div>
                <div>{showJobDetailModal.target_platforms ? showJobDetailModal.target_platforms.join(', ') : 'All platforms'}</div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10 }}>Target Namespaces</div>
                <div>{showJobDetailModal.target_namespaces ? showJobDetailModal.target_namespaces.join(', ') : 'All namespaces'}</div>
              </div>
            </div>

            {showJobDetailModal.trigger_token && (
              <div className="card" style={{ marginBottom: 16, background: 'rgba(139, 92, 246, 0.1)', borderColor: 'rgba(139, 92, 246, 0.3)' }}>
                <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>Azure DevOps Pipeline Trigger URL</div>
                <div style={{ fontSize: 11, fontFamily: 'monospace', background: 'rgba(0,0,0,0.3)', padding: 8, borderRadius: 4, wordBreak: 'break-all' }}>
                  POST /api/image-updates/trigger/{showJobDetailModal.trigger_token}
                </div>
                <div className="muted" style={{ fontSize: 10, marginTop: 4 }}>
                  Use this URL in your Azure DevOps pipeline to trigger the update after CD approval.
                </div>
              </div>
            )}

            {showJobDetailModal.results_summary && (
              <div className="card" style={{ marginBottom: 16, background: 'rgba(34, 197, 94, 0.1)', borderColor: 'rgba(34, 197, 94, 0.3)' }}>
                {/* Header with Title and Export Buttons */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <h4 style={{ margin: 0, fontSize: 14 }}>
                    {showJobDetailModal.status === 'completed' ? '✓ Job Completed' : 
                     showJobDetailModal.status === 'failed' ? '✗ Job Failed' : 
                     showJobDetailModal.status === 'rolled_back' ? '↩ Job Rolled Back' : 'Job Results'}
                  </h4>
                  {/* Export Buttons */}
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button 
                      onClick={() => downloadResults(showJobDetailModal.id, 'csv')}
                      style={{ fontSize: 10, padding: '4px 10px', background: 'rgba(34, 197, 94, 0.2)', border: '1px solid rgba(34, 197, 94, 0.4)', borderRadius: 4, cursor: 'pointer', color: '#22c55e' }}
                    >
                      📊 CSV
                    </button>
                    <button 
                      onClick={() => downloadResults(showJobDetailModal.id, 'pdf')}
                      style={{ fontSize: 10, padding: '4px 10px', background: 'rgba(239, 68, 68, 0.2)', border: '1px solid rgba(239, 68, 68, 0.4)', borderRadius: 4, cursor: 'pointer', color: '#ef4444' }}
                    >
                      📄 PDF
                    </button>
                    <button 
                      onClick={() => downloadResults(showJobDetailModal.id, 'json')}
                      style={{ fontSize: 10, padding: '4px 10px', background: 'rgba(59, 130, 246, 0.2)', border: '1px solid rgba(59, 130, 246, 0.4)', borderRadius: 4, cursor: 'pointer', color: '#3b82f6' }}
                    >
                      📥 JSON
                    </button>
                    <button 
                      onClick={() => {
                        const data = {
                          job_id: showJobDetailModal.id,
                          product: showJobDetailModal.product_name,
                          target_image: showJobDetailModal.target_image,
                          status: showJobDetailModal.status,
                          summary: showJobDetailModal.results_summary,
                          results: jobResults.map(r => ({
                            platform: r.platform,
                            namespace: r.namespace,
                            kind: r.kind,
                            resource: r.resource_name,
                            container: r.container_name,
                            source_image: r.old_image,
                            target_image: r.new_image,
                            status: r.status,
                            health_check: r.health_check_passed,
                            error: r.error_message
                          }))
                        }
                        navigator.clipboard.writeText(JSON.stringify(data, null, 2))
                        setSuccess('Results copied to clipboard!')
                      }}
                      style={{ fontSize: 10, padding: '4px 10px', background: 'rgba(168, 85, 247, 0.2)', border: '1px solid rgba(168, 85, 247, 0.4)', borderRadius: 4, cursor: 'pointer', color: '#a855f7' }}
                    >
                      📋 Copy
                    </button>
                  </div>
                </div>

                {/* Statistics Summary */}
                <div style={{ 
                  display: 'grid', 
                  gridTemplateColumns: 'repeat(auto-fit, minmax(100px, 1fr))', 
                  gap: 12, 
                  marginBottom: 16,
                  padding: 12,
                  background: 'rgba(0,0,0,0.2)',
                  borderRadius: 8
                }}>
                  <div style={{ textAlign: 'center', padding: 8 }}>
                    <div style={{ fontSize: 24, fontWeight: 700, color: '#60a5fa' }}>{showJobDetailModal.results_summary.total}</div>
                    <div style={{ fontSize: 10, color: 'var(--muted)' }}>TOTAL</div>
                  </div>
                  <div style={{ textAlign: 'center', padding: 8, background: 'rgba(34, 197, 94, 0.1)', borderRadius: 6 }}>
                    <div style={{ fontSize: 24, fontWeight: 700, color: '#22c55e' }}>{showJobDetailModal.results_summary.success}</div>
                    <div style={{ fontSize: 10, color: '#22c55e' }}>SUCCESS</div>
                  </div>
                  <div style={{ textAlign: 'center', padding: 8, background: 'rgba(239, 68, 68, 0.1)', borderRadius: 6 }}>
                    <div style={{ fontSize: 24, fontWeight: 700, color: '#ef4444' }}>{showJobDetailModal.results_summary.failed}</div>
                    <div style={{ fontSize: 10, color: '#ef4444' }}>FAILED</div>
                  </div>
                  <div style={{ textAlign: 'center', padding: 8, background: 'rgba(107, 114, 128, 0.1)', borderRadius: 6 }}>
                    <div style={{ fontSize: 24, fontWeight: 700, color: '#9ca3af' }}>{showJobDetailModal.results_summary.skipped}</div>
                    <div style={{ fontSize: 10, color: '#9ca3af' }}>SKIPPED</div>
                  </div>
                  {showJobDetailModal.results_summary.rolled_back > 0 && (
                    <div style={{ textAlign: 'center', padding: 8, background: 'rgba(245, 158, 11, 0.1)', borderRadius: 6 }}>
                      <div style={{ fontSize: 24, fontWeight: 700, color: '#f59e0b' }}>{showJobDetailModal.results_summary.rolled_back}</div>
                      <div style={{ fontSize: 10, color: '#f59e0b' }}>ROLLED BACK</div>
                    </div>
                  )}
                </div>

                {/* Detailed Results Table */}
                {loadingResults ? (
                  <div className="muted" style={{ marginTop: 12, fontSize: 11, textAlign: 'center' }}>
                    <span style={{ animation: 'pulse 1s infinite' }}>⟳</span> Loading details...
                  </div>
                ) : jobResults.length > 0 ? (
                  <div style={{ marginTop: 8 }}>
                    <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 8 }}>
                      Detailed Results ({jobResults.length} resources)
                    </div>
                    <div style={{ maxHeight: 350, overflow: 'auto', borderRadius: 4, border: '1px solid rgba(34, 197, 94, 0.3)' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
                      <thead>
                          <tr style={{ background: 'rgba(0,0,0,0.4)', position: 'sticky', top: 0 }}>
                            <th style={{ padding: '8px 10px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(34, 197, 94, 0.3)' }}>Platform</th>
                            <th style={{ padding: '8px 10px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(34, 197, 94, 0.3)' }}>Namespace</th>
                            <th style={{ padding: '8px 10px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(34, 197, 94, 0.3)' }}>Kind</th>
                            <th style={{ padding: '8px 10px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(34, 197, 94, 0.3)' }}>Resource</th>
                            <th style={{ padding: '8px 10px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(34, 197, 94, 0.3)' }}>Container</th>
                            <th style={{ padding: '8px 10px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(34, 197, 94, 0.3)' }}>Source Image</th>
                            <th style={{ padding: '8px 10px', textAlign: 'left', fontWeight: 600, borderBottom: '1px solid rgba(34, 197, 94, 0.3)' }}>Target Image</th>
                            <th style={{ padding: '8px 10px', textAlign: 'center', fontWeight: 600, borderBottom: '1px solid rgba(34, 197, 94, 0.3)' }}>Status</th>
                            <th style={{ padding: '8px 10px', textAlign: 'center', fontWeight: 600, borderBottom: '1px solid rgba(34, 197, 94, 0.3)' }}>Health</th>
                          </tr>
                        </thead>
                        <tbody>
                          {jobResults.map((result, idx) => {
                            const sourceVersion = result.old_image?.split(':').pop() || '-'
                            const targetVersion = result.new_image?.split(':').pop() || '-'
                            return (
                              <React.Fragment key={idx}>
                                <tr style={{ 
                                  background: idx % 2 === 0 ? 'rgba(0,0,0,0.2)' : 'transparent',
                                  borderBottom: '1px solid rgba(255,255,255,0.05)'
                                }}>
                                  <td style={{ padding: '8px 10px', color: '#60a5fa', whiteSpace: 'nowrap' }}>{result.platform}</td>
                                  <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>{result.namespace}</td>
                                  <td style={{ padding: '8px 10px', whiteSpace: 'nowrap', color: '#a855f7' }}>{result.kind}</td>
                                  <td style={{ padding: '8px 10px', whiteSpace: 'nowrap', fontWeight: 500 }} title={result.resource_name}>{result.resource_name}</td>
                                  <td style={{ padding: '8px 10px', color: '#22c55e', whiteSpace: 'nowrap' }}>{result.container_name || '-'}</td>
                                  <td style={{ padding: '8px 10px', fontFamily: 'monospace', fontSize: 9, color: '#9ca3af' }}>{sourceVersion}</td>
                                  <td style={{ padding: '8px 10px', fontFamily: 'monospace', fontSize: 9, color: result.was_rolled_back ? '#f59e0b' : '#22c55e' }}>
                                    {result.was_rolled_back ? '↩ ' : ''}{targetVersion}
                                  </td>
                                  <td style={{ padding: '8px 10px', textAlign: 'center', whiteSpace: 'nowrap' }}>
                                    <span style={{ 
                                      padding: '3px 8px', 
                                      borderRadius: 4, 
                                      fontSize: 9,
                                      fontWeight: 600,
                                      background: result.status === 'success' ? 'rgba(34, 197, 94, 0.2)' : 
                                                  result.status === 'failed' ? 'rgba(239, 68, 68, 0.2)' :
                                                  result.status === 'skipped' ? 'rgba(107, 114, 128, 0.2)' :
                                                  result.status === 'rolled_back' ? 'rgba(245, 158, 11, 0.2)' :
                                                  'rgba(139, 92, 246, 0.2)',
                                      color: result.status === 'success' ? '#22c55e' : 
                                             result.status === 'failed' ? '#ef4444' :
                                             result.status === 'skipped' ? '#9ca3af' :
                                             result.status === 'rolled_back' ? '#f59e0b' :
                                             '#a855f7'
                                    }}>
                                      {result.status === 'success' ? '✓ Completed' : 
                                       result.status === 'failed' ? '✗ Failed' :
                                       result.status === 'skipped' ? '○ Skipped' :
                                       result.status === 'rolled_back' ? '↩ Rolled Back' :
                                       result.status}
                                    </span>
                            </td>
                                  <td style={{ padding: '8px 10px', textAlign: 'center' }}>
                                    {result.health_check_passed === true && (
                                      <span style={{ color: '#22c55e', fontSize: 12 }} title={result.health_check_message || 'Healthy'}>🟢</span>
                                    )}
                                    {result.health_check_passed === false && (
                                      <span style={{ color: '#ef4444', fontSize: 12, cursor: 'help' }} title={result.health_check_message || 'Health check failed'}>🔴</span>
                                    )}
                                    {result.health_check_passed == null && result.status !== 'skipped' && (
                                      <span className="muted" style={{ fontSize: 10 }}>-</span>
                              )}
                            </td>
                                </tr>
                                {/* Expandable Log Row - Always show for failed/rolled_back, or when details exist */}
                                {(result.status === 'failed' || result.status === 'rolled_back' || result.error_message || result.rollback_reason || result.execution_log || result.health_check_message || (result.field_changes && result.field_changes.length > 0)) && (
                                  <tr>
                                    <td colSpan={9} style={{ padding: 0 }}>
                                      <details 
                                        style={{ fontSize: 10 }}
                                        open={result.status === 'failed' || result.status === 'rolled_back'}
                                      >
                                        <summary style={{ 
                                          padding: '4px 10px', 
                                          cursor: 'pointer', 
                                          background: result.status === 'failed' ? 'rgba(239, 68, 68, 0.15)' : 
                                                      result.status === 'rolled_back' ? 'rgba(245, 158, 11, 0.15)' : 'rgba(0,0,0,0.2)'
                                        }}>
                                          📋 View Details
                                          {result.status === 'failed' && <span style={{ color: '#ef4444', marginLeft: 8, fontWeight: 600 }}>❌ FAILED</span>}
                                          {result.error_message && result.status !== 'failed' && <span style={{ color: '#ef4444', marginLeft: 8 }}>⚠ Error</span>}
                                          {result.was_rolled_back && <span style={{ color: '#f59e0b', marginLeft: 8, fontWeight: 600 }}>↩ AUTO-ROLLBACK</span>}
                                          {result.health_check_passed === false && !result.was_rolled_back && <span style={{ color: '#ef4444', marginLeft: 8 }}>🔴 Health Failed</span>}
                                        </summary>
                                        <div style={{ padding: '8px 10px', background: 'rgba(0,0,0,0.3)' }}>
                                          {/* Error Message - Show prominently for failed status */}
                                          {(result.error_message || result.status === 'failed') && (
                                            <div style={{ marginBottom: 8, padding: '8px 12px', background: 'rgba(239, 68, 68, 0.15)', borderRadius: 4, borderLeft: '4px solid #ef4444' }}>
                                              <strong style={{ color: '#ef4444', fontSize: 11 }}>❌ Error:</strong>
                                              <div style={{ marginTop: 4, color: '#fca5a5', fontFamily: 'monospace', fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                                            {result.error_message || 'Unknown error occurred'}
                                          </div>
                                        </div>
                                      )}
                                      {/* Rollback Reason */}
                                      {result.rollback_reason && (
                                        <div style={{ marginBottom: 8, padding: '8px 12px', background: 'rgba(245, 158, 11, 0.15)', borderRadius: 4, borderLeft: '4px solid #f59e0b' }}>
                                          <strong style={{ color: '#f59e0b', fontSize: 11 }}>↩ Rollback Reason:</strong>
                                          <div style={{ marginTop: 4, color: '#fcd34d', fontFamily: 'monospace', fontSize: 11, whiteSpace: 'pre-wrap' }}>
                                            {result.rollback_reason}
                                          </div>
                                        </div>
                                      )}
                                      {/* Manifest edits applied (Update Product) */}
                                      {Array.isArray(result.field_changes) && result.field_changes.length > 0 && (
                                        <div style={{ marginBottom: 8, padding: '8px 12px', background: 'rgba(139, 92, 246, 0.12)', borderRadius: 4, borderLeft: '4px solid #8b5cf6' }}>
                                          <strong style={{ fontSize: 11 }}>📝 Manifest changes{result.helm_managed ? ' (Helm-managed)' : ''}:</strong>
                                          <div style={{ marginTop: 4, fontFamily: 'monospace', fontSize: 11 }}>
                                            {result.field_changes.map((fc: any, fcIdx: number) => (
                                              <div key={fcIdx}>
                                                {fc.path}: <span style={{ color: '#fca5a5' }}>{fc.old === undefined || fc.old === null ? '(absent)' : String(fc.old)}</span> {'→'} <span style={{ color: '#86efac' }}>{fc.new === undefined || fc.new === null ? '(removed)' : String(fc.new)}</span>
                                              </div>
                                            ))}
                                          </div>
                                        </div>
                                      )}
                                      {/* Health Check Message */}
                                      {result.health_check_message && (
                                        <div style={{ marginBottom: 8, padding: '8px 12px', background: result.health_check_passed ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)', borderRadius: 4, borderLeft: `4px solid ${result.health_check_passed ? '#22c55e' : '#ef4444'}` }}>
                                          <strong style={{ fontSize: 11 }}>{result.health_check_passed ? '✅' : '❌'} Health Check:</strong>
                                          <div style={{ marginTop: 4, fontFamily: 'monospace', fontSize: 11 }}>
                                            {result.health_check_message}
                                          </div>
                                          {result.health_checked_at && (
                                            <div className="muted" style={{ fontSize: 9, marginTop: 4 }}>
                                              Checked at: {new Date(result.health_checked_at).toLocaleString()}
                                            </div>
                                          )}
                                        </div>
                                      )}
                                      {/* Pre-Patch State */}
                                      {result.pre_patch_state && (
                                        <div style={{ marginBottom: 8, padding: '6px 10px', background: 'rgba(59, 130, 246, 0.1)', borderRadius: 4 }}>
                                          <strong style={{ fontSize: 10 }}>📊 Pre-Patch State:</strong>
                                          <div style={{ marginTop: 4, fontSize: 10 }}>
                                            <span>Replicas: <strong>{result.pre_patch_state.replicas}</strong></span>
                                            <span style={{ marginLeft: 12 }}>Ready: <strong style={{ color: result.pre_patch_state.ready_replicas === result.pre_patch_state.replicas ? '#22c55e' : '#f59e0b' }}>{result.pre_patch_state.ready_replicas}</strong></span>
                                            {result.pre_patch_state.pods && <span style={{ marginLeft: 12 }}>Pods: <strong>{result.pre_patch_state.pods.length}</strong></span>}
                                          </div>
                                          {/* Pod details */}
                                          {result.pre_patch_state.pods && result.pre_patch_state.pods.length > 0 && (
                                            <div style={{ marginTop: 6, fontSize: 9 }}>
                                              {result.pre_patch_state.pods.map((pod, podIdx) => (
                                                <div key={podIdx} style={{ marginBottom: 2, padding: '2px 4px', background: 'rgba(0,0,0,0.2)', borderRadius: 2 }}>
                                                  <span style={{ color: pod.ready ? '#22c55e' : '#ef4444' }}>{pod.ready ? '●' : '○'}</span>
                                                  {' '}<span className="muted">{pod.name}</span>
                                                  {' '}<span style={{ color: pod.phase === 'Running' ? '#22c55e' : '#f59e0b' }}>[{pod.phase}]</span>
                                                  {pod.restart_count > 0 && <span style={{ color: '#f59e0b', marginLeft: 4 }}>↻{pod.restart_count}</span>}
                                                </div>
                                              ))}
                                            </div>
                                          )}
                                        </div>
                                      )}
                                      {/* Post-Patch State */}
                                      {result.post_patch_state && (
                                        <div style={{ marginBottom: 8, padding: '6px 10px', background: 'rgba(139, 92, 246, 0.1)', borderRadius: 4 }}>
                                          <strong style={{ fontSize: 10 }}>📊 Post-Patch State:</strong>
                                          <div style={{ marginTop: 4, fontSize: 10 }}>
                                            <span>Replicas: <strong>{result.post_patch_state.replicas}</strong></span>
                                            <span style={{ marginLeft: 12 }}>Ready: <strong style={{ color: result.post_patch_state.ready_replicas === result.post_patch_state.replicas ? '#22c55e' : '#ef4444' }}>{result.post_patch_state.ready_replicas}</strong></span>
                                          </div>
                                          {/* Post-patch pod details */}
                                          {result.post_patch_state.pods && result.post_patch_state.pods.length > 0 && (
                                            <div style={{ marginTop: 6, fontSize: 9 }}>
                                              {result.post_patch_state.pods.map((pod, podIdx) => (
                                                <div key={podIdx} style={{ marginBottom: 2, padding: '2px 4px', background: 'rgba(0,0,0,0.2)', borderRadius: 2 }}>
                                                  <span style={{ color: pod.ready ? '#22c55e' : '#ef4444' }}>{pod.ready ? '●' : '○'}</span>
                                                  {' '}<span className="muted">{pod.name}</span>
                                                  {' '}<span style={{ color: pod.phase === 'Running' ? '#22c55e' : '#f59e0b' }}>[{pod.phase}]</span>
                                                  {pod.restart_count > 0 && <span style={{ color: '#f59e0b', marginLeft: 4 }}>↻{pod.restart_count}</span>}
                                                </div>
                                              ))}
                                            </div>
                                          )}
                                        </div>
                                      )}
                                      {/* Execution Log */}
                                      {result.execution_log && result.execution_log.length > 0 && (
                                        <div>
                                          <strong style={{ fontSize: 10 }}>📜 Execution Log ({result.execution_log.length} entries):</strong>
                                          <div style={{ maxHeight: 200, overflow: 'auto', fontFamily: 'monospace', fontSize: 9, background: 'rgba(0,0,0,0.4)', padding: 8, borderRadius: 4, marginTop: 4 }}>
                                            {result.execution_log.map((log, logIdx) => (
                                              <div key={logIdx} style={{ 
                                                marginBottom: 3, 
                                                padding: '2px 4px',
                                                background: log.level === 'error' ? 'rgba(239, 68, 68, 0.1)' : 
                                                           log.level === 'warning' ? 'rgba(245, 158, 11, 0.1)' : 'transparent',
                                                borderRadius: 2
                                              }}>
                                                <span className="muted">{new Date(log.timestamp).toLocaleTimeString()}</span>
                                                {' '}
                                                <span style={{ 
                                                  color: log.level === 'error' ? '#ef4444' : 
                                                         log.level === 'warning' ? '#f59e0b' : 
                                                         log.level === 'info' ? '#3b82f6' : 
                                                         log.level === 'debug' ? '#6b7280' : '#9ca3af',
                                                  fontWeight: log.level === 'error' ? 600 : 400
                                                }}>
                                                  [{log.level.toUpperCase()}]
                                                </span>
                                                {' '}<span style={{ color: log.level === 'error' ? '#fca5a5' : 'inherit' }}>{log.message}</span>
                                              </div>
                                            ))}
                                          </div>
                                        </div>
                                      )}
                                      {/* No details available message */}
                                      {!result.error_message && !result.rollback_reason && !result.health_check_message && !result.execution_log && (
                                        <div className="muted" style={{ fontSize: 10 }}>
                                          No detailed logs available for this operation.
                                        </div>
                                      )}
                                    </div>
                                  </details>
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        );
                      })}
                      </tbody>
                    </table>
                  </div>
                </div>
                ) : (
                  <div className="muted" style={{ textAlign: 'center', padding: 16, fontSize: 11 }}>
                    No detailed results available yet.
                  </div>
                )}
              </div>
            )}

            {/* Multi-Backend Info */}
            {showJobDetailModal.target_platforms && showJobDetailModal.target_platforms.length > 1 && (
              <div className="card" style={{ marginBottom: 16, background: 'rgba(59, 130, 246, 0.1)', borderColor: 'rgba(59, 130, 246, 0.3)' }}>
                <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>Multi-Backend Execution</div>
                <div style={{ fontSize: 12 }}>
                  This job will execute across <strong>{showJobDetailModal.target_platforms.length}</strong> platforms.
                  Each platform's backend will handle its own resources.
                </div>
                <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {showJobDetailModal.target_platforms.map(p => (
                    <span key={p} style={{ padding: '2px 8px', background: 'rgba(59, 130, 246, 0.2)', borderRadius: 4, fontSize: 11 }}>
                      {p}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {showJobDetailModal.notes && (
              <div style={{ marginBottom: 16 }}>
                <div className="muted" style={{ fontSize: 10, marginBottom: 4 }}>Notes</div>
                <div style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{showJobDetailModal.notes}</div>
              </div>
            )}

            </div>{/* end scrollable body */}

            {/* Actions */}
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', paddingTop: 16, borderTop: '1px solid rgba(255,255,255,0.1)', flexShrink: 0 }}>
              {showJobDetailModal.status === 'pending_approval' && (
                <>
                  <button className="btn" style={{ background: '#22c55e' }} onClick={() => approveJob(showJobDetailModal.id, true)}>
                    ✓ Approve
                  </button>
                  <button className="btn" style={{ background: '#ef4444' }} onClick={() => approveJob(showJobDetailModal.id, false)}>
                    ✗ Reject
                  </button>
                </>
              )}
              {['pending_approval', 'approved'].includes(showJobDetailModal.status) && (
                <button 
                  className="btn" 
                  style={{ background: '#6366f1', opacity: actionLoading ? 0.7 : 1 }}
                  onClick={() => dryRunJob(showJobDetailModal.id)}
                  disabled={!!actionLoading}
                >
                  {actionLoading === 'dry_run' ? '⏳ Checking...' : '🔍 Dry Run'}
                </button>
              )}
              {showJobDetailModal.status === 'approved' && (
                <button
                  className="btn"
                  onClick={() => requestExecuteJob(showJobDetailModal.id)}
                  disabled={!!actionLoading || executeConfirmLoading}
                  style={{ opacity: actionLoading || executeConfirmLoading ? 0.7 : 1 }}
                >
                  {executeConfirmLoading ? '⏳ Loading...' : actionLoading === 'executing' ? '⏳ Starting...' : '▶ Execute Now'}
                </button>
              )}
              {['failed', 'completed', 'completed_with_rollbacks', 'rolled_back'].includes(showJobDetailModal.status) && (
                <>
                  {showJobDetailModal.results_summary && (showJobDetailModal.results_summary.failed > 0 || showJobDetailModal.results_summary.rolled_back > 0) && (
                    <button 
                      className="btn" 
                      style={{ background: '#f59e0b', opacity: actionLoading ? 0.7 : 1 }} 
                      onClick={() => retryJob(showJobDetailModal.id)}
                      disabled={!!actionLoading}
                    >
                      {actionLoading === 'retrying' ? '⏳ Retrying...' : `↻ Retry Failed (${(showJobDetailModal.results_summary.failed || 0) + (showJobDetailModal.results_summary.rolled_back || 0)})`}
                  </button>
                  )}
                  {showJobDetailModal.results_summary && showJobDetailModal.results_summary.success > 0 && showJobDetailModal.status !== 'rolled_back' && (
                    <button 
                      className="btn" 
                      style={{ background: '#ef4444', opacity: (actionLoading || rollbackConfirmLoading) ? 0.7 : 1 }} 
                      onClick={() => requestRollback(showJobDetailModal.id)}
                      disabled={!!actionLoading || rollbackConfirmLoading}
                    >
                      {rollbackConfirmLoading ? '⏳ Loading...' : actionLoading === 'rolling_back' ? '⏳ Rolling Back...' : '⏪ Rollback'}
                    </button>
                  )}
                </>
              )}
              {['pending_approval', 'approved', 'executing'].includes(showJobDetailModal.status) && (
                <button 
                  className="btn secondary" 
                  onClick={() => cancelJob(showJobDetailModal.id)}
                  disabled={!!actionLoading}
                  style={{ opacity: actionLoading ? 0.7 : 1, ...(showJobDetailModal.status === 'executing' ? { background: 'rgba(239, 68, 68, 0.2)', color: '#ef4444', border: '1px solid rgba(239, 68, 68, 0.4)' } : {}) }}
                >
                  {actionLoading === 'cancelling' ? '⏳ Cancelling...' : showJobDetailModal.status === 'executing' ? 'Force Cancel' : 'Cancel Job'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Dry Run Results Modal - z-index 1001 to appear above Job Details modal */}
      {showDryRunModal && dryRunReport && (
        <div 
          style={{ 
            position: 'fixed', 
            inset: 0, 
            background: 'rgba(0,0,0,0.85)', 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center', 
            zIndex: 1001 
          }}
          onClick={() => setShowDryRunModal(false)}
        >
          <div 
            className="card" 
            style={{ 
              maxWidth: 900, 
              width: '95%',
              maxHeight: '90vh', 
              overflow: 'hidden', 
              display: 'flex', 
              flexDirection: 'column', 
              zIndex: 1002,
              background: 'var(--panel)',
              boxShadow: '0 20px 60px rgba(0,0,0,.5)'
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <h2 style={{ margin: 0 }}>🔍 Dry Run Report</h2>
                <span style={{ 
                  padding: '4px 10px', 
                  background: 'linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)', 
                  color: 'white', 
                  borderRadius: 4, 
                  fontSize: 10, 
                  fontWeight: 600 
                }}>
                  NO CHANGES MADE
                </span>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button 
                  className="btn secondary" 
                  style={{ fontSize: 11, padding: '6px 12px' }}
                  onClick={() => downloadDryRunCSV(dryRunReport)}
                  title="Download as CSV"
                >
                  📥 CSV
                </button>
                <button 
                  className="btn secondary" 
                  style={{ fontSize: 11, padding: '6px 12px' }}
                  onClick={() => downloadDryRunPDF(dryRunReport)}
                  title="Download as PDF"
                >
                  📄 PDF
                </button>
                <button className="btn secondary" onClick={() => setShowDryRunModal(false)}>✕</button>
              </div>
            </div>
            
            {/* Job Info */}
            <div style={{ marginBottom: 16, padding: 12, background: 'rgba(99, 102, 241, 0.1)', borderRadius: 8 }}>
              <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', fontSize: 13 }}>
                <div><span className="muted">Job:</span> #{dryRunReport.job_id}</div>
                <div><span className="muted">Product:</span> {dryRunReport.product}</div>
                <div style={{ flex: 1 }}><span className="muted">Target:</span> <code style={{ fontSize: 11 }}>{dryRunReport.target_image || '(no image change — manifest edits only)'}</code></div>
              </div>
            </div>

            {/* Phase 1: planned manifest edits (field-edit jobs) */}
            {Array.isArray(dryRunReport.field_edits) && dryRunReport.field_edits.length > 0 && (
              <div style={{ marginBottom: 16, padding: 12, background: 'rgba(139, 92, 246, 0.1)', borderRadius: 8, border: '1px solid rgba(139,92,246,0.3)' }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>📝 Planned manifest edits ({dryRunReport.field_edits.length})</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {dryRunReport.field_edits.map((e: any, i: number) => {
                    const matchedName = dryRunMatchedContainers.length === 1
                      ? `container "${dryRunMatchedContainers[0]}"`
                      : (dryRunMatchedContainers.length > 1 ? `containers ${dryRunMatchedContainers.join(', ')}` : 'matched container')
                    const tgt = e.type === 'affinity' ? 'pod template' : (e.container ? `container "${e.container}"` : (e.target ? `${e.target} metadata` : matchedName))
                    let desc = ''
                    if (e.type === 'env') desc = e.op === 'remove' ? `remove env ${e.name}` : `set env ${e.name}=${e.value}`
                    else if (e.type === 'resource') desc = `set ${e.kind} ${e.name}=${e.value}`
                    else if (e.type === 'command' || e.type === 'args') desc = `set ${e.type} = ${Array.isArray(e.value) ? e.value.join(' ') : e.value}`
                    else if (e.type === 'label' || e.type === 'annotation') desc = e.op === 'remove' ? `remove ${e.type} ${e.key}` : `set ${e.type} ${e.key}=${e.value}`
                    else if (e.type === 'affinity') desc = `${e.op === 'remove' ? 'remove' : 'set'} affinity ${e.subkey || 'all'}`
                    else desc = JSON.stringify(e)
                    return <div key={i} style={{ fontSize: 12 }}><code style={{ fontSize: 11 }}>{desc}</code> <span className="muted">on {tgt}</span></div>
                  })}
                </div>
                <div className="muted" style={{ fontSize: 10, marginTop: 6 }}>Applied to the matched container(s) of each Ready resource in one rollout (no image change unless an image target is also set).</div>
              </div>
            )}

            {/* Phase 1: Helm-managed targets */}
            {Array.isArray(dryRunReport.helm_managed_targets) && dryRunReport.helm_managed_targets.length > 0 && (
              <div style={{ marginBottom: 16, padding: 12, background: 'rgba(245, 158, 11, 0.12)', borderRadius: 8, border: '1px solid rgba(245,158,11,0.4)' }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#f59e0b', marginBottom: 6 }}>⚠️ Helm-managed targets ({dryRunReport.helm_managed_targets.length})</div>
                <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>A direct patch is reverted by the next helm upgrade. {dryRunReport.helm_managed_ack ? 'Acknowledged for this job.' : 'Requires acknowledgment to apply.'}</div>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11 }}>
                  {dryRunReport.helm_managed_targets.map((t: any, i: number) => (
                    <li key={i}>{t.platform}/{t.namespace}/{t.kind}/{t.resource_name}{t.release ? ` (release: ${t.release})` : ''}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Summary Cards */}
            <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
              <div style={{ 
                flex: 1, 
                padding: '12px 16px', 
                background: 'rgba(34, 197, 94, 0.1)', 
                borderRadius: 8,
                borderLeft: '4px solid #22c55e'
              }}>
                <div style={{ fontSize: 24, fontWeight: 700, color: '#22c55e' }}>{dryRunReport.summary.ready}</div>
                <div style={{ fontSize: 12, color: '#9ca3af' }}>Ready</div>
              </div>
              <div style={{ 
                flex: 1, 
                padding: '12px 16px', 
                background: 'rgba(245, 158, 11, 0.1)', 
                borderRadius: 8,
                borderLeft: '4px solid #f59e0b'
              }}>
                <div style={{ fontSize: 24, fontWeight: 700, color: '#f59e0b' }}>{dryRunReport.summary.warning}</div>
                <div style={{ fontSize: 12, color: '#9ca3af' }}>Warning</div>
              </div>
              <div style={{ 
                flex: 1, 
                padding: '12px 16px', 
                background: 'rgba(239, 68, 68, 0.1)', 
                borderRadius: 8,
                borderLeft: '4px solid #ef4444'
              }}>
                <div style={{ fontSize: 24, fontWeight: 700, color: '#ef4444' }}>{dryRunReport.summary.error}</div>
                <div style={{ fontSize: 12, color: '#9ca3af' }}>Error</div>
              </div>
              <div style={{ 
                flex: 1, 
                padding: '12px 16px', 
                background: 'rgba(107, 114, 128, 0.1)', 
                borderRadius: 8,
                borderLeft: '4px solid #6b7280'
              }}>
                <div style={{ fontSize: 24, fontWeight: 700, color: '#e5e7eb' }}>{dryRunReport.summary.total}</div>
                <div style={{ fontSize: 12, color: '#9ca3af' }}>Total</div>
              </div>
            </div>
            
            {/* Platform Summary */}
            {Object.keys(dryRunReport.by_platform).length > 1 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8, color: '#9ca3af' }}>By Platform</div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {Object.entries(dryRunReport.by_platform).map(([platform, stats]) => (
                    <div key={platform} style={{ 
                      padding: '6px 12px', 
                      background: '#1f2937', 
                      borderRadius: 6,
                      fontSize: 11
                    }}>
                      <span style={{ fontWeight: 600 }}>{platform}</span>
                      <span style={{ marginLeft: 8 }}>
                        <span style={{ color: '#22c55e' }}>{stats.ready}✓</span>
                        {stats.warning > 0 && <span style={{ color: '#f59e0b', marginLeft: 4 }}>{stats.warning}⚠</span>}
                        {stats.error > 0 && <span style={{ color: '#ef4444', marginLeft: 4 }}>{stats.error}✕</span>}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            
            {/* Details Table */}
            <div style={{ flex: 1, overflow: 'auto', marginBottom: 16 }}>
              <table style={{ width: '100%', fontSize: 11 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #374151' }}>
                    <th style={{ padding: '8px 6px', textAlign: 'left' }}>Status</th>
                    <th style={{ padding: '8px 6px', textAlign: 'left' }}>Platform</th>
                    <th style={{ padding: '8px 6px', textAlign: 'left' }}>Namespace</th>
                    <th style={{ padding: '8px 6px', textAlign: 'left' }}>Resource</th>
                    <th style={{ padding: '8px 6px', textAlign: 'left' }}>Current Image</th>
                    <th style={{ padding: '8px 6px', textAlign: 'left' }}>Message</th>
                  </tr>
                </thead>
                <tbody>
                  {dryRunReport.details.map((detail, idx) => (
                    <tr key={idx} style={{ borderBottom: '1px solid #1f2937' }}>
                      <td style={{ padding: '6px' }}>
                        {detail.status === 'ready' && <span style={{ color: '#22c55e', fontWeight: 600 }}>✓ Ready</span>}
                        {detail.status === 'warning' && <span style={{ color: '#f59e0b', fontWeight: 600 }}>⚠ Warning</span>}
                        {detail.status === 'error' && <span style={{ color: '#ef4444', fontWeight: 600 }}>✕ Error</span>}
                      </td>
                      <td style={{ padding: '6px' }}>
                        <span style={{ 
                          padding: '2px 6px', 
                          background: '#7c3aed20', 
                          color: '#a78bfa', 
                          borderRadius: 4,
                          fontSize: 10
                        }}>
                          {detail.platform}
                        </span>
                      </td>
                      <td style={{ padding: '6px', color: '#9ca3af' }}>{detail.namespace}</td>
                      <td style={{ padding: '6px' }} title={detail.resource}>
                        <div style={{ wordBreak: 'break-all' }}>{detail.resource}</div>
                        <div style={{ fontSize: 9, color: '#6b7280' }}>{detail.kind}</div>
                      </td>
                      <td style={{ padding: '6px' }}>
                        <code style={{ fontSize: 9, color: '#9ca3af' }}>
                          {detail.current_image ? detail.current_image.split('/').pop() : '-'}
                        </code>
                      </td>
                      <td style={{ padding: '6px', color: '#9ca3af', maxWidth: 200 }}>
                        <div style={{ wordBreak: 'break-word' }}>{detail.message}</div>
                        {detail.containers && detail.containers.length > 0 && (
                          <div style={{ marginTop: 4, fontSize: 9 }}>
                            {detail.containers.filter(c => c.will_update).length} container(s) will update
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            
            {/* Action Buttons */}
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', paddingTop: 12, borderTop: '1px solid #374151' }}>
              <button className="btn secondary" onClick={() => setShowDryRunModal(false)}>
                Close
              </button>
              {dryRunReport.summary.error === 0 && showJobDetailModal?.status === 'approved' && (
                <button 
                  className="btn" 
                  onClick={() => {
                    setShowDryRunModal(false)
                    if (showJobDetailModal) requestExecuteJob(showJobDetailModal.id)
                  }}
                  style={{ background: '#22c55e' }}
                >
                  ▶ Proceed with Execute
                </button>
              )}
              {dryRunReport.summary.error > 0 && (
                <div style={{ 
                  padding: '8px 16px', 
                  background: 'rgba(239, 68, 68, 0.1)', 
                  borderRadius: 6,
                  color: '#ef4444',
                  fontSize: 12
                }}>
                  ⚠ Fix {dryRunReport.summary.error} error(s) before executing
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Execute Confirmation Modal */}
      {showExecuteConfirm && executeConfirmJobId && executeConfirmParams && (
        <div
          style={{
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            background: 'rgba(0,0,0,0.7)', zIndex: 1002,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
          onClick={() => setShowExecuteConfirm(false)}
        >
          <div
            style={{
              background: '#1a1a2e', borderRadius: 12, padding: 24,
              width: '100%', maxWidth: 480, border: '1px solid rgba(139, 92, 246, 0.4)',
            }}
            onClick={e => e.stopPropagation()}
          >
            <h3 style={{ margin: '0 0 16px 0', fontSize: 16 }}>Confirm Execution</h3>

            {showJobDetailModal && (
              <div style={{ marginBottom: 16, padding: 10, background: 'rgba(99,102,241,0.1)', borderRadius: 8, fontSize: 12 }}>
                <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                  <div><span className="muted">Job:</span> <strong>#{executeConfirmJobId}</strong></div>
                  <div><span className="muted">Product:</span> <strong>{showJobDetailModal.product_name}</strong></div>
                </div>
                <div style={{ marginTop: 6, wordBreak: 'break-all' }}>
                  <span className="muted">Target:</span>{' '}
                  <code style={{ fontSize: 11 }}>{showJobDetailModal.target_image}</code>
                </div>
                {showJobDetailModal.target_platforms && (
                  <div style={{ marginTop: 6 }}>
                    <span className="muted">Platforms:</span> {showJobDetailModal.target_platforms.join(', ')}
                  </div>
                )}
              </div>
            )}

            <div style={{ marginBottom: 16, padding: 12, background: 'rgba(139,92,246,0.1)', borderRadius: 8, border: '1px solid rgba(139,92,246,0.3)' }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8, color: '#a855f7' }}>
                {showJobDetailModal?.health_check_enabled ? '✓ Smart Watch Enabled' : '✗ Monitoring Off'}
              </div>
              {showJobDetailModal?.health_check_enabled && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 16px', fontSize: 12 }}>
                  <div>
                    <span className="muted">Stuck Detection</span>
                    <div style={{ fontWeight: 600 }}>{executeConfirmParams.stuck_detection}s ({Math.round(executeConfirmParams.stuck_detection / 60)} min)</div>
                  </div>
                  <div>
                    <span className="muted">Crash Tolerance</span>
                    <div style={{ fontWeight: 600 }}>{executeConfirmParams.crash_tolerance}s ({Math.round(executeConfirmParams.crash_tolerance / 60)} min)</div>
                  </div>
                  <div>
                    <span className="muted">Batch Size</span>
                    <div style={{ fontWeight: 600 }}>{executeConfirmParams.batch_size} resources</div>
                  </div>
                  <div>
                    <span className="muted">Batch Pause</span>
                    <div style={{ fontWeight: 600 }}>{executeConfirmParams.batch_pause}s</div>
                  </div>
                </div>
              )}
              <div className="muted" style={{ fontSize: 10, marginTop: 8 }}>
                Values from Admin Settings (applied at execution time)
              </div>
            </div>

            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <button className="btn secondary" onClick={() => setShowExecuteConfirm(false)}>Cancel</button>
              <button
                className="btn"
                disabled={actionLoading === 'executing'}
                style={{ background: '#22c55e' }}
                onClick={() => {
                  setShowExecuteConfirm(false)
                  executeJob(executeConfirmJobId)
                }}
              >
                {actionLoading === 'executing' ? '⏳ Starting...' : '▶ Confirm Execute'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Rollback Confirmation Modal */}
      {showRollbackConfirm && rollbackPreview && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10001 }}
          onClick={() => setShowRollbackConfirm(false)}
        >
          <div
            className="card"
            style={{ minWidth: 500, maxWidth: 700, maxHeight: '85vh', overflow: 'auto', padding: 24, background: 'var(--card-bg, #1e1e2e)', boxShadow: '0 8px 32px rgba(0,0,0,0.5)' }}
            onClick={e => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 16, color: '#ef4444' }}>⏪ Rollback Confirmation</h3>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 16, fontSize: 12 }}>
              <div>
                <span className="muted">Product</span>
                <div style={{ fontWeight: 600 }}>{rollbackPreview.product_name}</div>
              </div>
              <div>
                <span className="muted">Resources to Rollback</span>
                <div style={{ fontWeight: 600, color: '#ef4444' }}>{rollbackPreview.total}</div>
              </div>
            </div>

            <div style={{ maxHeight: 350, overflow: 'auto', borderRadius: 4, border: '1px solid rgba(239, 68, 68, 0.3)', marginBottom: 16 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <thead>
                  <tr style={{ background: 'rgba(239, 68, 68, 0.1)' }}>
                    <th style={{ padding: '6px 8px', textAlign: 'left' }}>Resource</th>
                    <th style={{ padding: '6px 8px', textAlign: 'left' }}>Current</th>
                    <th style={{ padding: '6px 8px', textAlign: 'left' }}>Rollback To</th>
                  </tr>
                </thead>
                <tbody>
                  {rollbackPreview.items.map((item, idx) => (
                    <tr key={idx} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                      <td style={{ padding: '6px 8px' }}>
                        <div style={{ fontWeight: 500 }}>{item.resource_name}</div>
                        <div className="muted" style={{ fontSize: 10 }}>{item.namespace} / {item.kind}{item.container_name ? ` / ${item.container_name}` : ''}</div>
                        {item.platform && <div className="muted" style={{ fontSize: 9 }}>{item.platform}</div>}
                      </td>
                      <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 10, color: '#ef4444' }}>
                        {item.current_image?.split(':').pop() || '-'}
                      </td>
                      <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 10, color: '#22c55e' }}>
                        {item.rollback_to?.split(':').pop() || '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div style={{ padding: '8px 12px', background: 'rgba(239, 68, 68, 0.1)', borderRadius: 6, marginBottom: 16, fontSize: 11, borderLeft: '4px solid #ef4444' }}>
              This will revert {rollbackPreview.total} resource(s) to their previous images.
            </div>

            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <button className="btn secondary" onClick={() => { setShowRollbackConfirm(false); setRollbackPreview(null) }}>Cancel</button>
              <button
                className="btn"
                style={{ background: '#ef4444' }}
                onClick={confirmRollback}
              >
                ⏪ Confirm Rollback
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


