import { useState, useEffect, useMemo, useRef } from 'react'

type Container = {
  name?: string | null
  image?: string | null
  current_version?: string | null
  latest_version?: string | null
  version_diff?: string | null
  eol_date?: string | null
}

type Resource = {
  id: number
  platform: string
  namespace: string
  resource_name: string
  product_name?: string | null
  image?: string | null
  current_version?: string | null
  latest_version?: string | null
  version_diff?: string | null
  eol_date?: string | null
  security_info?: { containers?: Container[] } | null
  _backend?: string
}

type CategoryDef = {
  name: string
  color: string
  products: string[]
}

const DEFAULT_CATEGORIES: CategoryDef[] = [
  {
    name: 'Database',
    color: '#3b82f6',
    products: ['clickhouse', 'druid', 'elasticsearch', 'etcd', 'mongodb', 'neo4j', 'pinot', 'postgresql', 'qdrant', 'redis', 'risingwave', 'typesense'],
  },
  {
    name: 'Streaming & Messaging',
    color: '#8b5cf6',
    products: ['kafka', 'strimzi', 'ksql', 'mosquitto', 'rabbitmq', 'zeebe'],
  },
  {
    name: 'Data Processing & Analytics',
    color: '#06b6d4',
    products: ['airflow', 'flink', 'spark', 'superset', 'trino', 'starburst', 'kibana', 'logstash', 'metabase', 'posthog'],
  },
  {
    name: 'Monitoring & Observability',
    color: '#f59e0b',
    products: ['elasticsearch', 'fluent-bit', 'grafana', 'prometheus', 'skywalking', 'opentelemetry', 'zipkin', 'sentry'],
  },
  {
    name: 'CI/CD & DevOps',
    color: '#10b981',
    products: ['gitlab', 'sonarqube', 'harbor', 'nexus', 'jmeter'],
  },
  {
    name: 'Container & Orchestration',
    color: '#ef4444',
    products: ['kubernetes', 'dapr'],
  },
  {
    name: 'API Gateway & Proxy',
    color: '#ec4899',
    products: ['apisix', 'nginx', 'haproxy'],
  },
  {
    name: 'Security & Identity',
    color: '#f97316',
    products: ['vault', 'openfga'],
  },
  {
    name: 'AI & ML',
    color: '#a855f7',
    products: ['ollama', 'langfuse', 'dify', 'n8n', 'zep'],
  },
  {
    name: 'Application Platforms',
    color: '#14b8a6',
    products: ['backstage', 'nextcloud', 'awx'],
  },
  {
    name: 'Real Time Video',
    color: '#e11d48',
    products: ['livekit'],
  },
  {
    name: 'Storage',
    color: '#64748b',
    products: ['minio'],
  },
]

const OTHER_CAT_NAME = 'Other / Custom'

const CNCF_LOGO_BASE = 'https://raw.githubusercontent.com/cncf/landscape/master/hosted_logos/'

const LOGO_MAP: Record<string, string> = {
  'airflow': 'apache-airflow.svg',
  'apisix': 'apache-apisix.svg',
  'backstage': 'backstage.svg',
  'clickhouse': 'click-house.svg',
  'dapr': 'dapr.svg',
  'druid': 'apache-druid.svg',
  'elasticsearch': 'elastic.svg',
  'etcd': 'etcd.svg',
  'flink': 'apache-flink.svg',
  'fluent-bit': 'fluent-bit.svg',
  'grafana': 'grafana.svg',
  'haproxy': 'ha-proxy.svg',
  'harbor': 'harbor.svg',
  'kafka': 'apache-kafka.svg',
  'kubernetes': 'kubernetes.svg',
  'logstash': 'elastic.svg',
  'kibana': 'elastic.svg',
  'minio': 'minio.svg',
  'mongodb': 'mongo-db.svg',
  'mosquitto': 'eclipse-mosquitto.svg',
  'neo4j': 'neo4j.svg',
  'nexus': 'sonatype.svg',
  'nginx': 'nginx.svg',
  'ollama': 'ollama.svg',
  'opentelemetry': 'open-telemetry.svg',
  'openfga': 'openfga.svg',
  'pinot': 'apache-pinot.svg',
  'postgresql': 'postgre-sql.svg',
  'prometheus': 'prometheus.svg',
  'qdrant': 'qdrant.svg',
  'rabbitmq': 'rabbit-mq.svg',
  'redis': 'redis.svg',
  'risingwave': 'rising-wave.svg',
  'sentry': 'sentry.svg',
  'spark': 'apache-spark.svg',
  'strimzi': 'strimzi.svg',
  'superset': 'apache-superset.svg',
  'trino': 'trino.svg',
  'vault': 'vault.svg',
  'zipkin': 'zipkin.svg',
}


const SIMPLE_ICONS_BASE = 'https://cdn.jsdelivr.net/npm/simple-icons/icons/'

const SIMPLE_ICONS_MAP: Record<string, string> = {
  'airflow': 'apacheairflow',
  'apisix': 'apacheapisix',
  'backstage': 'backstage',
  'clickhouse': 'clickhouse',
  'dapr': 'dapr',
  'druid': 'apachedruid',
  'elasticsearch': 'elasticsearch',
  'etcd': 'etcd',
  'flink': 'apacheflink',
  'fluent-bit': 'fluentbit',
  'gitlab': 'gitlab',
  'grafana': 'grafana',
  'harbor': 'harbor',
  'kafka': 'apachekafka',
  'kibana': 'kibana',
  'kubernetes': 'kubernetes',
  'logstash': 'logstash',
  'metabase': 'metabase',
  'minio': 'minio',
  'mongodb': 'mongodb',
  'mosquitto': 'eclipsemosquitto',
  'n8n': 'n8n',
  'neo4j': 'neo4j',
  'nextcloud': 'nextcloud',
  'nginx': 'nginx',
  'ollama': 'ollama',
  'opentelemetry': 'opentelemetry',
  'posthog': 'posthog',
  'postgresql': 'postgresql',
  'prometheus': 'prometheus',
  'qdrant': 'qdrant',
  'rabbitmq': 'rabbitmq',
  'redis': 'redis',
  'sentry': 'sentry',
  'sonarqube': 'sonarqube',
  'spark': 'apachespark',
  'strimzi': 'strimzi',
  'superset': 'apachesuperset',
  'trino': 'trino',
  'vault': 'vault',
  'zipkin': 'zipkin',
}

const DIRECT_LOGOS: Record<string, string> = {
  'apisix': 'https://apisix.apache.org/img/logo2.svg',
  'awx': 'https://raw.githubusercontent.com/ansible/awx-logos/master/awx/ui/client/assets/192.png',
  'dify': 'https://raw.githubusercontent.com/langgenius/dify/main/images/GitHub_README_if.png',
  'jmeter': 'https://jmeter.apache.org/images/jmeter_square.svg',
  'pinot': 'https://upload.wikimedia.org/wikipedia/commons/e/e4/Pinot_Logo.svg',
  'langfuse': 'https://github.com/langfuse.png',
  'nginx': 'https://github.com/nginx.png',
  'posthog': 'https://github.com/PostHog.png',
  'qdrant': 'https://github.com/qdrant.png',
  'risingwave': 'https://github.com/risingwavelabs.png',
  'starburst': 'https://github.com/starburstdata.png',
  'strimzi': 'https://github.com/strimzi.png',
  'zeebe': 'https://github.com/camunda.png',
}

function getLogoUrls(product: string): string[] {
  const urls: string[] = []
  const key = product.toLowerCase()
  const direct = DIRECT_LOGOS[key]
  if (direct) urls.push(direct)
  const cncf = LOGO_MAP[key]
  if (cncf) urls.push(`${CNCF_LOGO_BASE}${cncf}`)
  const si = SIMPLE_ICONS_MAP[key]
  if (si) urls.push(`${SIMPLE_ICONS_BASE}${si}.svg`)
  return urls
}

function getInitialColor(name: string): string {
  const colors = ['#3b82f6', '#8b5cf6', '#06b6d4', '#f59e0b', '#10b981', '#ef4444', '#ec4899', '#f97316', '#a855f7', '#14b8a6']
  let hash = 0
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash)
  return colors[Math.abs(hash) % colors.length]
}

type CustomProduct = {
  name: string
  alias?: string | null
  platforms?: string[]
  resourceCount?: number
  currentVersion?: string | null
  latestVersion?: string | null
  logo?: string | null
  category?: string | null
}

type ProductInfo = {
  name: string
  resourceCount: number
  platforms: string[]
  currentVersions: string[]
  latestVersion: string | null
  versionDiff: string | null
  eolDate: string | null
  hasUpdate: boolean
  isCustom?: boolean
  backends: string[]
  isUnreachable?: boolean
}

const LOGO_CACHE_VERSION = '3'

const CATEGORY_COLORS = [
  '#3b82f6', '#8b5cf6', '#06b6d4', '#f59e0b', '#10b981',
  '#ef4444', '#ec4899', '#f97316', '#a855f7', '#14b8a6',
  '#e11d48', '#64748b', '#0ea5e9', '#84cc16', '#d946ef',
]

type SnapshotProduct = {
  name: string
  resourceCount: number
  platforms: string[]
  currentVersions: string[]
  latestVersion: string | null
  versionDiff: string | null
  eolDate: string | null
  hasUpdate: boolean
  backends: string[]
}

type LandscapeProps = {
  resources: Resource[]
  role?: string | null
  loading?: boolean
}

export function Landscape({ resources, role, loading = false }: LandscapeProps): JSX.Element {
  const [filter, setFilter] = useState('')
  const [failedUrls, setFailedUrls] = useState<Set<string>>(() => {
    try {
      const ver = localStorage.getItem('landscape_logo_ver')
      if (ver !== LOGO_CACHE_VERSION) {
        localStorage.removeItem('landscape_failed_logos')
        localStorage.setItem('landscape_logo_ver', LOGO_CACHE_VERSION)
        return new Set()
      }
      const stored = localStorage.getItem('landscape_failed_logos')
      if (stored) return new Set(JSON.parse(stored))
    } catch { /* ignore */ }
    return new Set()
  })
  const [selectedProduct, setSelectedProduct] = useState<ProductInfo | null>(null)
  const [customLogos, setCustomLogos] = useState<Record<string, string | { url: string | null; hideName?: boolean }>>({})
  const [productAliases, setProductAliases] = useState<Record<string, string>>({})
  const [editingLogo, setEditingLogo] = useState(false)
  const [logoUrl, setLogoUrl] = useState('')
  const [logoSaving, setLogoSaving] = useState(false)
  const [hideNameChecked, setHideNameChecked] = useState(false)
  const [editingAlias, setEditingAlias] = useState(false)
  const [aliasValue, setAliasValue] = useState('')
  const [aliasSaving, setAliasSaving] = useState(false)
  const [customProducts, setCustomProducts] = useState<CustomProduct[]>([])
  const [snapshotProducts, setSnapshotProducts] = useState<SnapshotProduct[]>([])
  const [showAddProduct, setShowAddProduct] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [categories, setCategories] = useState<CategoryDef[]>(DEFAULT_CATEGORIES)
  const [hasOverrides, setHasOverrides] = useState(false)
  const [configReady, setConfigReady] = useState(false)
  const [isDirty, setIsDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  const [draggedProduct, setDraggedProduct] = useState<string | null>(null)
  const [dragSourceCat, setDragSourceCat] = useState<string | null>(null)
  const [dragOverCat, setDragOverCat] = useState<string | null>(null)
  const isDraggingRef = useRef(false)

  const [draggedCatName, setDraggedCatName] = useState<string | null>(null)
  const [catDropTarget, setCatDropTarget] = useState<string | null>(null)

  const [showNewCat, setShowNewCat] = useState(false)
  const [newCatName, setNewCatName] = useState('')
  const [newCatColor, setNewCatColor] = useState('#6b7280')

  useEffect(() => {
    if (failedUrls.size > 0) {
      localStorage.setItem('landscape_failed_logos', JSON.stringify([...failedUrls]))
    }
  }, [failedUrls])

  useEffect(() => {
    const loads = [
      fetch('/api/landscape/categories')
        .then(r => r.json())
        .then(data => {
          if (data.categories && Array.isArray(data.categories)) {
            setCategories(data.categories)
            setHasOverrides(true)
          }
        })
        .catch(() => {}),
      fetch('/api/landscape/logos')
        .then(r => r.json())
        .then(data => {
          if (data.logos && typeof data.logos === 'object') setCustomLogos(data.logos)
        })
        .catch(() => {}),
      fetch('/api/landscape/aliases')
        .then(r => r.json())
        .then(data => {
          if (data.aliases && typeof data.aliases === 'object') setProductAliases(data.aliases)
        })
        .catch(() => {}),
      fetch('/api/landscape/custom-products')
        .then(r => r.json())
        .then(data => {
          if (data.products && Array.isArray(data.products)) setCustomProducts(data.products)
        })
        .catch(() => {}),
      fetch('/api/landscape/product-snapshot')
        .then(r => r.json())
        .then(data => {
          if (data.products && Array.isArray(data.products)) setSnapshotProducts(data.products)
        })
        .catch(() => {}),
    ]
    Promise.all(loads).then(() => setConfigReady(true))
  }, [])

  function getCustomLogoUrl(product: string): string | undefined {
    const entry = customLogos[product.toLowerCase()]
    if (!entry) return undefined
    return typeof entry === 'string' ? entry : entry.url
  }

  function isNameHidden(product: string): boolean {
    const entry = customLogos[product.toLowerCase()]
    if (!entry || typeof entry === 'string') return false
    return !!entry.hideName
  }

  function getLogoUrlsWithCustom(product: string): string[] {
    const customUrl = getCustomLogoUrl(product)
    if (customUrl) return [customUrl, ...getLogoUrls(product)]
    return getLogoUrls(product)
  }

  function getDisplayName(product: string): string {
    return productAliases[product.toLowerCase()] || product
  }

  const productMap = useMemo(() => {
    const map = new Map<string, ProductInfo>()
    const isHash = (v: string) => /^[a-f0-9]{20,}$/i.test(v)

    function findMatchingContainer(r: Resource, productName: string): Container | null {
      const containers = r.security_info?.containers
      if (!Array.isArray(containers) || containers.length === 0) return null
      const match = containers.find(c => {
        const cName = (c.name || '').toLowerCase()
        return cName === productName || cName.includes(productName)
      })
      return match || null
    }

    for (const r of resources) {
      const name = (r.product_name || '').toLowerCase().trim()
      if (!name) continue
      const backend = r._backend || '__default__'

      const matched = findMatchingContainer(r, name)
      const rawLatest = matched?.latest_version || r.latest_version || null
      const rawCur = matched?.current_version || r.current_version || null
      const latestVer = rawLatest && !isHash(rawLatest) ? rawLatest : null
      const curVer = rawCur && !isHash(rawCur) ? rawCur : null
      const diff = matched?.version_diff || r.version_diff || null
      const eol = matched?.eol_date || r.eol_date || null

      const existing = map.get(name)
      if (existing) {
        existing.resourceCount++
        if (r.platform && !existing.platforms.includes(r.platform)) existing.platforms.push(r.platform)
        if (curVer && !isHash(curVer) && !existing.currentVersions.includes(curVer)) existing.currentVersions.push(curVer)
        if (matched && latestVer) existing.latestVersion = latestVer
        else if (latestVer && !existing.latestVersion) existing.latestVersion = latestVer
        if (diff && diff !== 'same' && !existing.hasUpdate) existing.hasUpdate = true
        if (matched && eol) existing.eolDate = eol
        else if (eol && !existing.eolDate) existing.eolDate = eol
        if (!existing.backends.includes(backend)) existing.backends.push(backend)
      } else {
        map.set(name, {
          name,
          resourceCount: 1,
          platforms: r.platform ? [r.platform] : [],
          currentVersions: curVer && !isHash(curVer) ? [curVer] : [],
          latestVersion: latestVer,
          versionDiff: diff,
          eolDate: eol,
          hasUpdate: !!(diff && diff !== 'same'),
          backends: [backend],
        })
      }
    }

    // Merge custom (manual) products
    for (const cp of customProducts) {
      const key = cp.name.toLowerCase()
      if (!map.has(key)) {
        map.set(key, {
          name: key,
          resourceCount: cp.resourceCount || 1,
          platforms: cp.platforms || [],
          currentVersions: cp.currentVersion ? [cp.currentVersion] : [],
          latestVersion: cp.latestVersion || null,
          versionDiff: null,
          eolDate: null,
          hasUpdate: false,
          isCustom: true,
          backends: [],
        })
      }
    }

    // Merge snapshot products not found in live data as unreachable
    for (const sp of snapshotProducts) {
      const key = (sp.name || '').toLowerCase().trim()
      if (!key || map.has(key)) continue
      map.set(key, {
        name: key,
        resourceCount: sp.resourceCount || 0,
        platforms: sp.platforms || [],
        currentVersions: sp.currentVersions || [],
        latestVersion: sp.latestVersion || null,
        versionDiff: sp.versionDiff || null,
        eolDate: sp.eolDate || null,
        hasUpdate: sp.hasUpdate || false,
        backends: sp.backends || [],
        isUnreachable: true,
      })
    }

    return map
  }, [resources, customProducts, snapshotProducts])

  // Persist live products to database snapshot for instant load next time
  useEffect(() => {
    if (resources.length === 0) return
    const liveProducts: SnapshotProduct[] = [...productMap.values()]
      .filter(p => !p.isUnreachable && !p.isCustom)
      .map(p => ({
        name: p.name,
        resourceCount: p.resourceCount,
        platforms: p.platforms,
        currentVersions: p.currentVersions,
        latestVersion: p.latestVersion,
        versionDiff: p.versionDiff,
        eolDate: p.eolDate,
        hasUpdate: p.hasUpdate,
        backends: p.backends || [],
      }))
    const token = sessionStorage.getItem('token') || ''
    if (!token) return
    fetch('/api/landscape/product-snapshot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ products: liveProducts }),
    }).catch(() => {})
  }, [productMap, resources.length])

  // Sync custom product aliases & logos into their respective states
  useEffect(() => {
    for (const cp of customProducts) {
      const key = cp.name.toLowerCase()
      if (cp.alias) setProductAliases(prev => prev[key] ? prev : { ...prev, [key]: cp.alias! })
      if (cp.logo) setCustomLogos(prev => prev[key] ? prev : { ...prev, [key]: cp.logo! })
    }
  }, [customProducts])

  const categorized = useMemo(() => {
    const assigned = new Set<string>()
    const result: { category: CategoryDef; products: ProductInfo[] }[] = []

    // Build lookup for custom product category assignments
    const customCatMap = new Map<string, string>()
    for (const cp of customProducts) {
      if (cp.category) customCatMap.set(cp.name.toLowerCase(), cp.category)
    }

    for (const cat of categories) {
      const prods: ProductInfo[] = []
      for (const pName of cat.products) {
        const info = productMap.get(pName)
        if (info) {
          prods.push(info)
          assigned.add(pName)
        }
      }
      // Also add custom products assigned to this category
      for (const [cpName, cpCat] of customCatMap) {
        if (cpCat === cat.name && !assigned.has(cpName)) {
          const info = productMap.get(cpName)
          if (info) {
            prods.push(info)
            assigned.add(cpName)
          }
        }
      }
      result.push({ category: cat, products: prods })
    }

    const uncategorized: ProductInfo[] = []
    for (const [name, info] of productMap) {
      if (!assigned.has(name)) uncategorized.push(info)
    }
    if (uncategorized.length > 0) {
      result.push({
        category: { name: OTHER_CAT_NAME, color: '#6b7280', products: [] },
        products: uncategorized.sort((a, b) => a.name.localeCompare(b.name)),
      })
    }

    return result
  }, [productMap, categories])

  const filtered = useMemo(() => {
    if (!filter) return categorized
    const q = filter.toLowerCase()
    return categorized
      .map(g => ({
        ...g,
        products: g.products.filter(p => p.name.includes(q) || getDisplayName(p.name).toLowerCase().includes(q)),
      }))
      .filter(g => g.products.length > 0)
  }, [categorized, filter, productAliases])

  // ── Drag and Drop handlers ──

  function onDragStart(e: React.DragEvent, productName: string, catName: string) {
    isDraggingRef.current = true
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', productName)
    e.dataTransfer.setData('application/x-source-cat', catName)
    setDraggedProduct(productName)
    setDragSourceCat(catName)
  }

  function onDragOver(e: React.DragEvent, catName: string) {
    if (draggedCatName) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (dragOverCat !== catName) setDragOverCat(catName)
  }

  function onDragLeave(e: React.DragEvent, catName: string) {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const { clientX: x, clientY: y } = e
    if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) {
      setDragOverCat(null)
    }
  }

  function onDrop(e: React.DragEvent, targetCatName: string) {
    e.preventDefault()
    e.stopPropagation()
    setDragOverCat(null)

    const product = draggedProduct || e.dataTransfer.getData('text/plain')
    const sourceCat = dragSourceCat || e.dataTransfer.getData('application/x-source-cat')

    if (!product || !sourceCat || sourceCat === targetCatName) {
      setDraggedProduct(null)
      setDragSourceCat(null)
      return
    }

    setCategories(prev => {
      const next = prev.map(cat => ({ ...cat, products: [...cat.products] }))

      if (sourceCat !== OTHER_CAT_NAME) {
        const src = next.find(c => c.name === sourceCat)
        if (src) src.products = src.products.filter(p => p !== product)
      }

      if (targetCatName !== OTHER_CAT_NAME) {
        const tgt = next.find(c => c.name === targetCatName)
        if (tgt && !tgt.products.includes(product)) {
          tgt.products.push(product)
        }
      }

      return next
    })

    setIsDirty(true)
    setDraggedProduct(null)
    setDragSourceCat(null)
    setTimeout(() => { isDraggingRef.current = false }, 100)
  }

  function onDragEnd() {
    setDraggedProduct(null)
    setDragSourceCat(null)
    setDragOverCat(null)
    setDraggedCatName(null)
    setCatDropTarget(null)
    setTimeout(() => { isDraggingRef.current = false }, 100)
  }

  // ── Category reorder drag handlers ──

  function onCatDragStart(e: React.DragEvent, catName: string) {
    isDraggingRef.current = true
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('application/x-cat-reorder', catName)
    setDraggedCatName(catName)
    e.stopPropagation()
  }

  function onCatDragOver(e: React.DragEvent, catName: string) {
    if (!draggedCatName || draggedCatName === catName) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (catDropTarget !== catName) setCatDropTarget(catName)
  }

  function onCatDrop(e: React.DragEvent, targetCatName: string) {
    e.preventDefault()
    e.stopPropagation()

    const sourceName = draggedCatName || e.dataTransfer.getData('application/x-cat-reorder')
    if (!sourceName || sourceName === targetCatName) {
      setDraggedCatName(null)
      setCatDropTarget(null)
      return
    }

    setCategories(prev => {
      const next = [...prev]
      const srcIdx = next.findIndex(c => c.name === sourceName)
      const tgtIdx = next.findIndex(c => c.name === targetCatName)
      if (srcIdx === -1 || tgtIdx === -1) return prev
      const [moved] = next.splice(srcIdx, 1)
      next.splice(tgtIdx, 0, moved)
      return next
    })

    setIsDirty(true)
    setDraggedCatName(null)
    setCatDropTarget(null)
    setTimeout(() => { isDraggingRef.current = false }, 100)
  }

  // ── Save / Reset / Add / Delete ──

  async function handleSave() {
    setSaving(true)
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/landscape/categories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ categories }),
      })
      if (resp.ok) {
        setIsDirty(false)
        setHasOverrides(true)
      }
    } catch { /* ignore */ }
    setSaving(false)
    isDraggingRef.current = false
  }

  async function handleReset() {
    if (!confirm('Reset layout to defaults? Your custom layout will be lost.')) return
    setSaving(true)
    try {
      const token = sessionStorage.getItem('token') || ''
      await fetch('/api/landscape/categories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ categories: null }),
      })
      setCategories(DEFAULT_CATEGORIES)
      setIsDirty(false)
      setHasOverrides(false)
    } catch { /* ignore */ }
    setSaving(false)
  }

  function handleAddCategory() {
    const name = newCatName.trim()
    if (!name) return
    if (categories.some(c => c.name.toLowerCase() === name.toLowerCase())) {
      alert('A category with this name already exists.')
      return
    }
    setCategories(prev => [...prev, { name, color: newCatColor, products: [] }])
    setIsDirty(true)
    setShowNewCat(false)
    setNewCatName('')
    setNewCatColor(CATEGORY_COLORS[categories.length % CATEGORY_COLORS.length])
  }

  function handleDeleteCategory(catName: string) {
    if (!confirm(`Delete category "${catName}"? Products will move to "${OTHER_CAT_NAME}".`)) return
    setCategories(prev => prev.filter(c => c.name !== catName))
    setIsDirty(true)
  }

  // ── Logo management ──

  function openLogoEditor() {
    setEditingLogo(true)
    const entry = customLogos[selectedProduct?.name?.toLowerCase() || '']
    setLogoUrl(typeof entry === 'string' ? entry : entry?.url || '')
    setHideNameChecked(typeof entry === 'object' ? !!entry.hideName : false)
  }

  async function handleSaveLogo(dataUri?: string | null, overrideHideName?: boolean, keepEditorOpen?: boolean) {
    if (!selectedProduct) return
    const key = selectedProduct.name.toLowerCase()
    const existingEntry = customLogos[key]
    const existingUrl = existingEntry ? (typeof existingEntry === 'string' ? existingEntry : existingEntry.url) : null
    const value = dataUri === null ? null : (dataUri || logoUrl.trim() || existingUrl || null)
    const hideName = dataUri === null ? false : (overrideHideName ?? hideNameChecked)
    setLogoSaving(true)
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/landscape/logos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ product: selectedProduct.name, logo: value, hideName }),
      })
      if (resp.ok) {
        setCustomLogos(prev => {
          const next = { ...prev }
          if (value || hideName) next[key] = { url: value, hideName }
          else delete next[key]
          return next
        })
        setFailedUrls(prev => {
          const next = new Set(prev)
          if (value) next.delete(value)
          return next
        })
        if (!keepEditorOpen) {
          setEditingLogo(false)
          setLogoUrl('')
          setHideNameChecked(false)
        }
      }
    } catch { /* ignore */ }
    setLogoSaving(false)
  }

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    if (!file.type.match(/^image\/(svg\+xml|png|jpeg|webp)$/)) {
      alert('Only SVG, PNG, JPEG, and WebP formats are supported.')
      return
    }
    if (file.size > 512 * 1024) {
      alert('File size must be under 512 KB.')
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const dataUri = reader.result as string
      handleSaveLogo(dataUri)
    }
    reader.readAsDataURL(file)
    e.target.value = ''
  }

  function handleRemoveLogo() {
    if (!confirm('Remove custom logo? The default logo will be used.')) return
    setLogoUrl('')
    setHideNameChecked(false)
    handleSaveLogo(null)
  }

  async function handleSaveAlias() {
    if (!selectedProduct) return
    setAliasSaving(true)
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/landscape/aliases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ product: selectedProduct.name, alias: aliasValue.trim() || null }),
      })
      if (resp.ok) {
        const key = selectedProduct.name.toLowerCase()
        setProductAliases(prev => {
          const next = { ...prev }
          if (aliasValue.trim()) next[key] = aliasValue.trim()
          else delete next[key]
          return next
        })
        setEditingAlias(false)
      }
    } catch { /* ignore */ }
    setAliasSaving(false)
  }

  async function handleRemoveAlias() {
    if (!selectedProduct) return
    setAliasSaving(true)
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/landscape/aliases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ product: selectedProduct.name, alias: null }),
      })
      if (resp.ok) {
        const key = selectedProduct.name.toLowerCase()
        setProductAliases(prev => { const next = { ...prev }; delete next[key]; return next })
        setAliasValue('')
        setEditingAlias(false)
      }
    } catch { /* ignore */ }
    setAliasSaving(false)
  }

  // ── Add Product Wizard state ──
  const [wizStep, setWizStep] = useState(0)
  const [wizData, setWizData] = useState<Partial<CustomProduct>>({})
  const [wizSaving, setWizSaving] = useState(false)
  const [wizPlatformInput, setWizPlatformInput] = useState('')

  function resetWizard() {
    setShowAddProduct(false)
    setWizStep(0)
    setWizData({})
    setWizPlatformInput('')
    setWizSaving(false)
  }

  function wizAddPlatform() {
    const val = wizPlatformInput.trim()
    if (!val) return
    const current = wizData.platforms || []
    if (!current.includes(val)) setWizData(prev => ({ ...prev, platforms: [...(prev.platforms || []), val] }))
    setWizPlatformInput('')
  }

  function wizRemovePlatform(p: string) {
    setWizData(prev => ({ ...prev, platforms: (prev.platforms || []).filter(x => x !== p) }))
  }

  async function wizSave() {
    const name = (wizData.name || '').trim().toLowerCase()
    if (!name) return
    setWizSaving(true)
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch('/api/landscape/custom-products', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ ...wizData, name }),
      })
      if (resp.ok) {
        const newProduct: CustomProduct = { ...wizData, name } as CustomProduct
        setCustomProducts(prev => {
          const idx = prev.findIndex(p => p.name === name)
          if (idx >= 0) { const next = [...prev]; next[idx] = newProduct; return next }
          return [...prev, newProduct]
        })
        resetWizard()
      }
    } catch { /* ignore */ }
    setWizSaving(false)
  }

  async function handleDeleteCustomProduct(productName: string) {
    if (!confirm(`Delete custom product "${productName}" from landscape?`)) return
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch(`/api/landscape/custom-products/${encodeURIComponent(productName)}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (resp.ok) {
        setCustomProducts(prev => prev.filter(p => p.name !== productName))
        setSelectedProduct(null)
      }
    } catch { /* ignore */ }
  }

  async function handleRemoveSnapshotProduct(productName: string) {
    if (!confirm(`Remove "${productName}" from landscape?`)) return
    try {
      const token = sessionStorage.getItem('token') || ''
      const resp = await fetch(`/api/landscape/product-snapshot/${encodeURIComponent(productName)}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (resp.ok) {
        setSnapshotProducts(prev => prev.filter(p => p.name !== productName))
        setSelectedProduct(null)
      }
    } catch { /* ignore */ }
  }

  const wizSteps = ['Name', 'Display', 'Platforms', 'Details', 'Category', 'Review']

  const canEdit = role === 'admin'
  const totalProducts = productMap.size
  const totalResources = resources.length
  const unreachableCount = [...productMap.values()].filter(p => p.isUnreachable).length

  if (!configReady || (loading && totalProducts === 0)) {
    return (
      <div className="loading-overlay">
        <div className="loading-spinner" />
        <div className="loading-text">Loading Product Landscape</div>
        <div className="loading-sub">Fetching data from all backend endpoints...</div>
      </div>
    )
  }

  return (
    <div className="landscape-page">
      <div className="hero">
        <h1 style={{ fontSize: 20, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          Product Landscape
          <span style={{ fontSize: 11, padding: '3px 10px', borderRadius: 999, background: 'rgba(59,130,246,0.1)', color: '#2563eb', fontWeight: 500 }}>
            {totalProducts} products
          </span>
          <span style={{ fontSize: 11, padding: '3px 10px', borderRadius: 999, background: 'rgba(99,102,241,0.1)', color: '#6366f1', fontWeight: 500 }}>
            {totalResources} resources
          </span>
          {loading && <span style={{ fontSize: 10, color: '#94a3b8', fontWeight: 400 }}>Refreshing...</span>}
          {unreachableCount > 0 && !loading && (
            <span style={{ fontSize: 11, padding: '3px 10px', borderRadius: 999, background: 'rgba(239,68,68,0.1)', color: '#ef4444', fontWeight: 500 }}>
              {unreachableCount} unreachable
            </span>
          )}
        </h1>
        {canEdit && <p style={{ fontSize: 12 }}>Drag and drop products between categories to customize your layout</p>}
      </div>

      {/* Toolbar */}
      <div style={{ margin: '12px 0', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          className="input"
          placeholder="Filter products..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
          style={{ fontSize: 12, padding: '6px 12px', maxWidth: 260 }}
        />
        {filter && (
          <button className="btn secondary" onClick={() => setFilter('')} style={{ fontSize: 11, padding: '4px 10px' }}>
            Clear
          </button>
        )}

        {canEdit && (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            {isDirty && (
              <span style={{ fontSize: 11, color: '#f59e0b', fontWeight: 500 }}>Unsaved changes</span>
            )}
            <button
              className="btn secondary"
              onClick={() => setShowAddProduct(true)}
              style={{ fontSize: 11, padding: '4px 12px' }}
            >
              + Add Product
            </button>
            <button
              className="btn secondary"
              onClick={() => setShowNewCat(true)}
              style={{ fontSize: 11, padding: '4px 12px' }}
            >
              + New Category
            </button>
            {(isDirty || hasOverrides) && (
              <button
                className="btn secondary"
                onClick={handleReset}
                disabled={saving}
                style={{ fontSize: 11, padding: '4px 12px' }}
              >
                Reset
              </button>
            )}
            {isDirty && (
              <button
                className="btn"
                onClick={handleSave}
                disabled={saving}
                style={{ fontSize: 11, padding: '4px 14px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
              >
                {saving ? 'Saving...' : 'Save Layout'}
              </button>
            )}
          </div>
        )}
      </div>

      {/* New category dialog */}
      {canEdit && showNewCat && (
        <div className="landscape-new-cat-dialog">
          <input
            className="input"
            placeholder="Category name"
            value={newCatName}
            onChange={e => setNewCatName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleAddCategory(); if (e.key === 'Escape') setShowNewCat(false) }}
            autoFocus
            style={{ fontSize: 12, padding: '6px 12px', flex: 1, minWidth: 180 }}
          />
          <input
            type="color"
            value={newCatColor}
            onChange={e => setNewCatColor(e.target.value)}
            style={{ width: 32, height: 32, border: 'none', cursor: 'pointer', borderRadius: 4 }}
          />
          <button
            className="btn"
            onClick={handleAddCategory}
            style={{ fontSize: 11, padding: '4px 14px', background: '#10b981', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
          >
            Add
          </button>
          <button
            className="btn secondary"
            onClick={() => { setShowNewCat(false); setNewCatName(''); }}
            style={{ fontSize: 11, padding: '4px 10px' }}
          >
            Cancel
          </button>
        </div>
      )}

      {/* Grid */}
      <div className="landscape-grid">
        {filtered.map(group => {
          const isWide = group.products.length >= 6
          const isOther = group.category.name === OTHER_CAT_NAME
          const isProductDropTarget = dragOverCat === group.category.name && dragSourceCat !== group.category.name && !draggedCatName
          const isCatDropTarget = catDropTarget === group.category.name && draggedCatName !== group.category.name
          const isCatDragging = draggedCatName === group.category.name
          return (
            <div
              key={group.category.name}
              className={`landscape-category${isWide ? ' wide' : ''}${isProductDropTarget ? ' drop-target' : ''}${isCatDropTarget ? ' cat-drop-target' : ''}${isCatDragging ? ' cat-dragging' : ''}`}
              style={{ borderColor: isProductDropTarget ? group.category.color : isCatDropTarget ? '#3b82f6' : `${group.category.color}40` }}
              onDragOver={e => {
                if (draggedCatName) onCatDragOver(e, group.category.name)
                else onDragOver(e, group.category.name)
              }}
              onDragLeave={e => {
                if (draggedCatName) setCatDropTarget(null)
                else onDragLeave(e, group.category.name)
              }}
              onDrop={e => {
                if (draggedCatName) onCatDrop(e, group.category.name)
                else onDrop(e, group.category.name)
              }}
            >
              <div
                className="landscape-category-header"
                style={{ background: group.category.color, cursor: (canEdit && !isOther) ? 'grab' : 'default' }}
                draggable={canEdit && !isOther}
                onDragStart={e => { if (canEdit && !isOther) onCatDragStart(e, group.category.name) }}
                onDragEnd={onDragEnd}
              >
                {canEdit && !isOther && <span className="landscape-cat-grip">⠿</span>}
                <span className="landscape-category-name">{group.category.name}</span>
                <span className="landscape-category-count">{group.products.length}</span>
                {canEdit && !isOther && (
                  <button
                    className="landscape-cat-delete"
                    onClick={e => { e.stopPropagation(); handleDeleteCategory(group.category.name) }}
                    title="Delete category"
                  >
                    ×
                  </button>
                )}
              </div>
              <div className="landscape-products" style={{ minHeight: group.products.length === 0 ? 60 : undefined }}>
                {group.products.length === 0 && (
                  <div style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#94a3b8', fontSize: 11, fontStyle: 'italic' }}>
                    Drop products here
                  </div>
                )}
                {group.products.map(prod => {
                  const urls = getLogoUrlsWithCustom(prod.name)
                  const activeUrl = urls.find(u => !failedUrls.has(u))
                  const isDragging = draggedProduct === prod.name
                  return (
                    <div
                      key={prod.name}
                      className={`landscape-card ${prod.hasUpdate ? 'has-update' : ''} ${prod.isUnreachable ? 'unreachable' : ''} ${isDragging ? 'dragging' : ''}`}
                      draggable={canEdit}
                      onDragStart={e => { if (canEdit) onDragStart(e, prod.name, group.category.name) }}
                      onDragEnd={onDragEnd}
                      onClick={() => { if (!isDraggingRef.current) setSelectedProduct(prod) }}
                      title={`${getDisplayName(prod.name)} — ${prod.resourceCount} resource(s) on ${prod.platforms.length} platform(s)${prod.isUnreachable ? '\n⚠ Backend unreachable – showing cached data' : ''}${canEdit ? '\nDrag to move between categories' : ''}`}
                    >
                      <div className="landscape-card-logo">
                        {activeUrl ? (
                          <img
                            src={activeUrl}
                            alt={getDisplayName(prod.name)}
                            draggable={false}
                            onError={() => setFailedUrls(prev => new Set([...prev, activeUrl]))}
                          />
                        ) : (
                          <div className="landscape-card-initial" style={{ background: getInitialColor(prod.name) }}>
                            {getDisplayName(prod.name).charAt(0).toUpperCase()}
                          </div>
                        )}
                      </div>
                      {!isNameHidden(prod.name) && <div className="landscape-card-name">{getDisplayName(prod.name)}</div>}
                      {prod.isUnreachable && <div className="landscape-card-badge unreachable" title="Backend unreachable – cached data">↑</div>}
                      {!prod.isUnreachable && prod.hasUpdate && <div className="landscape-card-badge update" title="Update available">↑</div>}
                      {prod.isCustom && <div className="landscape-card-badge" title="Custom product" style={{ background: '#8b5cf6', color: '#fff', fontSize: 7, padding: '1px 4px', borderRadius: 3, position: 'absolute', top: 4, left: 4 }}>M</div>}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>

      {/* Add Product Wizard */}
      {showAddProduct && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 999 }}
          onClick={resetWizard}
        >
          <div
            className="card"
            style={{ maxWidth: 520, width: '90%', padding: 24, background: '#fff', color: '#1e293b', boxShadow: '0 20px 60px rgba(0,0,0,.3)', border: '1px solid #e2e8f0' }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <h2 style={{ margin: 0, fontSize: 16 }}>Add Custom Product</h2>
              <button className="btn secondary" onClick={resetWizard} style={{ padding: '4px 10px', fontSize: 11 }}>Close</button>
            </div>

            {/* Step indicator */}
            <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
              {wizSteps.map((s, i) => (
                <div key={s} style={{ flex: 1, textAlign: 'center', fontSize: 9, padding: '4px 0', borderRadius: 4,
                  background: i === wizStep ? '#3b82f6' : i < wizStep ? '#10b981' : '#f1f5f9',
                  color: i <= wizStep ? '#fff' : '#94a3b8', fontWeight: i === wizStep ? 600 : 400, cursor: i < wizStep ? 'pointer' : 'default'
                }} onClick={() => { if (i < wizStep) setWizStep(i) }}>{s}</div>
              ))}
            </div>

            {/* Step 0: Name */}
            {wizStep === 0 && (
              <div>
                <label className="muted" style={{ fontSize: 11, marginBottom: 6, display: 'block' }}>Product Name (unique key)</label>
                <input className="input" placeholder="e.g. jira, open-webui" value={wizData.name || ''}
                  onChange={e => setWizData(prev => ({ ...prev, name: e.target.value }))}
                  onKeyDown={e => { if (e.key === 'Enter' && wizData.name?.trim()) setWizStep(1) }}
                  autoFocus style={{ fontSize: 12, padding: '8px 12px', width: '100%', boxSizing: 'border-box' }} />
              </div>
            )}

            {/* Step 1: Display Name & Logo */}
            {wizStep === 1 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div>
                  <label className="muted" style={{ fontSize: 11, marginBottom: 4, display: 'block' }}>Display Name (alias)</label>
                  <input className="input" placeholder={wizData.name || 'Display name'} value={wizData.alias || ''}
                    onChange={e => setWizData(prev => ({ ...prev, alias: e.target.value }))}
                    autoFocus style={{ fontSize: 12, padding: '8px 12px', width: '100%', boxSizing: 'border-box' }} />
                </div>
                <div>
                  <label className="muted" style={{ fontSize: 11, marginBottom: 4, display: 'block' }}>Logo URL (optional)</label>
                  <input className="input" placeholder="https://github.com/org.png" value={wizData.logo || ''}
                    onChange={e => setWizData(prev => ({ ...prev, logo: e.target.value }))}
                    style={{ fontSize: 12, padding: '8px 12px', width: '100%', boxSizing: 'border-box' }} />
                </div>
              </div>
            )}

            {/* Step 2: Platforms */}
            {wizStep === 2 && (
              <div>
                <label className="muted" style={{ fontSize: 11, marginBottom: 6, display: 'block' }}>Deployed On (platforms/servers)</label>
                <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                  <input className="input" placeholder="e.g. prod-openshift" value={wizPlatformInput}
                    onChange={e => setWizPlatformInput(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); wizAddPlatform() } }}
                    autoFocus style={{ fontSize: 12, padding: '8px 12px', flex: 1 }} />
                  <button className="btn secondary" onClick={wizAddPlatform} style={{ fontSize: 11, padding: '6px 14px' }}>Add</button>
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {(wizData.platforms || []).map(p => (
                    <span key={p} style={{ fontSize: 11, padding: '3px 10px', borderRadius: 999, background: '#f1f5f9', border: '1px solid #e2e8f0', display: 'flex', alignItems: 'center', gap: 4 }}>
                      {p}
                      <span onClick={() => wizRemovePlatform(p)} style={{ cursor: 'pointer', color: '#ef4444', fontWeight: 700, fontSize: 13 }}>&times;</span>
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Step 3: Resource count & versions */}
            {wizStep === 3 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div>
                  <label className="muted" style={{ fontSize: 11, marginBottom: 4, display: 'block' }}>Number of Resources</label>
                  <input className="input" type="number" min="1" value={wizData.resourceCount || 1}
                    onChange={e => setWizData(prev => ({ ...prev, resourceCount: parseInt(e.target.value) || 1 }))}
                    autoFocus style={{ fontSize: 12, padding: '8px 12px', width: 120 }} />
                </div>
                <div>
                  <label className="muted" style={{ fontSize: 11, marginBottom: 4, display: 'block' }}>Current Version (optional)</label>
                  <input className="input" placeholder="e.g. 9.12.0" value={wizData.currentVersion || ''}
                    onChange={e => setWizData(prev => ({ ...prev, currentVersion: e.target.value }))}
                    style={{ fontSize: 12, padding: '8px 12px', width: '100%', boxSizing: 'border-box' }} />
                </div>
                <div>
                  <label className="muted" style={{ fontSize: 11, marginBottom: 4, display: 'block' }}>Latest Version (optional)</label>
                  <input className="input" placeholder="e.g. 9.14.0" value={wizData.latestVersion || ''}
                    onChange={e => setWizData(prev => ({ ...prev, latestVersion: e.target.value }))}
                    style={{ fontSize: 12, padding: '8px 12px', width: '100%', boxSizing: 'border-box' }} />
                </div>
              </div>
            )}

            {/* Step 4: Category */}
            {wizStep === 4 && (
              <div>
                <label className="muted" style={{ fontSize: 11, marginBottom: 6, display: 'block' }}>Category</label>
                <select className="input" value={wizData.category || ''}
                  onChange={e => setWizData(prev => ({ ...prev, category: e.target.value }))}
                  style={{ fontSize: 12, padding: '8px 12px', width: '100%' }}>
                  <option value="">-- Other / Custom --</option>
                  {categories.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                </select>
              </div>
            )}

            {/* Step 5: Review */}
            {wizStep === 5 && (
              <div style={{ fontSize: 12, lineHeight: 1.8 }}>
                <div><span className="muted">Name:</span> <strong>{wizData.name}</strong></div>
                {wizData.alias && <div><span className="muted">Display:</span> {wizData.alias}</div>}
                {(wizData.platforms || []).length > 0 && <div><span className="muted">Platforms:</span> {(wizData.platforms || []).join(', ')}</div>}
                <div><span className="muted">Resources:</span> {wizData.resourceCount || 1}</div>
                {wizData.currentVersion && <div><span className="muted">Version:</span> {wizData.currentVersion}{wizData.latestVersion ? ` → ${wizData.latestVersion}` : ''}</div>}
                <div><span className="muted">Category:</span> {wizData.category || 'Other / Custom'}</div>
                {wizData.logo && <div style={{ marginTop: 8 }}><img src={wizData.logo} alt="logo" style={{ height: 32, objectFit: 'contain' }} /></div>}
              </div>
            )}

            {/* Navigation */}
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 20 }}>
              <button className="btn secondary" onClick={() => wizStep > 0 ? setWizStep(wizStep - 1) : resetWizard()}
                style={{ fontSize: 11, padding: '6px 16px' }}>
                {wizStep === 0 ? 'Cancel' : 'Back'}
              </button>
              {wizStep < 5 ? (
                <button className="btn" onClick={() => setWizStep(wizStep + 1)}
                  disabled={wizStep === 0 && !wizData.name?.trim()}
                  style={{ fontSize: 11, padding: '6px 16px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
                  Next
                </button>
              ) : (
                <button className="btn" onClick={wizSave} disabled={wizSaving}
                  style={{ fontSize: 11, padding: '6px 20px', background: '#10b981', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
                  {wizSaving ? 'Saving...' : 'Save Product'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Product detail modal */}
      {selectedProduct && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 999 }}
          onClick={() => { setSelectedProduct(null); setEditingLogo(false); setEditingAlias(false); setLogoUrl(''); setHideNameChecked(false) }}
        >
          <div
            className="card"
            style={{ maxWidth: 480, width: '90%', padding: 24, background: '#ffffff', color: '#1e293b', boxShadow: '0 20px 60px rgba(0,0,0,.3)', border: '1px solid #e2e8f0' }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                {(() => {
                  const urls = getLogoUrlsWithCustom(selectedProduct.name)
                  const activeUrl = urls.find(u => !failedUrls.has(u))
                  return activeUrl ? (
                    <img src={activeUrl} alt={getDisplayName(selectedProduct.name)} style={{ width: 36, height: 36, objectFit: 'contain' }}
                      onError={() => setFailedUrls(prev => new Set([...prev, activeUrl]))} />
                  ) : (
                    <div className="landscape-card-initial" style={{ background: getInitialColor(selectedProduct.name), width: 36, height: 36, fontSize: 16 }}>
                      {getDisplayName(selectedProduct.name).charAt(0).toUpperCase()}
                    </div>
                  )
                })()}
                <h2 style={{ margin: 0, fontSize: 18, textTransform: 'capitalize' }}>{getDisplayName(selectedProduct.name)}</h2>
                {selectedProduct.isUnreachable && (
                  <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 999, background: '#fef2f2', color: '#ef4444', fontWeight: 500, marginLeft: 8 }}>Unreachable</span>
                )}
              </div>
              <button className="btn secondary" onClick={() => { setSelectedProduct(null); setEditingLogo(false); setEditingAlias(false); setLogoUrl(''); setHideNameChecked(false) }} style={{ padding: '4px 10px', fontSize: 11 }}>Close</button>
            </div>
            {selectedProduct.isUnreachable && (
              <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 6, background: '#fef2f2', border: '1px solid #fecaca', fontSize: 11, color: '#991b1b' }}>
                Backend unreachable — showing cached data. Information may be outdated.
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px 24px', fontSize: 13 }}>
              <div>
                <div className="muted" style={{ fontSize: 10, marginBottom: 2 }}>Resources</div>
                <strong>{selectedProduct.resourceCount}</strong>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10, marginBottom: 2 }}>Platforms</div>
                <strong>{selectedProduct.platforms.length}</strong>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10, marginBottom: 2 }}>Latest Available</div>
                <div style={{ fontSize: 11, wordBreak: 'break-word' }}>{selectedProduct.latestVersion || '-'}</div>
              </div>
            </div>
            <div style={{ marginTop: 16 }}>
              <div className="muted" style={{ fontSize: 10, marginBottom: 6 }}>Deployed On</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {selectedProduct.platforms.map(p => (
                  <span key={p} style={{ fontSize: 10, padding: '2px 8px', borderRadius: 999, border: '1px solid #e2e8f0', background: '#f8fafc', color: '#334155' }}>{p}</span>
                ))}
              </div>
            </div>

            {/* Custom product actions */}
            {canEdit && selectedProduct.isCustom && (
              <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
                <button className="btn secondary" onClick={() => {
                  const cp = customProducts.find(p => p.name === selectedProduct.name)
                  if (cp) { setWizData(cp); setWizStep(0); setShowAddProduct(true); setSelectedProduct(null) }
                }} style={{ fontSize: 11, padding: '4px 12px' }}>
                  Edit Product
                </button>
                <button className="btn secondary" onClick={() => handleDeleteCustomProduct(selectedProduct.name)}
                  style={{ fontSize: 11, padding: '4px 12px', color: '#ef4444' }}>
                  Delete Product
                </button>
              </div>
            )}

            {/* Unreachable (snapshot) product removal */}
            {canEdit && selectedProduct.isUnreachable && !selectedProduct.isCustom && (
              <div style={{ marginTop: 12 }}>
                <button className="btn secondary" onClick={() => handleRemoveSnapshotProduct(selectedProduct.name)}
                  style={{ fontSize: 11, padding: '4px 12px', color: '#ef4444' }}>
                  Remove from Landscape
                </button>
              </div>
            )}

            {/* Admin tools: alias + logo */}
            {canEdit && (
              <div style={{ marginTop: 16, borderTop: '1px solid #e2e8f0', paddingTop: 12 }}>
                {/* Alias editor */}
                {!editingAlias ? (
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                    <button
                      className="btn secondary"
                      onClick={() => { setEditingAlias(true); setAliasValue(productAliases[selectedProduct.name.toLowerCase()] || '') }}
                      style={{ fontSize: 11, padding: '4px 12px' }}
                    >
                      {productAliases[selectedProduct.name.toLowerCase()] ? 'Edit Alias' : 'Set Alias'}
                    </button>
                    {productAliases[selectedProduct.name.toLowerCase()] && (
                      <span className="muted" style={{ fontSize: 10 }}>
                        Alias: <strong>{productAliases[selectedProduct.name.toLowerCase()]}</strong>
                      </span>
                    )}
                  </div>
                ) : (
                  <div style={{ marginBottom: 10 }}>
                    <div className="muted" style={{ fontSize: 10, marginBottom: 6, fontWeight: 600 }}>Display Alias</div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <input
                        className="input"
                        placeholder={selectedProduct.name}
                        value={aliasValue}
                        onChange={e => setAliasValue(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') handleSaveAlias(); if (e.key === 'Escape') setEditingAlias(false) }}
                        autoFocus
                        style={{ fontSize: 11, padding: '5px 10px', flex: 1 }}
                      />
                      <button
                        className="btn"
                        onClick={handleSaveAlias}
                        disabled={aliasSaving}
                        style={{ fontSize: 11, padding: '5px 12px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
                      >
                        {aliasSaving ? '...' : 'Save'}
                      </button>
                      {productAliases[selectedProduct.name.toLowerCase()] && (
                        <button
                          className="btn secondary"
                          onClick={handleRemoveAlias}
                          disabled={aliasSaving}
                          style={{ fontSize: 11, padding: '4px 10px', color: '#ef4444' }}
                        >
                          Remove
                        </button>
                      )}
                      <button
                        className="btn secondary"
                        onClick={() => setEditingAlias(false)}
                        style={{ fontSize: 11, padding: '4px 10px' }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
                {/* Logo editor */}
                {!editingLogo ? (
                  <button
                    className="btn secondary"
                    onClick={openLogoEditor}
                    style={{ fontSize: 11, padding: '4px 12px', display: 'flex', alignItems: 'center', gap: 6 }}
                  >
                    <span style={{ fontSize: 14 }}>🖼</span> Change Logo
                  </button>
                ) : (
                  <div className="landscape-logo-editor">
                    <div className="muted" style={{ fontSize: 10, marginBottom: 8, fontWeight: 600 }}>Update Logo</div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                      <input
                        className="input"
                        placeholder="Paste logo URL (svg, png, jpg)..."
                        value={logoUrl}
                        onChange={e => setLogoUrl(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') handleSaveLogo() }}
                        style={{ fontSize: 11, padding: '5px 10px', flex: 1 }}
                      />
                      <button
                        className="btn"
                        onClick={() => handleSaveLogo()}
                        disabled={logoSaving || !logoUrl.trim()}
                        style={{ fontSize: 11, padding: '5px 12px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer', whiteSpace: 'nowrap' }}
                      >
                        {logoSaving ? '...' : 'Save URL'}
                      </button>
                    </div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <button
                        className="btn secondary"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={logoSaving}
                        style={{ fontSize: 11, padding: '4px 12px' }}
                      >
                        Upload File
                      </button>
                      <span className="muted" style={{ fontSize: 10 }}>SVG, PNG, JPEG — max 512 KB</span>
                      <input ref={fileInputRef} type="file" accept=".svg,.png,.jpg,.jpeg,.webp,image/svg+xml,image/png,image/jpeg,image/webp" onChange={handleFileSelect} style={{ display: 'none' }} />
                    </div>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, fontSize: 11, cursor: 'pointer', userSelect: 'none' }}>
                      <input
                        type="checkbox"
                        checked={hideNameChecked}
                        onChange={e => {
                          const checked = e.target.checked
                          setHideNameChecked(checked)
                          handleSaveLogo(undefined, checked, true)
                        }}
                        disabled={logoSaving}
                        style={{ accentColor: '#3b82f6' }}
                      />
                      Logo contains product name (hide name below card)
                    </label>
                    <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                      {customLogos[selectedProduct.name.toLowerCase()] && (
                        <button
                          className="btn secondary"
                          onClick={handleRemoveLogo}
                          disabled={logoSaving}
                          style={{ fontSize: 11, padding: '4px 10px', color: '#ef4444' }}
                        >
                          Remove Custom Logo
                        </button>
                      )}
                      <button
                        className="btn secondary"
                        onClick={() => { setEditingLogo(false); setLogoUrl(''); setHideNameChecked(false) }}
                        style={{ fontSize: 11, padding: '4px 10px', marginLeft: 'auto' }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
