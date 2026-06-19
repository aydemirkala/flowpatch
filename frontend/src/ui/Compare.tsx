import React, { useState, useEffect, useMemo } from 'react'

interface Backend {
  name: string
  platform: string
  api_url: string
}

interface ResourceInfo {
  namespace: string
  name: string
  kind: string
  product_name?: string
  has_manifest?: boolean
  manifest_updated_at?: string
}

interface Difference {
  path: string
  type: string
  value1: string | null
  value2: string | null
}

interface RelatedObjectDiff {
  name: string
  type: string
  differences: Difference[]
  normalized_name?: string | null
  cluster1_name?: string
  cluster2_name?: string
}

interface ComparisonResult {
  name: string
  kind: string
  namespace1: string
  namespace2: string
  has_differences: boolean
  difference_count: number
  differences: Difference[]
  related_differences?: { [key: string]: RelatedObjectDiff[] }
  related_difference_count?: number
  yaml1?: any
  yaml2?: any
  related1?: any
  related2?: any
  manifest_updated_at1?: string
  manifest_updated_at2?: string
  error?: string
}

interface CompareResult {
  cluster1: string
  cluster2: string
  common_count: number
  only_in_cluster1: ResourceInfo[]
  only_in_cluster2: ResourceInfo[]
  comparisons: ComparisonResult[]
}

interface SavedReport {
  id: number
  name: string
  created_at: string
  created_by: string
  cluster1: string
  cluster2: string
  cluster1_namespaces: string[]
  cluster2_namespaces: string[]
  cluster1_kinds: string[]
  cluster2_kinds: string[]
  common_count: number
  only_in_cluster1_count: number
  only_in_cluster2_count: number
  with_differences_count: number
  total_diffs_count: number
  comparison_data?: CompareResult
}

const Compare: React.FC = () => {
  const [backends, setBackends] = useState<Backend[]>([])
  const [loading, setLoading] = useState(false)
  const [comparing, setComparing] = useState(false)

  // Get user role and username from sessionStorage
  const userRole = sessionStorage.getItem('role') || ''
  const currentUsername = sessionStorage.getItem('username') || ''
  const isAdmin = userRole === 'admin'
  const isAnalyst = userRole === 'analyst'
  const canCompare = isAdmin || isAnalyst  // Admin and Analyst can execute comparisons

  // Cluster 1 selections
  const [cluster1, setCluster1] = useState('')
  const [namespaces1, setNamespaces1] = useState<string[]>([])
  const [selectedNs1, setSelectedNs1] = useState<string[]>([])
  const [selectedKinds1, setSelectedKinds1] = useState<string[]>([])

  // Cluster 2 selections
  const [cluster2, setCluster2] = useState('')
  const [namespaces2, setNamespaces2] = useState<string[]>([])
  const [selectedNs2, setSelectedNs2] = useState<string[]>([])
  const [selectedKinds2, setSelectedKinds2] = useState<string[]>([])

  // Results
  const [result, setResult] = useState<CompareResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  
  // UI state
  const [expandedResources, setExpandedResources] = useState<Set<string>>(new Set())
  const [filterDiffsOnly, setFilterDiffsOnly] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [activeTab, setActiveTab] = useState<'summary' | 'differences' | 'only1' | 'only2'>('summary')
  const [selectedResource, setSelectedResource] = useState<ComparisonResult | null>(null)
  const [showYamlView, setShowYamlView] = useState(false)
  
  // Result filters (for Only in X tabs)
  const [resultSearch, setResultSearch] = useState('')
  const [resultKindFilter, setResultKindFilter] = useState<string>('')
  const [resultNsFilter, setResultNsFilter] = useState<string>('')
  
  // Differences tab filters
  const [diffKindFilter, setDiffKindFilter] = useState<string>('')
  const [diffNsFilter, setDiffNsFilter] = useState<string>('')
  
  // Related object types (not workloads) - used to determine if we should hide workload diffs
  const relatedObjectTypes = ['secret', 'configmap', 'service', 'route', 'pvc', 'hpa', 'pdb']
  const isFilteringRelatedOnly = diffKindFilter && relatedObjectTypes.includes(diffKindFilter.toLowerCase())
  
  // Saved Reports
  const [savedReports, setSavedReports] = useState<SavedReport[]>([])
  const [showSavedReports, setShowSavedReports] = useState(false)
  const [savingReport, setSavingReport] = useState(false)
  const [reportName, setReportName] = useState('')
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [viewingReport, setViewingReport] = useState<SavedReport | null>(null)
  
  // Sync before compare option
  const [syncBeforeCompare, setSyncBeforeCompare] = useState(false)

  const kinds = ['deployment', 'statefulset', 'daemonset']

  // Load backends
  useEffect(() => {
    loadBackends()
  }, [])

  // Load namespaces when cluster selected
  useEffect(() => {
    if (cluster1) loadNamespaces(cluster1, setNamespaces1)
  }, [cluster1])

  useEffect(() => {
    if (cluster2) loadNamespaces(cluster2, setNamespaces2)
  }, [cluster2])

  const loadBackends = async () => {
    try {
      const token = sessionStorage.getItem('token')
      const resp = await fetch('/api/compare/backends', {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        const data = await resp.json()
        setBackends(data)
      }
    } catch (e) {
      console.error('Failed to load backends:', e)
    }
  }

  const loadNamespaces = async (backend: string, setter: (ns: string[]) => void) => {
    try {
      setLoading(true)
      const token = sessionStorage.getItem('token')
      const resp = await fetch(`/api/compare/namespaces/${backend}`, {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        const data = await resp.json()
        setter(data)
      } else {
        const detail = await resp.text().catch(() => '')
        console.error(`Failed to load namespaces for ${backend}: ${resp.status} ${detail}`)
        setError(`Failed to load namespaces for ${backend}: ${resp.status}`)
      }
    } catch (e) {
      console.error('Failed to load namespaces:', e)
      setError(`Failed to load namespaces for ${backend}: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }

  const executeComparison = async () => {
    if (!cluster1 || !cluster2) {
      setError('Please select both clusters')
      return
    }

    setComparing(true)
    setError(null)
    setResult(null)
    setExpandedResources(new Set())
    setSelectedResource(null)

    try {
      const token = sessionStorage.getItem('token')
      const resp = await fetch('/api/compare/execute', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({
          cluster1,
          cluster1_namespaces: selectedNs1,
          cluster1_kinds: selectedKinds1,
          cluster2,
          cluster2_namespaces: selectedNs2,
          cluster2_kinds: selectedKinds2,
          sync_before_compare: syncBeforeCompare
        })
      })

      if (resp.ok) {
        const data = await resp.json()
        setResult(data)
        setActiveTab('summary')
      } else {
        const errText = await resp.text()
        setError(`Comparison failed: ${errText}`)
      }
    } catch (e: any) {
      setError(`Comparison error: ${e.message}`)
    } finally {
      setComparing(false)
    }
  }

  // Saved Reports Functions
  const loadSavedReports = async () => {
    try {
      const token = sessionStorage.getItem('token')
      const resp = await fetch('/api/compare/reports', {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        const data = await resp.json()
        setSavedReports(data)
      }
    } catch (e) {
      console.error('Failed to load saved reports:', e)
    }
  }

  const saveReport = async () => {
    if (!result || !reportName.trim()) return
    
    setSavingReport(true)
    try {
      const token = sessionStorage.getItem('token')
      const withDiffs = result.comparisons.filter(c => c.has_differences).length
      const totalDiffs = result.comparisons.reduce((sum, c) => sum + (c.difference_count || 0), 0)
      
      const resp = await fetch('/api/compare/reports', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({
          name: reportName.trim(),
          cluster1,
          cluster2,
          cluster1_namespaces: selectedNs1,
          cluster2_namespaces: selectedNs2,
          cluster1_kinds: selectedKinds1,
          cluster2_kinds: selectedKinds2,
          common_count: result.common_count,
          only_in_cluster1_count: result.only_in_cluster1.length,
          only_in_cluster2_count: result.only_in_cluster2.length,
          with_differences_count: withDiffs,
          total_diffs_count: totalDiffs,
          comparison_data: result
        })
      })
      
      if (resp.ok) {
        setShowSaveDialog(false)
        setReportName('')
        loadSavedReports()
      } else {
        const errText = await resp.text()
        alert(`Failed to save report: ${errText}`)
      }
    } catch (e: any) {
      alert(`Error saving report: ${e.message}`)
    } finally {
      setSavingReport(false)
    }
  }

  const viewReport = async (reportId: number) => {
    try {
      const token = sessionStorage.getItem('token')
      const resp = await fetch(`/api/compare/reports/${reportId}`, {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        const data = await resp.json()
        setViewingReport(data)
        if (data.comparison_data) {
          setResult(data.comparison_data)
          setActiveTab('summary')
        }
        setShowSavedReports(false)
      }
    } catch (e) {
      console.error('Failed to load report:', e)
    }
  }

  const deleteReport = async (reportId: number, reportName: string) => {
    if (!confirm(`Delete report "${reportName}"?`)) return
    
    try {
      const token = sessionStorage.getItem('token')
      const resp = await fetch(`/api/compare/reports/${reportId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` }
      })
      if (resp.ok) {
        loadSavedReports()
      }
    } catch (e) {
      console.error('Failed to delete report:', e)
    }
  }

  // Load saved reports on mount
  useEffect(() => {
    loadSavedReports()
  }, [])

  const toggleExpanded = (key: string) => {
    const newSet = new Set(expandedResources)
    if (newSet.has(key)) {
      newSet.delete(key)
    } else {
      newSet.add(key)
    }
    setExpandedResources(newSet)
  }

  // Unique namespaces and kinds from comparisons (for Differences tab filters)
  const comparisonNamespaces = useMemo(() => {
    if (!result) return []
    const nsSet = new Set<string>()
    result.comparisons.forEach(c => {
      nsSet.add(c.namespace1)
      if (c.namespace2 !== c.namespace1) nsSet.add(c.namespace2)
    })
    return Array.from(nsSet).sort()
  }, [result])

  const comparisonKinds = useMemo(() => {
    if (!result) return []
    const kindSet = new Set<string>()
    result.comparisons.forEach(c => {
      kindSet.add(c.kind)
      // Also include related object types if they have differences
      if (c.related_differences) {
        Object.keys(c.related_differences).forEach(k => kindSet.add(k))
      }
    })
    return Array.from(kindSet).sort()
  }, [result])

  // Filter and search comparisons
  const filteredComparisons = useMemo(() => {
    if (!result) return []
    let filtered = result.comparisons
    
    // filterDiffsOnly: only show items that have actual differences
    if (filterDiffsOnly) {
      filtered = filtered.filter(c => {
        // Has workload differences
        if (c.difference_count && c.difference_count > 0) return true
        // Has related object differences
        if (c.related_difference_count && c.related_difference_count > 0) return true
        // Has error
        if (c.error) return true
        return false
      })
    }
    
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      filtered = filtered.filter(c => 
        c.name.toLowerCase().includes(q) ||
        c.kind.toLowerCase().includes(q) ||
        c.namespace1.toLowerCase().includes(q) ||
        c.namespace2.toLowerCase().includes(q)
      )
    }
    
    // Kind filter
    if (diffKindFilter) {
      const kindLower = diffKindFilter.toLowerCase()
      // Check if filtering by related object type (not workload)
      const isRelatedType = relatedObjectTypes.includes(kindLower)
      
      filtered = filtered.filter(c => {
        // Match main workload kind
        if (c.kind.toLowerCase() === kindLower) return true
        // Or match if has related differences of this kind
        if (isRelatedType && c.related_differences) {
          // Check if this related type exists in related_differences
          if (c.related_differences[diffKindFilter] || c.related_differences[kindLower]) {
            return true
          }
        }
        return false
      })
    }
    
    // Namespace filter
    if (diffNsFilter) {
      filtered = filtered.filter(c => 
        c.namespace1 === diffNsFilter || c.namespace2 === diffNsFilter
      )
    }
    
    return filtered
  }, [result, filterDiffsOnly, searchQuery, diffKindFilter, diffNsFilter, relatedObjectTypes])

  // Statistics (including Related Objects)
  const stats = useMemo(() => {
    if (!result) return null
    const withDiffs = result.comparisons.filter(c => c.has_differences).length
    const identical = result.comparisons.filter(c => !c.has_differences && !c.error).length
    const errors = result.comparisons.filter(c => c.error).length
    const totalDiffs = result.comparisons.reduce((sum, c) => sum + (c.difference_count || 0), 0)
    
    // Count Related Objects that exist only in one cluster
    // Note: 'matched_normalized' means the objects match after removing random suffixes,
    // so they should NOT be counted as "only in X"
    let relatedOnlyIn1 = 0
    let relatedOnlyIn2 = 0
    
    for (const comp of result.comparisons) {
      if (comp.related_differences) {
        for (const [, objs] of Object.entries(comp.related_differences)) {
          for (const obj of objs as any[]) {
            if (obj.type === 'missing_in_cluster2') {
              relatedOnlyIn1++ // Exists in cluster1 but not in cluster2
            } else if (obj.type === 'missing_in_cluster1') {
              relatedOnlyIn2++ // Exists in cluster2 but not in cluster1
            }
            // matched_normalized: exists in both (with different random suffix), don't count
          }
        }
      }
    }
    
    return {
      total: result.common_count,
      withDiffs,
      identical,
      errors,
      onlyIn1: result.only_in_cluster1.length,
      onlyIn2: result.only_in_cluster2.length,
      relatedOnlyIn1,
      relatedOnlyIn2,
      totalDiffs
    }
  }, [result])

  // Related Objects unique to each cluster (for display in "Only in X" tabs)
  const relatedObjectsOnlyIn1 = useMemo(() => {
    if (!result) return []
    const items: { kind: string; name: string; workload: string; namespace: string }[] = []
    for (const comp of result.comparisons) {
      if (comp.related_differences) {
        for (const [kind, objs] of Object.entries(comp.related_differences)) {
          for (const obj of objs as any[]) {
            if (obj.type === 'missing_in_cluster2') {
              items.push({
                kind,
                name: obj.name,
                workload: comp.name,
                namespace: comp.namespace1
              })
            }
          }
        }
      }
    }
    return items
  }, [result])

  const relatedObjectsOnlyIn2 = useMemo(() => {
    if (!result) return []
    const items: { kind: string; name: string; workload: string; namespace: string }[] = []
    for (const comp of result.comparisons) {
      if (comp.related_differences) {
        for (const [kind, objs] of Object.entries(comp.related_differences)) {
          for (const obj of objs as any[]) {
            if (obj.type === 'missing_in_cluster1') {
              items.push({
                kind,
                name: obj.name,
                workload: comp.name,
                namespace: comp.namespace2
              })
            }
          }
        }
      }
    }
    return items
  }, [result])

  // Unique namespaces for filter dropdown
  const uniqueNamespaces = useMemo(() => {
    if (!result) return []
    const nsSet = new Set<string>()
    result.only_in_cluster1.forEach(r => nsSet.add(r.namespace))
    result.only_in_cluster2.forEach(r => nsSet.add(r.namespace))
    relatedObjectsOnlyIn1.forEach(r => nsSet.add(r.namespace))
    relatedObjectsOnlyIn2.forEach(r => nsSet.add(r.namespace))
    return Array.from(nsSet).sort()
  }, [result, relatedObjectsOnlyIn1, relatedObjectsOnlyIn2])

  // Unique kinds for filter dropdown
  const uniqueKinds = useMemo(() => {
    if (!result) return []
    const kindSet = new Set<string>()
    result.only_in_cluster1.forEach(r => kindSet.add(r.kind))
    result.only_in_cluster2.forEach(r => kindSet.add(r.kind))
    relatedObjectsOnlyIn1.forEach(r => kindSet.add(r.kind))
    relatedObjectsOnlyIn2.forEach(r => kindSet.add(r.kind))
    return Array.from(kindSet).sort()
  }, [result, relatedObjectsOnlyIn1, relatedObjectsOnlyIn2])

  // Filtered workloads for Cluster 1
  const filteredWorkloads1 = useMemo(() => {
    if (!result) return []
    return result.only_in_cluster1.filter(r => {
      const matchSearch = !resultSearch || 
        r.name.toLowerCase().includes(resultSearch.toLowerCase()) ||
        r.namespace.toLowerCase().includes(resultSearch.toLowerCase()) ||
        (r.product_name && r.product_name.toLowerCase().includes(resultSearch.toLowerCase()))
      const matchKind = !resultKindFilter || r.kind.toLowerCase() === resultKindFilter.toLowerCase()
      const matchNs = !resultNsFilter || r.namespace === resultNsFilter
      return matchSearch && matchKind && matchNs
    })
  }, [result, resultSearch, resultKindFilter, resultNsFilter])

  // Filtered workloads for Cluster 2
  const filteredWorkloads2 = useMemo(() => {
    if (!result) return []
    return result.only_in_cluster2.filter(r => {
      const matchSearch = !resultSearch || 
        r.name.toLowerCase().includes(resultSearch.toLowerCase()) ||
        r.namespace.toLowerCase().includes(resultSearch.toLowerCase()) ||
        (r.product_name && r.product_name.toLowerCase().includes(resultSearch.toLowerCase()))
      const matchKind = !resultKindFilter || r.kind.toLowerCase() === resultKindFilter.toLowerCase()
      const matchNs = !resultNsFilter || r.namespace === resultNsFilter
      return matchSearch && matchKind && matchNs
    })
  }, [result, resultSearch, resultKindFilter, resultNsFilter])

  // Filtered related objects for Cluster 1
  const filteredRelated1 = useMemo(() => {
    return relatedObjectsOnlyIn1.filter(r => {
      const matchSearch = !resultSearch || 
        r.name.toLowerCase().includes(resultSearch.toLowerCase()) ||
        r.namespace.toLowerCase().includes(resultSearch.toLowerCase()) ||
        r.workload.toLowerCase().includes(resultSearch.toLowerCase())
      const matchKind = !resultKindFilter || r.kind.toLowerCase() === resultKindFilter.toLowerCase()
      const matchNs = !resultNsFilter || r.namespace === resultNsFilter
      return matchSearch && matchKind && matchNs
    })
  }, [relatedObjectsOnlyIn1, resultSearch, resultKindFilter, resultNsFilter])

  // Filtered related objects for Cluster 2
  const filteredRelated2 = useMemo(() => {
    return relatedObjectsOnlyIn2.filter(r => {
      const matchSearch = !resultSearch || 
        r.name.toLowerCase().includes(resultSearch.toLowerCase()) ||
        r.namespace.toLowerCase().includes(resultSearch.toLowerCase()) ||
        r.workload.toLowerCase().includes(resultSearch.toLowerCase())
      const matchKind = !resultKindFilter || r.kind.toLowerCase() === resultKindFilter.toLowerCase()
      const matchNs = !resultNsFilter || r.namespace === resultNsFilter
      return matchSearch && matchKind && matchNs
    })
  }, [relatedObjectsOnlyIn2, resultSearch, resultKindFilter, resultNsFilter])

  // Export to CSV
  const exportToCsv = () => {
    if (!result) return

    const rows: string[][] = [
      ['Resource Name', 'Kind', 'Product', 'Namespace (Cluster 1)', 'Namespace (Cluster 2)', 'Status', 'Difference Count', 'Differences']
    ]

    for (const r of result.only_in_cluster1) {
      rows.push([r.name, r.kind, r.product_name || '', r.namespace, '-', 'Only in Cluster 1', '0', ''])
    }

    for (const r of result.only_in_cluster2) {
      rows.push([r.name, r.kind, r.product_name || '', '-', r.namespace, 'Only in Cluster 2', '0', ''])
    }

    for (const c of result.comparisons) {
      const status = c.error ? 'Error' : c.has_differences ? 'Different' : 'Same'
      const diffSummary = c.differences?.map(d => `${d.path}: ${d.type}`).join('; ') || ''
      rows.push([c.name, c.kind, '', c.namespace1, c.namespace2, status, String(c.difference_count || 0), diffSummary])
    }

    const csvContent = rows.map(row => 
      row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(',')
    ).join('\n')

    const blob = new Blob(['\ufeff' + csvContent], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `comparison_${cluster1}_vs_${cluster2}_${new Date().toISOString().split('T')[0]}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  // Export detailed report
  const exportDetailedReport = () => {
    if (!result) return

    let content = `╔══════════════════════════════════════════════════════════════════╗
║              CLUSTER COMPARISON REPORT                           ║
╚══════════════════════════════════════════════════════════════════╝

Generated: ${new Date().toLocaleString()}

┌─────────────────────────────────────────────────────────────────┐
│ CLUSTERS                                                         │
├─────────────────────────────────────────────────────────────────┤
│ Cluster 1: ${result.cluster1.padEnd(50)}│
│ Cluster 2: ${result.cluster2.padEnd(50)}│
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ SUMMARY                                                          │
├─────────────────────────────────────────────────────────────────┤
│ Common resources:        ${String(result.common_count).padEnd(37)}│
│ Only in Cluster 1:       ${String(result.only_in_cluster1.length).padEnd(37)}│
│ Only in Cluster 2:       ${String(result.only_in_cluster2.length).padEnd(37)}│
│ With differences:        ${String(result.comparisons.filter(c => c.has_differences).length).padEnd(37)}│
│ Identical:               ${String(result.comparisons.filter(c => !c.has_differences && !c.error).length).padEnd(37)}│
└─────────────────────────────────────────────────────────────────┘

`

    if (result.only_in_cluster1.length > 0) {
      content += `\n▶ RESOURCES ONLY IN ${result.cluster1.toUpperCase()}\n${'─'.repeat(65)}\n`
      for (const r of result.only_in_cluster1) {
        content += `  • [${r.kind.toUpperCase()}] ${r.namespace}/${r.name}\n`
      }
    }

    if (result.only_in_cluster2.length > 0) {
      content += `\n▶ RESOURCES ONLY IN ${result.cluster2.toUpperCase()}\n${'─'.repeat(65)}\n`
      for (const r of result.only_in_cluster2) {
        content += `  • [${r.kind.toUpperCase()}] ${r.namespace}/${r.name}\n`
      }
    }

    const withDiffs = result.comparisons.filter(comp => comp.has_differences)
    if (withDiffs.length > 0) {
      content += `\n▶ DETAILED DIFFERENCES\n${'─'.repeat(65)}\n`
      
      for (const c of withDiffs) {
        content += `\n┌${'─'.repeat(63)}┐\n`
        content += `│ ${c.kind.toUpperCase()}: ${c.name}`.padEnd(64) + `│\n`
        content += `├${'─'.repeat(63)}┤\n`
        content += `│ Namespace in ${result.cluster1}: ${c.namespace1}`.padEnd(64) + `│\n`
        content += `│ Namespace in ${result.cluster2}: ${c.namespace2}`.padEnd(64) + `│\n`
        content += `│ Total differences: ${c.difference_count}`.padEnd(64) + `│\n`
        content += `└${'─'.repeat(63)}┘\n`
        
        if (c.differences && c.differences.length > 0) {
          content += `\n  Workload Differences:\n`
          for (const d of c.differences) {
            content += `  ├─ ${d.path}\n`
            content += `  │  ${result.cluster1}: ${d.value1 || '(not set)'}\n`
            content += `  │  ${result.cluster2}: ${d.value2 || '(not set)'}\n`
          }
        }
        
        if (c.related_differences) {
          for (const [relType, objs] of Object.entries(c.related_differences)) {
            if (objs && objs.length > 0) {
              content += `\n  Related ${relType.toUpperCase()} Differences:\n`
              for (const obj of objs) {
                content += `  ├─ ${obj.name} (${obj.type})\n`
              }
            }
          }
        }
        content += '\n'
      }
    }

    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `comparison_report_${cluster1}_vs_${cluster2}_${new Date().toISOString().split('T')[0]}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  // Expand/Collapse all
  const expandAll = () => {
    const keys = new Set(filteredComparisons.map(c => `${c.kind}-${c.name}`))
    setExpandedResources(keys)
  }

  const collapseAll = () => {
    setExpandedResources(new Set())
  }

  // Multi-select component
  const MultiSelect: React.FC<{
    label: string
    options: string[]
    selected: string[]
    onChange: (val: string[]) => void
    placeholder?: string
    icon?: string
  }> = ({ label, options, selected, onChange, placeholder, icon }) => {
    const [open, setOpen] = useState(false)
    const [filter, setFilter] = useState('')

    const filteredOptions = filter 
      ? options.filter(o => o.toLowerCase().includes(filter.toLowerCase()))
      : options

    return (
      <div style={{ position: 'relative', marginBottom: 16 }}>
        <label style={{ 
          display: 'flex', 
          alignItems: 'center', 
          gap: 6, 
          marginBottom: 6, 
          fontSize: 12, 
          color: 'var(--muted)',
          fontWeight: 500 
        }}>
          {icon && <span>{icon}</span>}
          {label}
        </label>
        <div
          onClick={() => setOpen(!open)}
          style={{
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '10px 14px',
            cursor: 'pointer',
            background: 'var(--card)',
            minHeight: 42,
            display: 'flex',
            flexWrap: 'wrap',
            gap: 6,
            transition: 'border-color 0.2s, box-shadow 0.2s',
            ...(open && { borderColor: 'var(--accent)', boxShadow: '0 0 0 3px rgba(99,102,241,0.1)' })
          }}
        >
          {selected.length === 0 ? (
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>{placeholder || 'All'}</span>
          ) : (
            selected.map(s => (
              <span key={s} style={{
                background: 'linear-gradient(135deg, var(--accent), #7c3aed)',
                color: 'white',
                padding: '3px 10px',
                borderRadius: 6,
                fontSize: 11,
                fontWeight: 500,
                display: 'flex',
                alignItems: 'center',
                gap: 6
              }}>
                {s}
                <span
                  onClick={(e) => { e.stopPropagation(); onChange(selected.filter(x => x !== s)) }}
                  style={{ cursor: 'pointer', opacity: 0.8, fontSize: 14 }}
                >×</span>
              </span>
            ))
          )}
        </div>
        {open && (
          <div style={{
            position: 'absolute',
            top: 'calc(100% + 4px)',
            left: 0,
            right: 0,
            background: 'var(--card)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            maxHeight: 250,
            overflow: 'hidden',
            zIndex: 100,
            boxShadow: '0 8px 24px rgba(0,0,0,0.3)'
          }}>
            {options.length > 5 && (
              <div style={{ padding: 8, borderBottom: '1px solid var(--border)' }}>
                <input
                  type="text"
                  placeholder="Search..."
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    border: '1px solid var(--border)',
                    borderRadius: 6,
                    background: 'var(--bg)',
                    color: 'inherit',
                    fontSize: 12
                  }}
                />
              </div>
            )}
            <div style={{ maxHeight: 200, overflow: 'auto' }}>
              {filteredOptions.length === 0 ? (
                <div style={{ padding: 12, color: 'var(--muted)', fontSize: 12, textAlign: 'center' }}>
                  No options found
                </div>
              ) : (
                filteredOptions.map(opt => (
                  <div
                    key={opt}
                    onClick={(e) => {
                      e.stopPropagation()
                      if (selected.includes(opt)) {
                        onChange(selected.filter(x => x !== opt))
                      } else {
                        onChange([...selected, opt])
                      }
                    }}
                    style={{
                      padding: '10px 14px',
                      cursor: 'pointer',
                      background: selected.includes(opt) ? 'rgba(99,102,241,0.15)' : 'transparent',
                      color: selected.includes(opt) ? 'var(--accent)' : 'inherit',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 10,
                      fontSize: 13,
                      transition: 'background 0.15s'
                    }}
                    onMouseEnter={(e) => {
                      if (!selected.includes(opt)) e.currentTarget.style.background = 'rgba(255,255,255,0.05)'
                    }}
                    onMouseLeave={(e) => {
                      if (!selected.includes(opt)) e.currentTarget.style.background = 'transparent'
                    }}
                  >
                    <span style={{
                      width: 18,
                      height: 18,
                      borderRadius: 4,
                      border: selected.includes(opt) ? 'none' : '2px solid var(--border)',
                      background: selected.includes(opt) ? 'var(--accent)' : 'transparent',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: 'white',
                      fontSize: 12
                    }}>
                      {selected.includes(opt) && '✓'}
                    </span>
                    {opt}
                  </div>
                ))
              )}
            </div>
            {selected.length > 0 && (
              <div style={{ 
                padding: '8px 14px', 
                borderTop: '1px solid var(--border)',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center'
              }}>
                <span style={{ fontSize: 11, color: 'var(--muted)' }}>{selected.length} selected</span>
                <button
                  onClick={(e) => { e.stopPropagation(); onChange([]) }}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: '#ef4444',
                    cursor: 'pointer',
                    fontSize: 11,
                    padding: '4px 8px'
                  }}
                >
                  Clear all
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  // Stat card component
  const StatCard: React.FC<{
    label: string
    value: number
    icon: string
    color: string
    onClick?: () => void
  }> = ({ label, value, icon, color, onClick }) => (
    <div
      onClick={onClick}
      style={{
        background: `linear-gradient(135deg, ${color}15, ${color}08)`,
        border: `1px solid ${color}30`,
        borderRadius: 12,
        padding: 16,
        cursor: onClick ? 'pointer' : 'default',
        transition: 'transform 0.2s, box-shadow 0.2s',
        textAlign: 'center'
      }}
      onMouseEnter={(e) => {
        if (onClick) {
          e.currentTarget.style.transform = 'translateY(-2px)'
          e.currentTarget.style.boxShadow = `0 8px 24px ${color}20`
        }
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = 'translateY(0)'
        e.currentTarget.style.boxShadow = 'none'
      }}
    >
      <div style={{ fontSize: 24, marginBottom: 8 }}>{icon}</div>
      <div style={{ fontSize: 28, fontWeight: 700, color, marginBottom: 4 }}>{value}</div>
      <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
    </div>
  )

  // YAML diff viewer
  const YamlDiffViewer: React.FC<{ comp: ComparisonResult }> = ({ comp }) => {
    const formatYaml = (obj: any) => {
      try {
        return JSON.stringify(obj, null, 2)
      } catch {
        return String(obj)
      }
    }

    return (
      <div style={{ 
        position: 'fixed', 
        inset: 0, 
        background: 'rgba(0,0,0,0.8)', 
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20
      }}
      onClick={() => setShowYamlView(false)}
      >
        <div 
          style={{ 
            background: 'var(--card)', 
            borderRadius: 12, 
            width: '95%',
            maxWidth: 1400,
            height: '90vh',
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column'
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div style={{ 
            padding: '16px 24px', 
            borderBottom: '1px solid var(--border)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center'
          }}>
            <div>
              <h3 style={{ margin: 0 }}>{comp.kind}/{comp.name}</h3>
              <p className="muted" style={{ margin: '4px 0 0', fontSize: 12 }}>Full YAML Comparison</p>
            </div>
            <button 
              onClick={() => setShowYamlView(false)}
              style={{
                background: 'none',
                border: 'none',
                fontSize: 24,
                cursor: 'pointer',
                color: 'var(--muted)',
                padding: 8
              }}
            >×</button>
          </div>
          
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', flex: 1, overflow: 'hidden', minHeight: 0 }}>
            <div style={{ borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
              <div style={{ 
                padding: '12px 16px', 
                background: 'rgba(99,102,241,0.1)', 
                borderBottom: '1px solid var(--border)',
                fontWeight: 600,
                fontSize: 13,
                color: 'var(--accent)',
                flexShrink: 0
              }}>
                {result?.cluster1} ({comp.namespace1})
              </div>
              <pre style={{ 
                flex: 1, 
                overflow: 'auto', 
                padding: 16, 
                margin: 0, 
                fontSize: 11, 
                lineHeight: 1.5,
                background: 'var(--bg)',
                minHeight: 0
              }}>
                {formatYaml(comp.yaml1)}
              </pre>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
              <div style={{ 
                padding: '12px 16px', 
                background: 'rgba(245,158,11,0.1)', 
                borderBottom: '1px solid var(--border)',
                fontWeight: 600,
                fontSize: 13,
                color: '#f59e0b',
                flexShrink: 0
              }}>
                {result?.cluster2} ({comp.namespace2})
              </div>
              <pre style={{ 
                flex: 1, 
                overflow: 'auto', 
                padding: 16, 
                margin: 0, 
                fontSize: 11, 
                lineHeight: 1.5,
                background: 'var(--bg)',
                minHeight: 0
              }}>
                {formatYaml(comp.yaml2)}
              </pre>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div>
      {/* Header */}
      <div className="hero" style={{ marginBottom: 32 }}>
        <h1 style={{ 
          fontSize: 32, 
          fontWeight: 700, 
          background: 'linear-gradient(135deg, var(--accent), #a855f7)', 
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          marginBottom: 8
        }}>
          Compare Clusters
        </h1>
        <p className="muted" style={{ fontSize: 14, maxWidth: 600 }}>
          Compare Kubernetes resources between clusters. Only managed products with cached manifests are compared.
        </p>
      </div>

      {/* Selection Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 60px 1fr', gap: 16, marginBottom: 32, alignItems: 'start' }}>
        {/* Cluster 1 */}
        <div className="card" style={{ 
          borderTop: '3px solid var(--accent)',
          padding: 24
        }}>
          <div style={{ 
            display: 'flex', 
            alignItems: 'center', 
            gap: 10, 
            marginBottom: 20 
          }}>
            <div style={{
              width: 36,
              height: 36,
              borderRadius: 8,
              background: 'linear-gradient(135deg, var(--accent), #7c3aed)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 700,
              color: 'white'
            }}>1</div>
            <div>
              <h3 style={{ margin: 0, fontSize: 16 }}>Source Cluster</h3>
              <p className="muted" style={{ margin: 0, fontSize: 11 }}>Select the first cluster to compare</p>
            </div>
          </div>
          
          <div style={{ marginBottom: 16 }}>
            <label style={{ 
              display: 'flex', 
              alignItems: 'center', 
              gap: 6, 
              marginBottom: 6, 
              fontSize: 12, 
              color: 'var(--muted)',
              fontWeight: 500 
            }}>
              🖥️ Cluster *
            </label>
            <select
              className="input"
              value={cluster1}
              onChange={(e) => { setCluster1(e.target.value); setSelectedNs1([]); }}
              style={{ 
                width: '100%', 
                padding: '12px 14px',
                fontSize: 13,
                borderRadius: 8
              }}
            >
              <option value="">Select a cluster...</option>
              {backends.map(b => (
                <option key={b.name} value={b.name}>{b.name} ({b.platform})</option>
              ))}
            </select>
          </div>

          <MultiSelect
            label="Namespaces"
            icon="📁"
            options={namespaces1}
            selected={selectedNs1}
            onChange={setSelectedNs1}
            placeholder="All namespaces"
          />

          <MultiSelect
            label="Resource Types"
            icon="📦"
            options={kinds}
            selected={selectedKinds1}
            onChange={setSelectedKinds1}
            placeholder="All types (Deployment, StatefulSet, DaemonSet)"
          />
        </div>

        {/* Arrow */}
        <div style={{ 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'center',
          height: '100%',
          paddingTop: 60
        }}>
          <div style={{ 
            fontSize: 28, 
            color: 'var(--muted)',
            animation: 'pulse 2s infinite'
          }}>⟷</div>
        </div>

        {/* Cluster 2 */}
        <div className="card" style={{ 
          borderTop: '3px solid #f59e0b',
          padding: 24
        }}>
          <div style={{ 
            display: 'flex', 
            alignItems: 'center', 
            gap: 10, 
            marginBottom: 20 
          }}>
            <div style={{
              width: 36,
              height: 36,
              borderRadius: 8,
              background: 'linear-gradient(135deg, #f59e0b, #ea580c)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 700,
              color: 'white'
            }}>2</div>
            <div>
              <h3 style={{ margin: 0, fontSize: 16 }}>Target Cluster</h3>
              <p className="muted" style={{ margin: 0, fontSize: 11 }}>Select the second cluster to compare</p>
            </div>
          </div>
          
          <div style={{ marginBottom: 16 }}>
            <label style={{ 
              display: 'flex', 
              alignItems: 'center', 
              gap: 6, 
              marginBottom: 6, 
              fontSize: 12, 
              color: 'var(--muted)',
              fontWeight: 500 
            }}>
              🖥️ Cluster *
            </label>
            <select
              className="input"
              value={cluster2}
              onChange={(e) => { setCluster2(e.target.value); setSelectedNs2([]); }}
              style={{ 
                width: '100%', 
                padding: '12px 14px',
                fontSize: 13,
                borderRadius: 8
              }}
            >
              <option value="">Select a cluster...</option>
              {backends.map(b => (
                <option key={b.name} value={b.name}>{b.name} ({b.platform})</option>
              ))}
            </select>
          </div>

          <MultiSelect
            label="Namespaces"
            icon="📁"
            options={namespaces2}
            selected={selectedNs2}
            onChange={setSelectedNs2}
            placeholder="All namespaces"
          />

          <MultiSelect
            label="Resource Types"
            icon="📦"
            options={kinds}
            selected={selectedKinds2}
            onChange={setSelectedKinds2}
            placeholder="All types (Deployment, StatefulSet, DaemonSet)"
          />
        </div>
      </div>

      {/* Compare Button & Saved Reports */}
      <div style={{ textAlign: 'center', marginBottom: 32, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        {/* Sync Before Compare Checkbox - Admin only */}
        <label 
          style={{ 
            display: 'flex', 
            alignItems: 'center', 
            gap: 8, 
            cursor: isAdmin ? 'pointer' : 'not-allowed',
            padding: '10px 16px',
            borderRadius: 8,
            background: !isAdmin ? 'var(--card)' : syncBeforeCompare ? 'rgba(99, 102, 241, 0.15)' : 'var(--card)',
            border: !isAdmin ? '1px solid var(--border)' : syncBeforeCompare ? '1px solid var(--accent)' : '1px solid var(--border)',
            fontSize: 13,
            transition: 'all 0.2s',
            opacity: isAdmin ? 1 : 0.6
          }}
          title={isAdmin 
            ? "Sync selected resources from Kubernetes before comparing (includes image, twistlock, EOL, replicas)" 
            : "Admin role required to sync before compare"
          }
        >
          <input
            type="checkbox"
            checked={syncBeforeCompare}
            onChange={(e) => isAdmin && setSyncBeforeCompare(e.target.checked)}
            disabled={!isAdmin}
            style={{ width: 16, height: 16, cursor: isAdmin ? 'pointer' : 'not-allowed' }}
          />
          <span style={{ color: !isAdmin ? 'var(--muted)' : syncBeforeCompare ? 'var(--accent)' : 'var(--muted)' }}>
            🔄 Sync before compare {!isAdmin && '(Admin only)'}
          </span>
        </label>
        
        <button
          className="btn"
          onClick={executeComparison}
          disabled={!canCompare || !cluster1 || !cluster2 || comparing}
          title={!canCompare ? 'Admin or Analyst role required to execute comparisons' : undefined}
          style={{ 
            minWidth: 220, 
            fontSize: 15, 
            padding: '14px 40px',
            borderRadius: 10,
            background: !canCompare || !cluster1 || !cluster2 ? 'var(--muted)' : 'linear-gradient(135deg, var(--accent), #7c3aed)',
            boxShadow: !canCompare || !cluster1 || !cluster2 || comparing ? 'none' : '0 4px 20px rgba(99,102,241,0.4)',
            transition: 'all 0.3s',
            cursor: !canCompare ? 'not-allowed' : undefined
          }}
        >
          {comparing ? (
            <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ animation: 'spin 1s linear infinite', display: 'inline-block' }}>⟳</span>
              {syncBeforeCompare ? 'Syncing & Comparing...' : 'Comparing...'}
            </span>
          ) : (
            <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              🔍 Compare Clusters {!canCompare && '(Requires Permission)'}
            </span>
          )}
        </button>
        
        <button
          onClick={() => setShowSavedReports(true)}
          style={{
            padding: '14px 24px',
            borderRadius: 10,
            background: 'var(--card)',
            border: '1px solid var(--border)',
            color: 'var(--foreground)',
            cursor: 'pointer',
            fontSize: 14,
            display: 'flex',
            alignItems: 'center',
            gap: 8
          }}
        >
          📋 Saved Reports {savedReports.length > 0 && <span style={{ 
            background: 'var(--accent)', 
            color: 'white', 
            padding: '2px 8px', 
            borderRadius: 10, 
            fontSize: 11 
          }}>{savedReports.length}</span>}
        </button>
        
        {!cluster1 || !cluster2 ? (
          <p className="muted" style={{ fontSize: 12, width: '100%', marginTop: 10 }}>
            Select both clusters to enable comparison
          </p>
        ) : null}
      </div>

      {error && (
        <div className="card" style={{ 
          background: 'rgba(239,68,68,0.1)', 
          borderColor: '#ef4444', 
          marginBottom: 24,
          display: 'flex',
          alignItems: 'center',
          gap: 12
        }}>
          <span style={{ fontSize: 20 }}>⚠️</span>
          <p style={{ color: '#ef4444', margin: 0 }}>{error}</p>
        </div>
      )}

      {/* Results */}
      {result && stats && (
        <div style={{ animation: 'fadeIn 0.3s ease-out' }}>
          {/* Stats Dashboard */}
          <div style={{ 
            display: 'grid', 
            gridTemplateColumns: 'repeat(6, 1fr)', 
            gap: 16, 
            marginBottom: 24 
          }}>
            <StatCard 
              label="Total Common" 
              value={stats.total} 
              icon="📊" 
              color="#6366f1"
            />
            <StatCard 
              label="With Differences" 
              value={stats.withDiffs} 
              icon="⚡" 
              color="#ef4444"
              onClick={() => setActiveTab('differences')}
            />
            <StatCard 
              label="Identical" 
              value={stats.identical} 
              icon="✅" 
              color="#22c55e"
            />
            <StatCard 
              label={`Only in ${result.cluster1.replace('-backend', '')}`}
              value={stats.onlyIn1 + stats.relatedOnlyIn1} 
              icon="🔵" 
              color="#3b82f6"
              onClick={() => setActiveTab('only1')}
            />
            <StatCard 
              label={`Only in ${result.cluster2.replace('-backend', '')}`}
              value={stats.onlyIn2 + stats.relatedOnlyIn2} 
              icon="🟠" 
              color="#f59e0b"
              onClick={() => setActiveTab('only2')}
            />
            <StatCard 
              label="Total Diffs" 
              value={stats.totalDiffs} 
              icon="🔢" 
              color="#8b5cf6"
            />
          </div>

          {/* Tabs */}
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ 
              display: 'flex', 
              borderBottom: '1px solid var(--border)',
              background: 'var(--bg)'
            }}>
              {[
                { id: 'summary', label: 'Summary', icon: '📋' },
                { id: 'differences', label: `Differences (${stats.withDiffs})`, icon: '⚡' },
                { id: 'only1', label: `Only in ${result.cluster1.replace('-backend', '')} (${stats.onlyIn1 + stats.relatedOnlyIn1})`, icon: '🔵' },
                { id: 'only2', label: `Only in ${result.cluster2.replace('-backend', '')} (${stats.onlyIn2 + stats.relatedOnlyIn2})`, icon: '🟠' },
              ].map(tab => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as any)}
                  style={{
                    padding: '14px 24px',
                    border: 'none',
                    background: activeTab === tab.id ? 'var(--card)' : 'transparent',
                    color: activeTab === tab.id ? 'var(--accent)' : 'var(--muted)',
                    cursor: 'pointer',
                    fontSize: 13,
                    fontWeight: activeTab === tab.id ? 600 : 400,
                    borderBottom: activeTab === tab.id ? '2px solid var(--accent)' : '2px solid transparent',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    transition: 'all 0.2s'
                  }}
                >
                  {tab.icon} {tab.label}
                </button>
              ))}
              
              <div style={{ flex: 1 }} />
              
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0 16px' }}>
                <button className="btn secondary" onClick={exportToCsv} style={{ padding: '8px 12px', fontSize: 12 }}>
                  📊 CSV
                </button>
                <button className="btn secondary" onClick={exportDetailedReport} style={{ padding: '8px 12px', fontSize: 12 }}>
                  📄 Report
                </button>
                {canCompare && (
                  <button 
                    className="btn" 
                    onClick={() => setShowSaveDialog(true)} 
                    style={{ 
                      padding: '8px 12px', 
                      fontSize: 12,
                      background: 'linear-gradient(135deg, #10b981, #059669)'
                    }}
                  >
                    💾 Save
                  </button>
                )}
              </div>
            </div>

            {/* Tab Content */}
            <div style={{ padding: 24 }}>
              {/* Summary Tab */}
              {activeTab === 'summary' && (
                <div>
                  <div style={{ 
                    display: 'grid', 
                    gridTemplateColumns: '1fr 1fr', 
                    gap: 20 
                  }}>
                    <div style={{
                      padding: 20,
                      background: 'rgba(99,102,241,0.05)',
                      borderRadius: 12,
                      border: '1px solid rgba(99,102,241,0.2)'
                    }}>
                      <h4 style={{ margin: '0 0 12px', color: 'var(--accent)' }}>
                        🖥️ {result.cluster1}
                      </h4>
                      <div style={{ fontSize: 13 }}>
                        <div style={{ marginBottom: 6 }}>
                          <span className="muted">Resources compared:</span> <strong>{stats.total}</strong>
                        </div>
                        <div style={{ marginBottom: 6 }}>
                          <span className="muted">Unique resources:</span> <strong style={{ color: '#3b82f6' }}>{stats.onlyIn1}</strong>
                        </div>
                        <div>
                          <span className="muted">Namespaces:</span> <strong>{selectedNs1.length || 'All'}</strong>
                        </div>
                      </div>
                    </div>
                    
                    <div style={{
                      padding: 20,
                      background: 'rgba(245,158,11,0.05)',
                      borderRadius: 12,
                      border: '1px solid rgba(245,158,11,0.2)'
                    }}>
                      <h4 style={{ margin: '0 0 12px', color: '#f59e0b' }}>
                        🖥️ {result.cluster2}
                      </h4>
                      <div style={{ fontSize: 13 }}>
                        <div style={{ marginBottom: 6 }}>
                          <span className="muted">Resources compared:</span> <strong>{stats.total}</strong>
                        </div>
                        <div style={{ marginBottom: 6 }}>
                          <span className="muted">Unique resources:</span> <strong style={{ color: '#f59e0b' }}>{stats.onlyIn2}</strong>
                        </div>
                        <div>
                          <span className="muted">Namespaces:</span> <strong>{selectedNs2.length || 'All'}</strong>
                        </div>
                      </div>
                    </div>
                  </div>
                  
                  {stats.withDiffs > 0 && (
                    <div style={{ marginTop: 24 }}>
                      <h4 style={{ margin: '0 0 12px' }}>Quick Overview - Resources with Differences</h4>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                        {result.comparisons.filter(c => c.has_differences).slice(0, 20).map((c, i) => (
                          <span
                            key={i}
                            onClick={() => {
                              setActiveTab('differences')
                              setSelectedResource(c)
                              toggleExpanded(`${c.kind}-${c.name}`)
                            }}
                            style={{
                              padding: '6px 12px',
                              background: 'rgba(239,68,68,0.1)',
                              border: '1px solid rgba(239,68,68,0.3)',
                              borderRadius: 6,
                              fontSize: 12,
                              cursor: 'pointer',
                              transition: 'all 0.2s'
                            }}
                          >
                            {c.kind}/{c.name}
                            <span style={{ marginLeft: 6, color: '#ef4444', fontWeight: 600 }}>
                              {c.difference_count}
                            </span>
                          </span>
                        ))}
                        {result.comparisons.filter(c => c.has_differences).length > 20 && (
                          <span 
                            onClick={() => setActiveTab('differences')}
                            style={{
                              padding: '6px 12px',
                              background: 'var(--border)',
                              borderRadius: 6,
                              fontSize: 12,
                              cursor: 'pointer'
                            }}
                          >
                            +{result.comparisons.filter(c => c.has_differences).length - 20} more...
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Differences Tab */}
              {activeTab === 'differences' && (
                <div>
                  {/* Filter Bar */}
                  <div style={{
                    display: 'flex',
                    gap: 12,
                    padding: 16,
                    background: 'var(--card)',
                    borderRadius: 8,
                    border: '1px solid var(--border)',
                    flexWrap: 'wrap',
                    alignItems: 'center',
                    marginBottom: 16
                  }}>
                    {/* Search */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 200 }}>
                      <span style={{ fontSize: 16 }}>🔍</span>
                      <input
                        type="text"
                        placeholder="Search by name, namespace..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        style={{
                          flex: 1,
                          padding: '8px 12px',
                          background: 'var(--bg)',
                          border: '1px solid var(--border)',
                          borderRadius: 6,
                          color: 'var(--foreground)',
                          fontSize: 13
                        }}
                      />
                    </div>
                    
                    {/* Kind Filter */}
                    <select
                      value={diffKindFilter}
                      onChange={e => setDiffKindFilter(e.target.value)}
                      style={{
                        padding: '8px 12px',
                        background: 'var(--bg)',
                        border: '1px solid var(--border)',
                        borderRadius: 6,
                        color: 'var(--foreground)',
                        fontSize: 13,
                        minWidth: 150
                      }}
                    >
                      <option value="">All Types</option>
                      {comparisonKinds.map(k => (
                        <option key={k} value={k}>{k}</option>
                      ))}
                    </select>
                    
                    {/* Namespace Filter */}
                    <select
                      value={diffNsFilter}
                      onChange={e => setDiffNsFilter(e.target.value)}
                      style={{
                        padding: '8px 12px',
                        background: 'var(--bg)',
                        border: '1px solid var(--border)',
                        borderRadius: 6,
                        color: 'var(--foreground)',
                        fontSize: 13,
                        minWidth: 180
                      }}
                    >
                      <option value="">All Namespaces</option>
                      {comparisonNamespaces.map(ns => (
                        <option key={ns} value={ns}>{ns}</option>
                      ))}
                    </select>
                    
                    {/* Only Differences Checkbox */}
                    <label style={{ 
                      display: 'flex', 
                      alignItems: 'center', 
                      gap: 8, 
                      cursor: 'pointer',
                      fontSize: 13,
                      padding: '8px 12px',
                      background: filterDiffsOnly ? 'rgba(99,102,241,0.1)' : 'transparent',
                      borderRadius: 6,
                      border: '1px solid var(--border)'
                    }}>
                      <input
                        type="checkbox"
                        checked={filterDiffsOnly}
                        onChange={(e) => setFilterDiffsOnly(e.target.checked)}
                        style={{ accentColor: 'var(--accent)' }}
                      />
                      Only show differences
                    </label>
                    
                    {/* Clear Filters */}
                    {(searchQuery || diffKindFilter || diffNsFilter) && (
                      <button
                        onClick={() => {
                          setSearchQuery('')
                          setDiffKindFilter('')
                          setDiffNsFilter('')
                        }}
                        style={{
                          padding: '8px 12px',
                          background: 'rgba(239,68,68,0.1)',
                          border: '1px solid rgba(239,68,68,0.3)',
                          borderRadius: 6,
                          color: '#ef4444',
                          fontSize: 12,
                          cursor: 'pointer'
                        }}
                      >
                        ✕ Clear Filters
                      </button>
                    )}
                  </div>
                  
                  {/* Toolbar */}
                  <div style={{ 
                    display: 'flex', 
                    justifyContent: 'space-between', 
                    alignItems: 'center', 
                    marginBottom: 16
                  }}>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button 
                        onClick={expandAll}
                        style={{
                          padding: '8px 12px',
                          border: '1px solid var(--border)',
                          borderRadius: 6,
                          background: 'var(--bg)',
                          cursor: 'pointer',
                          fontSize: 12,
                          color: 'inherit'
                        }}
                      >
                        ⬇️ Expand All
                      </button>
                      <button 
                        onClick={collapseAll}
                        style={{
                          padding: '8px 12px',
                          border: '1px solid var(--border)',
                          borderRadius: 6,
                          background: 'var(--bg)',
                          cursor: 'pointer',
                          fontSize: 12,
                          color: 'inherit'
                        }}
                      >
                        ⬆️ Collapse All
                      </button>
                    </div>
                  </div>

                  {/* Results count */}
                  <div className="muted" style={{ marginBottom: 12, fontSize: 12 }}>
                    Showing {filteredComparisons.length} of {result.comparisons.length} resources
                  </div>

                  {filteredComparisons.length === 0 ? (
                    <div style={{ 
                      textAlign: 'center', 
                      padding: 48,
                      color: 'var(--muted)'
                    }}>
                      <div style={{ fontSize: 48, marginBottom: 16 }}>✨</div>
                      <p style={{ fontSize: 16, marginBottom: 8 }}>No differences found!</p>
                      <p style={{ fontSize: 13 }}>All compared resources are identical between clusters.</p>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {filteredComparisons.map((comp, idx) => {
                        const key = `${comp.kind}-${comp.name}`
                        const isExpanded = expandedResources.has(key)
                        const relatedDiffCount = comp.related_differences 
                          ? Object.values(comp.related_differences).reduce((sum, arr) => sum + arr.length, 0)
                          : 0

                        return (
                          <div
                            key={idx}
                            style={{
                              border: '1px solid var(--border)',
                              borderRadius: 10,
                              overflow: 'hidden',
                              background: 'var(--card)',
                              transition: 'box-shadow 0.2s'
                            }}
                          >
                            {/* Header */}
                            <div
                              onClick={() => toggleExpanded(key)}
                              style={{
                                padding: '14px 20px',
                                cursor: 'pointer',
                                background: comp.has_differences 
                                  ? 'linear-gradient(90deg, rgba(239,68,68,0.08), transparent)'
                                  : 'linear-gradient(90deg, rgba(34,197,94,0.08), transparent)',
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                borderLeft: `4px solid ${comp.has_differences ? '#ef4444' : '#22c55e'}`
                              }}
                            >
                              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                                <span style={{ 
                                  fontSize: 12, 
                                  color: 'var(--muted)',
                                  transition: 'transform 0.2s',
                                  transform: isExpanded ? 'rotate(90deg)' : 'rotate(0)'
                                }}>▶</span>
                                <div>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                    <span style={{ fontWeight: 600, fontSize: 14 }}>{comp.name}</span>
                                    <span style={{
                                      padding: '2px 8px',
                                      background: 'rgba(99,102,241,0.15)',
                                      borderRadius: 4,
                                      fontSize: 10,
                                      textTransform: 'uppercase',
                                      color: 'var(--accent)',
                                      fontWeight: 600
                                    }}>{comp.kind}</span>
                                  </div>
                                  {comp.namespace1 !== comp.namespace2 ? (
                                    <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                                      {comp.namespace1} ↔ {comp.namespace2}
                                    </div>
                                  ) : (
                                    <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                                      {comp.namespace1}
                                    </div>
                                  )}
                                </div>
                              </div>
                              
                              <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                                {comp.error ? (
                                  <span style={{ 
                                    padding: '4px 12px',
                                    background: 'rgba(239,68,68,0.1)',
                                    color: '#ef4444',
                                    borderRadius: 6,
                                    fontSize: 12,
                                    fontWeight: 500
                                  }}>⚠️ Error</span>
                                ) : comp.has_differences ? (
                                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                    <span style={{ 
                                      padding: '4px 12px',
                                      background: 'rgba(239,68,68,0.15)',
                                      color: '#ef4444',
                                      borderRadius: 6,
                                      fontSize: 12,
                                      fontWeight: 600
                                    }}>
                                      {comp.difference_count} diff{comp.difference_count !== 1 ? 's' : ''}
                                    </span>
                                    {relatedDiffCount > 0 && (
                                      <span style={{ 
                                        padding: '4px 12px',
                                        background: 'rgba(245,158,11,0.15)',
                                        color: '#f59e0b',
                                        borderRadius: 6,
                                        fontSize: 12,
                                        fontWeight: 500
                                      }}>
                                        +{relatedDiffCount} related
                                      </span>
                                    )}
                                  </div>
                                ) : (
                                  <span style={{ 
                                    padding: '4px 12px',
                                    background: 'rgba(34,197,94,0.15)',
                                    color: '#22c55e',
                                    borderRadius: 6,
                                    fontSize: 12,
                                    fontWeight: 500
                                  }}>✓ Identical</span>
                                )}
                              </div>
                            </div>

                            {/* Expanded content */}
                            {isExpanded && (
                              <div style={{ 
                                padding: 20, 
                                borderTop: '1px solid var(--border)',
                                background: 'var(--bg)',
                                animation: 'slideDown 0.2s ease-out'
                              }}>
                                {/* Metadata */}
                                <div style={{ 
                                  display: 'flex', 
                                  justifyContent: 'space-between',
                                  alignItems: 'center',
                                  marginBottom: 16,
                                  paddingBottom: 12,
                                  borderBottom: '1px solid var(--border)'
                                }}>
                                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                                    {comp.manifest_updated_at1 && (
                                      <span>📅 {result.cluster1}: {new Date(comp.manifest_updated_at1).toLocaleString()}</span>
                                    )}
                                    {comp.manifest_updated_at1 && comp.manifest_updated_at2 && <span style={{ margin: '0 12px' }}>|</span>}
                                    {comp.manifest_updated_at2 && (
                                      <span>📅 {result.cluster2}: {new Date(comp.manifest_updated_at2).toLocaleString()}</span>
                                    )}
                                  </div>
                                  {comp.yaml1 && comp.yaml2 && (
                                    <button
                                      onClick={() => { setSelectedResource(comp); setShowYamlView(true); }}
                                      style={{
                                        padding: '6px 12px',
                                        border: '1px solid var(--border)',
                                        borderRadius: 6,
                                        background: 'var(--card)',
                                        cursor: 'pointer',
                                        fontSize: 11,
                                        color: 'inherit',
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 6
                                      }}
                                    >
                                      📝 View Full YAML
                                    </button>
                                  )}
                                </div>

                                {comp.error ? (
                                  <div style={{ 
                                    padding: 16, 
                                    background: 'rgba(239,68,68,0.1)', 
                                    borderRadius: 8,
                                    color: '#ef4444'
                                  }}>
                                    {comp.error}
                                  </div>
                                ) : (
                                  <>
                                    {/* Main workload differences - hidden when filtering by related object type */}
                                    {!isFilteringRelatedOnly && comp.differences && comp.differences.length > 0 && (
                                      <div style={{ marginBottom: 20 }}>
                                        <h4 style={{ 
                                          margin: '0 0 12px', 
                                          fontSize: 13,
                                          display: 'flex',
                                          alignItems: 'center',
                                          gap: 8
                                        }}>
                                          <span style={{ fontSize: 16 }}>📦</span>
                                          Workload Differences
                                          <span style={{ 
                                            padding: '2px 8px',
                                            background: 'rgba(239,68,68,0.1)',
                                            borderRadius: 4,
                                            fontSize: 11,
                                            color: '#ef4444'
                                          }}>{comp.difference_count}</span>
                                        </h4>
                                        
                                        <div style={{ 
                                          borderRadius: 8, 
                                          overflow: 'hidden',
                                          border: '1px solid var(--border)'
                                        }}>
                                          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                            <thead>
                                              <tr style={{ background: 'var(--card)' }}>
                                                <th style={{ 
                                                  textAlign: 'left', 
                                                  padding: '10px 14px', 
                                                  borderBottom: '1px solid var(--border)', 
                                                  width: '35%',
                                                  fontWeight: 600
                                                }}>
                                                  Path
                                                </th>
                                                <th style={{ 
                                                  textAlign: 'left', 
                                                  padding: '10px 14px', 
                                                  borderBottom: '1px solid var(--border)', 
                                                  width: '30%',
                                                  color: 'var(--accent)',
                                                  fontWeight: 600
                                                }}>
                                                  {result.cluster1}
                                                </th>
                                                <th style={{ 
                                                  textAlign: 'left', 
                                                  padding: '10px 14px', 
                                                  borderBottom: '1px solid var(--border)', 
                                                  width: '30%',
                                                  color: '#f59e0b',
                                                  fontWeight: 600
                                                }}>
                                                  {result.cluster2}
                                                </th>
                                              </tr>
                                            </thead>
                                            <tbody>
                                              {comp.differences.map((diff, di) => (
                                                <tr key={di}>
                                                  <td style={{ 
                                                    padding: '10px 14px', 
                                                    borderBottom: '1px solid var(--border)', 
                                                    fontFamily: 'monospace', 
                                                    fontSize: 11,
                                                    wordBreak: 'break-all',
                                                    background: 'var(--card)'
                                                  }}>
                                                    {diff.path}
                                                  </td>
                                                  <td style={{
                                                    padding: '10px 14px',
                                                    borderBottom: '1px solid var(--border)',
                                                    wordBreak: 'break-all',
                                                    background: diff.type === 'missing_in_cluster1' ? 'rgba(239,68,68,0.08)' : 'transparent'
                                                  }}>
                                                    {diff.type === 'missing_in_cluster1' ? (
                                                      <span style={{ color: '#ef4444', fontStyle: 'italic' }}>— missing —</span>
                                                    ) : (
                                                      <code style={{ 
                                                        padding: '2px 6px', 
                                                        background: 'rgba(99,102,241,0.1)', 
                                                        borderRadius: 4,
                                                        fontSize: 11
                                                      }}>{diff.value1}</code>
                                                    )}
                                                  </td>
                                                  <td style={{
                                                    padding: '10px 14px',
                                                    borderBottom: '1px solid var(--border)',
                                                    wordBreak: 'break-all',
                                                    background: diff.type === 'missing_in_cluster2' ? 'rgba(239,68,68,0.08)' : 'transparent'
                                                  }}>
                                                    {diff.type === 'missing_in_cluster2' ? (
                                                      <span style={{ color: '#ef4444', fontStyle: 'italic' }}>— missing —</span>
                                                    ) : (
                                                      <code style={{ 
                                                        padding: '2px 6px', 
                                                        background: 'rgba(245,158,11,0.1)', 
                                                        borderRadius: 4,
                                                        fontSize: 11
                                                      }}>{diff.value2}</code>
                                                    )}
                                                  </td>
                                                </tr>
                                              ))}
                                            </tbody>
                                          </table>
                                        </div>
                                      </div>
                                    )}
                                    
                                    {/* Related objects differences */}
                                    {comp.related_differences && Object.keys(comp.related_differences).length > 0 && (
                                      <div>
                                        <h4 style={{ 
                                          margin: '0 0 12px', 
                                          fontSize: 13,
                                          display: 'flex',
                                          alignItems: 'center',
                                          gap: 8
                                        }}>
                                          <span style={{ fontSize: 16 }}>🔗</span>
                                          Related Objects
                                          {isFilteringRelatedOnly && (
                                            <span style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 'normal' }}>
                                              (filtered: {diffKindFilter})
                                            </span>
                                          )}
                                        </h4>
                                        
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                                          {/* PDB and HPA - Full comparison */}
                                          {['pdb', 'hpa'].filter(t => !isFilteringRelatedOnly || t === diffKindFilter.toLowerCase()).map(relType => {
                                            const objs = comp.related_differences?.[relType]
                                            if (!objs || objs.length === 0) return null
                                            return (
                                              <div key={relType} style={{ 
                                                padding: 14,
                                                background: 'var(--card)',
                                                borderRadius: 8,
                                                border: '1px solid var(--border)'
                                              }}>
                                                <div style={{ 
                                                  display: 'flex',
                                                  alignItems: 'center',
                                                  gap: 8,
                                                  marginBottom: 10
                                                }}>
                                                  <span>{relType === 'pdb' ? '🛡️' : '📈'}</span>
                                                  <span style={{ fontWeight: 600, fontSize: 12 }}>
                                                    {relType === 'pdb' ? 'PodDisruptionBudget' : 'HorizontalPodAutoscaler'}
                                                  </span>
                                                  <span style={{
                                                    padding: '2px 8px',
                                                    background: 'rgba(139,92,246,0.1)',
                                                    borderRadius: 4,
                                                    fontSize: 10,
                                                    color: '#8b5cf6'
                                                  }}>{objs.length}</span>
                                                </div>
                                                {objs.map((obj: RelatedObjectDiff, oi: number) => (
                                                  <div key={oi} style={{ 
                                                    marginLeft: 24,
                                                    padding: '8px 12px',
                                                    background: 'var(--bg)',
                                                    borderRadius: 6,
                                                    marginBottom: 6,
                                                    fontSize: 12
                                                  }}>
                                                    <div style={{ fontWeight: 500 }}>
                                                      {obj.name}
                                                      {obj.type === 'missing_in_cluster1' && (
                                                        <span style={{ marginLeft: 8, color: '#f59e0b', fontSize: 11 }}>
                                                          only in {result.cluster2}
                                                        </span>
                                                      )}
                                                      {obj.type === 'missing_in_cluster2' && (
                                                        <span style={{ marginLeft: 8, color: 'var(--accent)', fontSize: 11 }}>
                                                          only in {result.cluster1}
                                                        </span>
                                                      )}
                                                    </div>
                                                    {obj.type === 'different' && obj.differences && obj.differences.length > 0 && (
                                                      <div style={{ marginTop: 6, marginLeft: 8 }}>
                                                        {obj.differences.slice(0, 5).map((d: Difference, di: number) => (
                                                          <div key={di} style={{ fontSize: 11, color: 'var(--muted)' }}>
                                                            • {d.path}: 
                                                            <span style={{ color: 'var(--accent)', marginLeft: 4 }}>{d.value1 || '∅'}</span>
                                                            <span style={{ margin: '0 4px' }}>→</span>
                                                            <span style={{ color: '#f59e0b' }}>{d.value2 || '∅'}</span>
                                                          </div>
                                                        ))}
                                                        {obj.differences.length > 5 && (
                                                          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                                                            +{obj.differences.length - 5} more differences
                                                          </div>
                                                        )}
                                                      </div>
                                                    )}
                                                  </div>
                                                ))}
                                              </div>
                                            )
                                          })}
                                          
                                          {/* Service, ConfigMap, Secret, Route, PVC - Full manifest comparison */}
                                          {['service', 'configmap', 'secret', 'route', 'pvc'].filter(t => !isFilteringRelatedOnly || t === diffKindFilter.toLowerCase()).map(relType => {
                                            const objs = comp.related_differences?.[relType]
                                            if (!objs || objs.length === 0) return null
                                            const icons: { [k: string]: string } = {
                                              service: '🌐', configmap: '⚙️', secret: '🔐', route: '🛣️', pvc: '💾'
                                            }
                                            const labels: { [k: string]: string } = {
                                              service: 'Service', configmap: 'ConfigMap', secret: 'Secret', route: 'Route', pvc: 'PersistentVolumeClaim'
                                            }
                                            // Check if any object has differences (full comparison mode)
                                            const hasFullComparison = objs.some((obj: RelatedObjectDiff) => obj.type === 'different' && obj.differences && obj.differences.length > 0)
                                            return (
                                              <div key={relType} style={{ 
                                                padding: 14,
                                                background: 'var(--card)',
                                                borderRadius: 8,
                                                border: '1px solid var(--border)'
                                              }}>
                                                <div style={{ 
                                                  display: 'flex',
                                                  alignItems: 'center',
                                                  gap: 8,
                                                  marginBottom: 10
                                                }}>
                                                  <span>{icons[relType]}</span>
                                                  <span style={{ fontWeight: 600, fontSize: 12 }}>{labels[relType]}</span>
                                                  <span style={{
                                                    padding: '2px 8px',
                                                    background: hasFullComparison ? 'rgba(99,102,241,0.1)' : 'rgba(245,158,11,0.1)',
                                                    borderRadius: 4,
                                                    fontSize: 10,
                                                    color: hasFullComparison ? 'var(--accent)' : '#f59e0b'
                                                  }}>{hasFullComparison ? 'full comparison' : 'existence check'}</span>
                                                </div>
                                                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginLeft: 24 }}>
                                                  {objs.map((obj: RelatedObjectDiff, oi: number) => (
                                                    <div key={oi}>
                                                      <div 
                                                        style={{ 
                                                          display: 'inline-flex',
                                                          alignItems: 'center',
                                                          padding: '4px 10px',
                                                          borderRadius: 6,
                                                          fontSize: 11,
                                                          background: obj.type === 'missing_in_cluster1' 
                                                            ? 'rgba(245,158,11,0.15)' 
                                                            : obj.type === 'matched_normalized'
                                                            ? 'rgba(34,197,94,0.15)'
                                                            : obj.type === 'different'
                                                            ? 'rgba(239,68,68,0.15)'
                                                            : 'rgba(99,102,241,0.15)',
                                                          color: obj.type === 'missing_in_cluster1' 
                                                            ? '#f59e0b' 
                                                            : obj.type === 'matched_normalized'
                                                            ? '#22c55e'
                                                            : obj.type === 'different'
                                                            ? '#ef4444'
                                                            : 'var(--accent)',
                                                          border: `1px solid ${
                                                            obj.type === 'missing_in_cluster1' 
                                                              ? 'rgba(245,158,11,0.3)' 
                                                              : obj.type === 'matched_normalized'
                                                              ? 'rgba(34,197,94,0.3)'
                                                              : obj.type === 'different'
                                                              ? 'rgba(239,68,68,0.3)'
                                                              : 'rgba(99,102,241,0.3)'
                                                          }`
                                                        }}
                                                      >
                                                        {obj.type === 'matched_normalized' ? (
                                                          <>
                                                            <span title={`${obj.cluster1_name} ↔ ${obj.cluster2_name}`}>
                                                              {obj.normalized_name}
                                                            </span>
                                                            <span style={{ marginLeft: 6, opacity: 0.7, fontSize: 10 }}>
                                                              (✓ matched)
                                                            </span>
                                                          </>
                                                        ) : obj.type === 'different' ? (
                                                          <>
                                                            {obj.name}
                                                            {obj.normalized_name && (
                                                              <span style={{ marginLeft: 4, opacity: 0.5, fontSize: 9 }}>
                                                                [{obj.normalized_name}]
                                                              </span>
                                                            )}
                                                            <span style={{ marginLeft: 6, opacity: 0.7, fontSize: 10 }}>
                                                              ({obj.differences?.length || 0} fark)
                                                            </span>
                                                          </>
                                                        ) : (
                                                          <>
                                                            {obj.name}
                                                            {obj.normalized_name && (
                                                              <span style={{ marginLeft: 4, opacity: 0.5, fontSize: 9 }}>
                                                                [{obj.normalized_name}]
                                                              </span>
                                                            )}
                                                            <span style={{ marginLeft: 6, opacity: 0.7, fontSize: 10 }}>
                                                              ({obj.type === 'missing_in_cluster1' ? `in ${result.cluster2}` : `in ${result.cluster1}`})
                                                            </span>
                                                          </>
                                                        )}
                                                      </div>
                                                      {/* Show differences for 'different' type */}
                                                      {obj.type === 'different' && obj.differences && obj.differences.length > 0 && (
                                                        <div style={{ marginTop: 6, marginLeft: 8 }}>
                                                          {obj.differences.slice(0, 5).map((d: Difference, di: number) => (
                                                            <div key={di} style={{ fontSize: 11, color: 'var(--muted)' }}>
                                                              {d.type?.startsWith('key_') ? (
                                                                <span style={{ 
                                                                  color: d.type === 'key_value_different' ? '#f59e0b' 
                                                                       : d.type === 'key_missing_in_cluster1' ? '#f59e0b' 
                                                                       : 'var(--accent)' 
                                                                }}>
                                                                  • {d.value1}
                                                                </span>
                                                              ) : (
                                                                <>
                                                                  • {d.path}: 
                                                                  <span style={{ color: 'var(--accent)', marginLeft: 4 }}>{d.value1 || '∅'}</span>
                                                                  <span style={{ margin: '0 4px' }}>→</span>
                                                                  <span style={{ color: '#f59e0b' }}>{d.value2 || '∅'}</span>
                                                                </>
                                                              )}
                                                            </div>
                                                          ))}
                                                          {obj.differences.length > 5 && (
                                                            <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                                                              +{obj.differences.length - 5} more differences
                                                            </div>
                                                          )}
                                                        </div>
                                                      )}
                                                    </div>
                                                  ))}
                                                </div>
                                              </div>
                                            )
                                          })}
                                        </div>
                                      </div>
                                    )}
                                    
                                    {(!comp.differences || comp.differences.length === 0) && 
                                     (!comp.related_differences || Object.keys(comp.related_differences).length === 0) && (
                                      <div style={{ 
                                        textAlign: 'center', 
                                        padding: 24,
                                        color: '#22c55e'
                                      }}>
                                        <div style={{ fontSize: 32, marginBottom: 8 }}>✓</div>
                                        <p style={{ margin: 0 }}>This resource is identical in both clusters</p>
                                      </div>
                                    )}
                                  </>
                                )}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* Only in Cluster 1 Tab */}
              {activeTab === 'only1' && (
                <div>
                  {result.only_in_cluster1.length === 0 && relatedObjectsOnlyIn1.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: 48, color: 'var(--muted)' }}>
                      <div style={{ fontSize: 48, marginBottom: 16 }}>📭</div>
                      <p>No unique resources in {result.cluster1}</p>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                      {/* Filter Bar */}
                      <div style={{
                        display: 'flex',
                        gap: 12,
                        padding: 16,
                        background: 'var(--card)',
                        borderRadius: 8,
                        border: '1px solid var(--border)',
                        flexWrap: 'wrap',
                        alignItems: 'center'
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 200 }}>
                          <span style={{ fontSize: 16 }}>🔍</span>
                          <input
                            type="text"
                            placeholder="Search by name, namespace, product..."
                            value={resultSearch}
                            onChange={e => setResultSearch(e.target.value)}
                            style={{
                              flex: 1,
                              padding: '8px 12px',
                              background: 'var(--bg)',
                              border: '1px solid var(--border)',
                              borderRadius: 6,
                              color: 'var(--foreground)',
                              fontSize: 13
                            }}
                          />
                        </div>
                        <select
                          value={resultKindFilter}
                          onChange={e => setResultKindFilter(e.target.value)}
                          style={{
                            padding: '8px 12px',
                            background: 'var(--bg)',
                            border: '1px solid var(--border)',
                            borderRadius: 6,
                            color: 'var(--foreground)',
                            fontSize: 13,
                            minWidth: 150
                          }}
                        >
                          <option value="">All Types</option>
                          {uniqueKinds.map(k => (
                            <option key={k} value={k}>{k}</option>
                          ))}
                        </select>
                        <select
                          value={resultNsFilter}
                          onChange={e => setResultNsFilter(e.target.value)}
                          style={{
                            padding: '8px 12px',
                            background: 'var(--bg)',
                            border: '1px solid var(--border)',
                            borderRadius: 6,
                            color: 'var(--foreground)',
                            fontSize: 13,
                            minWidth: 180
                          }}
                        >
                          <option value="">All Namespaces</option>
                          {uniqueNamespaces.map(ns => (
                            <option key={ns} value={ns}>{ns}</option>
                          ))}
                        </select>
                        {(resultSearch || resultKindFilter || resultNsFilter) && (
                          <button
                            onClick={() => {
                              setResultSearch('')
                              setResultKindFilter('')
                              setResultNsFilter('')
                            }}
                            style={{
                              padding: '8px 12px',
                              background: 'rgba(239,68,68,0.1)',
                              border: '1px solid rgba(239,68,68,0.3)',
                              borderRadius: 6,
                              color: '#ef4444',
                              fontSize: 12,
                              cursor: 'pointer'
                            }}
                          >
                            ✕ Clear Filters
                          </button>
                        )}
                        <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                          Showing {filteredWorkloads1.length + filteredRelated1.length} of {result.only_in_cluster1.length + relatedObjectsOnlyIn1.length}
                        </span>
                      </div>

                      {/* Workloads Section */}
                      {filteredWorkloads1.length > 0 && (
                        <div>
                          <h4 style={{ margin: '0 0 12px 0', fontSize: 13, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
                            📦 Workloads ({filteredWorkloads1.length})
                          </h4>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            {filteredWorkloads1.map((r, i) => (
                              <div key={i} style={{
                                padding: '12px 16px',
                                background: 'var(--card)',
                                borderRadius: 8,
                                border: '1px solid var(--border)',
                                borderLeft: '4px solid #3b82f6',
                                display: 'flex',
                                alignItems: 'center',
                                gap: 12
                              }}>
                                <span style={{
                                  padding: '4px 10px',
                                  background: 'rgba(59,130,246,0.1)',
                                  borderRadius: 4,
                                  fontSize: 10,
                                  textTransform: 'uppercase',
                                  color: '#3b82f6',
                                  fontWeight: 600
                                }}>{r.kind}</span>
                                <div>
                                  <div style={{ fontWeight: 500 }}>{r.name}</div>
                                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>{r.namespace}</div>
                                </div>
                                {r.product_name && (
                                  <span style={{
                                    marginLeft: 'auto',
                                    padding: '4px 10px',
                                    background: 'rgba(139,92,246,0.1)',
                                    borderRadius: 4,
                                    fontSize: 10,
                                    color: '#8b5cf6'
                                  }}>{r.product_name}</span>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Related Objects Section */}
                      {filteredRelated1.length > 0 && (
                        <div>
                          <h4 style={{ margin: '0 0 12px 0', fontSize: 13, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
                            🔗 Related Objects ({filteredRelated1.length})
                          </h4>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            {filteredRelated1.map((r, i) => (
                              <div key={i} style={{
                                padding: '12px 16px',
                                background: 'var(--card)',
                                borderRadius: 8,
                                border: '1px solid var(--border)',
                                borderLeft: '4px solid #60a5fa',
                                display: 'flex',
                                alignItems: 'center',
                                gap: 12
                              }}>
                                <span style={{
                                  padding: '4px 10px',
                                  background: 'rgba(96,165,250,0.1)',
                                  borderRadius: 4,
                                  fontSize: 10,
                                  textTransform: 'uppercase',
                                  color: '#60a5fa',
                                  fontWeight: 600
                                }}>{r.kind}</span>
                                <div>
                                  <div style={{ fontWeight: 500 }}>{r.name}</div>
                                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                                    {r.namespace} • Workload: <strong>{r.workload}</strong>
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* No results after filtering */}
                      {filteredWorkloads1.length === 0 && filteredRelated1.length === 0 && (
                        <div style={{ textAlign: 'center', padding: 48, color: 'var(--muted)' }}>
                          <div style={{ fontSize: 48, marginBottom: 16 }}>🔍</div>
                          <p>No results match your filters</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Only in Cluster 2 Tab */}
              {activeTab === 'only2' && (
                <div>
                  {result.only_in_cluster2.length === 0 && relatedObjectsOnlyIn2.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: 48, color: 'var(--muted)' }}>
                      <div style={{ fontSize: 48, marginBottom: 16 }}>📭</div>
                      <p>No unique resources in {result.cluster2}</p>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                      {/* Filter Bar */}
                      <div style={{
                        display: 'flex',
                        gap: 12,
                        padding: 16,
                        background: 'var(--card)',
                        borderRadius: 8,
                        border: '1px solid var(--border)',
                        flexWrap: 'wrap',
                        alignItems: 'center'
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 200 }}>
                          <span style={{ fontSize: 16 }}>🔍</span>
                          <input
                            type="text"
                            placeholder="Search by name, namespace, product..."
                            value={resultSearch}
                            onChange={e => setResultSearch(e.target.value)}
                            style={{
                              flex: 1,
                              padding: '8px 12px',
                              background: 'var(--bg)',
                              border: '1px solid var(--border)',
                              borderRadius: 6,
                              color: 'var(--foreground)',
                              fontSize: 13
                            }}
                          />
                        </div>
                        <select
                          value={resultKindFilter}
                          onChange={e => setResultKindFilter(e.target.value)}
                          style={{
                            padding: '8px 12px',
                            background: 'var(--bg)',
                            border: '1px solid var(--border)',
                            borderRadius: 6,
                            color: 'var(--foreground)',
                            fontSize: 13,
                            minWidth: 150
                          }}
                        >
                          <option value="">All Types</option>
                          {uniqueKinds.map(k => (
                            <option key={k} value={k}>{k}</option>
                          ))}
                        </select>
                        <select
                          value={resultNsFilter}
                          onChange={e => setResultNsFilter(e.target.value)}
                          style={{
                            padding: '8px 12px',
                            background: 'var(--bg)',
                            border: '1px solid var(--border)',
                            borderRadius: 6,
                            color: 'var(--foreground)',
                            fontSize: 13,
                            minWidth: 180
                          }}
                        >
                          <option value="">All Namespaces</option>
                          {uniqueNamespaces.map(ns => (
                            <option key={ns} value={ns}>{ns}</option>
                          ))}
                        </select>
                        {(resultSearch || resultKindFilter || resultNsFilter) && (
                          <button
                            onClick={() => {
                              setResultSearch('')
                              setResultKindFilter('')
                              setResultNsFilter('')
                            }}
                            style={{
                              padding: '8px 12px',
                              background: 'rgba(239,68,68,0.1)',
                              border: '1px solid rgba(239,68,68,0.3)',
                              borderRadius: 6,
                              color: '#ef4444',
                              fontSize: 12,
                              cursor: 'pointer'
                            }}
                          >
                            ✕ Clear Filters
                          </button>
                        )}
                        <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                          Showing {filteredWorkloads2.length + filteredRelated2.length} of {result.only_in_cluster2.length + relatedObjectsOnlyIn2.length}
                        </span>
                      </div>

                      {/* Workloads Section */}
                      {filteredWorkloads2.length > 0 && (
                        <div>
                          <h4 style={{ margin: '0 0 12px 0', fontSize: 13, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
                            📦 Workloads ({filteredWorkloads2.length})
                          </h4>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            {filteredWorkloads2.map((r, i) => (
                              <div key={i} style={{
                                padding: '12px 16px',
                                background: 'var(--card)',
                                borderRadius: 8,
                                border: '1px solid var(--border)',
                                borderLeft: '4px solid #f59e0b',
                                display: 'flex',
                                alignItems: 'center',
                                gap: 12
                              }}>
                                <span style={{
                                  padding: '4px 10px',
                                  background: 'rgba(245,158,11,0.1)',
                                  borderRadius: 4,
                                  fontSize: 10,
                                  textTransform: 'uppercase',
                                  color: '#f59e0b',
                                  fontWeight: 600
                                }}>{r.kind}</span>
                                <div>
                                  <div style={{ fontWeight: 500 }}>{r.name}</div>
                                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>{r.namespace}</div>
                                </div>
                                {r.product_name && (
                                  <span style={{
                                    marginLeft: 'auto',
                                    padding: '4px 10px',
                                    background: 'rgba(139,92,246,0.1)',
                                    borderRadius: 4,
                                    fontSize: 10,
                                    color: '#8b5cf6'
                                  }}>{r.product_name}</span>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Related Objects Section */}
                      {filteredRelated2.length > 0 && (
                        <div>
                          <h4 style={{ margin: '0 0 12px 0', fontSize: 13, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
                            🔗 Related Objects ({filteredRelated2.length})
                          </h4>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            {filteredRelated2.map((r, i) => (
                              <div key={i} style={{
                                padding: '12px 16px',
                                background: 'var(--card)',
                                borderRadius: 8,
                                border: '1px solid var(--border)',
                                borderLeft: '4px solid #fbbf24',
                                display: 'flex',
                                alignItems: 'center',
                                gap: 12
                              }}>
                                <span style={{
                                  padding: '4px 10px',
                                  background: 'rgba(251,191,36,0.1)',
                                  borderRadius: 4,
                                  fontSize: 10,
                                  textTransform: 'uppercase',
                                  color: '#fbbf24',
                                  fontWeight: 600
                                }}>{r.kind}</span>
                                <div>
                                  <div style={{ fontWeight: 500 }}>{r.name}</div>
                                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                                    {r.namespace} • Workload: <strong>{r.workload}</strong>
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* No results after filtering */}
                      {filteredWorkloads2.length === 0 && filteredRelated2.length === 0 && (
                        <div style={{ textAlign: 'center', padding: 48, color: 'var(--muted)' }}>
                          <div style={{ fontSize: 48, marginBottom: 16 }}>🔍</div>
                          <p>No results match your filters</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* YAML Diff Modal */}
      {showYamlView && selectedResource && (
        <YamlDiffViewer comp={selectedResource} />
      )}

      {/* Save Report Dialog */}
      {showSaveDialog && (
        <div style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.8)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 2000
        }}>
          <div style={{
            background: 'var(--card)',
            borderRadius: 12,
            padding: 24,
            width: '100%',
            maxWidth: 400,
            border: '1px solid var(--border)'
          }}>
            <h3 style={{ margin: '0 0 16px 0', display: 'flex', alignItems: 'center', gap: 10 }}>
              💾 Save Comparison Report
            </h3>
            <input
              type="text"
              placeholder="Report name (e.g., Pre-release check 2026-01-10)"
              value={reportName}
              onChange={e => setReportName(e.target.value)}
              style={{
                width: '100%',
                padding: '12px 16px',
                borderRadius: 8,
                border: '1px solid var(--border)',
                background: 'var(--bg)',
                color: 'var(--foreground)',
                fontSize: 14,
                marginBottom: 16
              }}
              autoFocus
            />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button
                onClick={() => {
                  setShowSaveDialog(false)
                  setReportName('')
                }}
                style={{
                  padding: '10px 20px',
                  borderRadius: 8,
                  background: 'var(--bg)',
                  border: '1px solid var(--border)',
                  color: 'var(--foreground)',
                  cursor: 'pointer'
                }}
              >
                Cancel
              </button>
              <button
                onClick={saveReport}
                disabled={!reportName.trim() || savingReport}
                style={{
                  padding: '10px 20px',
                  borderRadius: 8,
                  background: !reportName.trim() ? 'var(--muted)' : 'linear-gradient(135deg, #10b981, #059669)',
                  border: 'none',
                  color: 'white',
                  cursor: !reportName.trim() ? 'not-allowed' : 'pointer',
                  fontWeight: 500
                }}
              >
                {savingReport ? 'Saving...' : 'Save Report'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Saved Reports Modal */}
      {showSavedReports && (
        <div style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.8)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 2000
        }}>
          <div style={{
            background: 'var(--card)',
            borderRadius: 12,
            width: '100%',
            maxWidth: 800,
            maxHeight: '80vh',
            overflow: 'hidden',
            border: '1px solid var(--border)',
            display: 'flex',
            flexDirection: 'column'
          }}>
            {/* Header */}
            <div style={{ 
              padding: '16px 24px', 
              borderBottom: '1px solid var(--border)',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center'
            }}>
              <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 10 }}>
                📋 Saved Comparison Reports
              </h3>
              <button
                onClick={() => setShowSavedReports(false)}
                style={{
                  background: 'none',
                  border: 'none',
                  fontSize: 20,
                  cursor: 'pointer',
                  color: 'var(--muted)'
                }}
              >
                ✕
              </button>
            </div>
            
            {/* Content */}
            <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
              {savedReports.length === 0 ? (
                <div style={{ textAlign: 'center', padding: 48, color: 'var(--muted)' }}>
                  <div style={{ fontSize: 48, marginBottom: 16 }}>📭</div>
                  <p>No saved reports yet</p>
                  <p style={{ fontSize: 12 }}>Run a comparison and click "Save" to save it for later.</p>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {savedReports.map(report => (
                    <div
                      key={report.id}
                      style={{
                        padding: 16,
                        background: 'var(--bg)',
                        borderRadius: 8,
                        border: '1px solid var(--border)'
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
                        <div>
                          <h4 style={{ margin: 0, marginBottom: 4 }}>{report.name}</h4>
                          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                            {report.created_by} • {new Date(report.created_at).toLocaleString()}
                          </div>
                        </div>
                        <div style={{ display: 'flex', gap: 8 }}>
                          <button
                            onClick={() => viewReport(report.id)}
                            style={{
                              padding: '6px 12px',
                              borderRadius: 6,
                              background: 'var(--accent)',
                              border: 'none',
                              color: 'white',
                              cursor: 'pointer',
                              fontSize: 12
                            }}
                          >
                            👁️ View
                          </button>
                          {/* Admin can delete any report, Analyst can only delete their own */}
                          {(isAdmin || (isAnalyst && report.created_by === currentUsername)) && (
                            <button
                              onClick={() => deleteReport(report.id, report.name)}
                              style={{
                                padding: '6px 12px',
                                borderRadius: 6,
                                background: 'rgba(239,68,68,0.1)',
                                border: '1px solid rgba(239,68,68,0.3)',
                                color: '#ef4444',
                                cursor: 'pointer',
                                fontSize: 12
                              }}
                            >
                              🗑️ Delete
                            </button>
                          )}
                        </div>
                      </div>
                      
                      {/* Report Summary */}
                      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                        <div style={{ fontSize: 12 }}>
                          <span style={{ color: 'var(--muted)' }}>Clusters:</span>{' '}
                          <strong>{report.cluster1.replace('-backend', '')}</strong> vs <strong>{report.cluster2.replace('-backend', '')}</strong>
                        </div>
                        <div style={{ fontSize: 12 }}>
                          <span style={{ color: 'var(--muted)' }}>Common:</span> <strong>{report.common_count}</strong>
                        </div>
                        <div style={{ fontSize: 12 }}>
                          <span style={{ color: 'var(--muted)' }}>Differences:</span>{' '}
                          <strong style={{ color: report.with_differences_count > 0 ? '#f59e0b' : '#22c55e' }}>
                            {report.with_differences_count}
                          </strong>
                        </div>
                        <div style={{ fontSize: 12 }}>
                          <span style={{ color: 'var(--muted)' }}>Total Diffs:</span>{' '}
                          <strong>{report.total_diffs_count}</strong>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Viewing Report Banner */}
      {viewingReport && (
        <div style={{
          position: 'fixed',
          top: 60,
          left: '50%',
          transform: 'translateX(-50%)',
          background: 'linear-gradient(135deg, #10b981, #059669)',
          color: 'white',
          padding: '10px 20px',
          borderRadius: 8,
          zIndex: 1000,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          boxShadow: '0 4px 20px rgba(0,0,0,0.3)'
        }}>
          <span>📋 Viewing saved report: <strong>{viewingReport.name}</strong></span>
          <button
            onClick={() => {
              setViewingReport(null)
              setResult(null)
            }}
            style={{
              background: 'rgba(255,255,255,0.2)',
              border: 'none',
              color: 'white',
              padding: '4px 12px',
              borderRadius: 4,
              cursor: 'pointer',
              fontSize: 12
            }}
          >
            ✕ Close
          </button>
        </div>
      )}

      {/* CSS Animations */}
      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes slideDown {
          from { opacity: 0; max-height: 0; }
          to { opacity: 1; max-height: 2000px; }
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  )
}

export default Compare
