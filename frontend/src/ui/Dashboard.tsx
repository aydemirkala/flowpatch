import React, { useEffect, useState } from 'react'
import { fetchResources } from '../services/multiBackend'

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
  product_name?: string | null
  eol_date?: string | null
  note?: string | null
  planned_upgrade_at?: string | null
  security_info?: any | null
  container_name?: string | null
}

const GROUP_OPTIONS: Array<{value: string, label: string}> = [
  {value: 'product_name', label: 'Product'},
  {value: 'namespace', label: 'Namespace'},
  {value: 'platform', label: 'Platform'},
  {value: 'kind', label: 'Kind'},
  {value: 'replicas', label: 'Replicas'},
  {value: 'registry', label: 'Image Registry'},
  {value: 'image_repo', label: 'Image Repository'},
  {value: 'version_diff', label: 'Version Diff'},
  {value: 'container_name', label: 'Container Name'},
  {value: 'current_version', label: 'Current Version'},
  {value: 'latest_version', label: 'Latest Version'},
]

const COUNT_OPTIONS: Array<{value: string, label: string}> = [
  {value: 'count', label: 'Count of Items'},
  {value: 'unique_namespaces', label: 'Unique Namespaces'},
  {value: 'unique_containers', label: 'Unique Containers'},
  {value: 'eol_issues', label: 'EOL Issues (expired)'},
  {value: 'security_risks', label: 'Security Risks (riskFactorCount > 0)'},
  {value: 'high_severity', label: 'High Severity Vulnerabilities'},
  {value: 'critical_severity', label: 'Critical Severity Vulnerabilities'},
  {value: 'missing_plans', label: 'Missing Upgrade Plans'},
  {value: 'with_notes', label: 'Resources with Notes'},
]

const FILTER_OPTIONS: Array<{value: string, label: string}> = [
  {value: 'product_name', label: 'Product'},
  {value: 'namespace', label: 'Namespace'},
  {value: 'platform', label: 'Platform'},
  {value: 'kind', label: 'Kind'},
  {value: 'replicas', label: 'Replicas'},
  {value: 'registry', label: 'Image Registry'},
  {value: 'image_repo', label: 'Image Repository'},
  {value: 'version_diff', label: 'Version Diff'},
  {value: 'container_name', label: 'Container Name'},
]

const GROUP_OPTIONS_MAP: Record<string, string> = Object.fromEntries(GROUP_OPTIONS.map(o => [o.value, o.label]))
const COUNT_OPTIONS_MAP: Record<string, string> = Object.fromEntries(COUNT_OPTIONS.map(o => [o.value, o.label]))

export default function Dashboard({ sharedResources }: { sharedResources?: any[] } = {}): JSX.Element {
  const [resources, setResources] = useState<Resource[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [savedCharts, setSavedCharts] = useState<any[]>([])
  const [lastUpdate, setLastUpdate] = useState<number>(Date.now())
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([])
  const [editingChart, setEditingChart] = useState<any>(null)
  const [chartLimit, setChartLimit] = useState<number>(10)

  const loadResources = async () => {
    try {
      const token = sessionStorage.getItem('token')
      if (!token) return
      
      const rawResources = (sharedResources && sharedResources.length > 0)
        ? sharedResources
        : await fetchResources(token)
      
      // Expand containers into separate rows (same as main Resources page)
      const expanded: any[] = []
      const seen = new Set<string>() // Track unique resources to avoid duplicates
      
      for (const r of rawResources) {
        // Use _platform from multi-backend if available, otherwise use platform
        const platformValue = (r as any)._platform || r.platform || 'unknown'
        
        const containers: any[] = ((r as any).containers || (r as any).security_info?.containers || []) as any[]
        if (Array.isArray(containers) && containers.length > 0) {
          for (const c of containers) {
            const img = String(c?.image || r.image || '')
            const tag = img && img.includes(':') ? img.split(':').pop() as string : 'latest'
            const cloned: any = { ...r, image: img, current_version: tag, container_name: c?.name || '', platform: platformValue }
            
            // Create unique key: platform + namespace + name + kind + container
            const uniqueKey = `${platformValue}|${r.namespace || ''}|${r.resource_name || ''}|${r.kind || ''}|${c?.name || ''}`
            
            // Skip if already seen (duplicate from another backend)
            if (seen.has(uniqueKey)) continue
            seen.add(uniqueKey)
            
            if (c.latest_version !== undefined) cloned.latest_version = c.latest_version
            if (c.version_diff !== undefined) cloned.version_diff = c.version_diff
            cloned.eol_date = c.eol_date !== undefined ? c.eol_date : null
            const parentSI: any = (r as any).security_info || {}
            const mergedSI: any = { ...parentSI }
            if (c && c.twistlock) mergedSI.twistlock = c.twistlock
            cloned.security_info = mergedSI
            expanded.push(cloned)
          }
        } else {
          // Create unique key for resources without containers
          const uniqueKey = `${platformValue}|${r.namespace || ''}|${r.resource_name || ''}|${r.kind || ''}|`
          
          // Skip if already seen
          if (seen.has(uniqueKey)) continue
          seen.add(uniqueKey)
          
          // Ensure platform is set correctly
          const resourceWithPlatform = { ...r, platform: platformValue }
          expanded.push(resourceWithPlatform)
        }
      }
      setResources(expanded)
      setLastUpdate(Date.now())
    } catch {}
    setLoading(false)
  }

  useEffect(() => {
    void loadResources()
    // Poll for updates every 30 seconds
    const interval = setInterval(() => {
      void loadResources()
    }, 30000)
    return () => clearInterval(interval)
  }, [])
  
  useEffect(() => {
    const loadCharts = async () => {
      try {
        const token = sessionStorage.getItem('token') || ''
        const resp = await fetch('/api/user-charts', { headers: { Authorization: `Bearer ${token}` } })
        if (resp.ok) {
          setSavedCharts(await resp.json())
        }
      } catch {}
    }
    void loadCharts()
  }, [])
  
  // Initialize selected platforms when resources load
  useEffect(() => {
    if (resources.length > 0 && selectedPlatforms.length === 0) {
      const allPlatforms = Array.from(new Set(resources.map(r => r.platform))).sort()
      setSelectedPlatforms(allPlatforms)
    }
  }, [resources, selectedPlatforms.length])

  if (loading) return <p className="muted">Loading dashboard data...</p>

  // Get all unique platforms
  const allPlatforms = Array.from(new Set(resources.map(r => r.platform))).sort()
  
  // Filter resources by selected platforms
  const filteredResources = selectedPlatforms.length > 0 
    ? resources.filter(r => selectedPlatforms.includes(r.platform))
    : resources

  // Helper to limit to top N items
  const limitTopN = (data: Record<string, number>, n: number): Record<string, number> => {
    const entries = Object.entries(data).sort((a, b) => b[1] - a[1])
    const topEntries = entries.slice(0, n)
    const remaining = entries.slice(n)
    const result: Record<string, number> = {}
    topEntries.forEach(([k, v]) => { result[k] = v })
    if (remaining.length > 0) {
      const otherSum = remaining.reduce((sum, [, v]) => sum + v, 0)
      if (otherSum > 0) {
        result[`Others (${remaining.length})`] = otherSum
      }
    }
    return result
  }

  // Calculate metrics
  const productStats = calculateProductStats(filteredResources)
  const containersByProductRaw = calculateContainersByProduct(filteredResources)
  const eolIssuesByProductRaw = calculateEOLIssuesByProduct(filteredResources)
  const securityIssuesByProductRaw = calculateSecurityIssuesByProduct(filteredResources)
  const productsByImageRepoRaw = calculateProductsByImageRepo(filteredResources)
  
  // Apply chart limit (0 = show all)
  const containersByProduct = chartLimit > 0 ? limitTopN(containersByProductRaw, chartLimit) : containersByProductRaw
  const eolIssuesByProduct = chartLimit > 0 ? limitTopN(eolIssuesByProductRaw, chartLimit) : eolIssuesByProductRaw
  const securityIssuesByProduct = chartLimit > 0 ? limitTopN(securityIssuesByProductRaw, chartLimit) : securityIssuesByProductRaw
  const productsByImageRepo = chartLimit > 0 ? limitTopN(productsByImageRepoRaw, chartLimit) : productsByImageRepoRaw

  return (
    <div>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16,flexWrap:'wrap',gap:12}}>
        <div style={{fontSize:12,color:'var(--muted)'}}>Last updated: {new Date(lastUpdate).toLocaleString()}</div>
        <button className="btn secondary" onClick={() => loadResources()} style={{fontSize:11,padding:'4px 8px'}}>
          Refresh Data
        </button>
      </div>
      
      {/* Platform Selector - Always Show */}
      {allPlatforms.length > 0 && (
        <div className="card" style={{marginBottom:16,borderLeft:'4px solid #8b5cf6'}}>
          <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
            <h3 style={{margin:0,fontSize:14}}>🌐 Platform Filter</h3>
            <div style={{display:'flex',gap:8}}>
              <button 
                className="btn secondary" 
                onClick={() => setSelectedPlatforms(allPlatforms)}
                style={{fontSize:11,padding:'3px 8px'}}
              >
                Select All
              </button>
              <button 
                className="btn secondary" 
                onClick={() => setSelectedPlatforms([])}
                style={{fontSize:11,padding:'3px 8px'}}
                disabled={selectedPlatforms.length === 0}
              >
                Clear All
              </button>
            </div>
          </div>
          <div style={{display:'flex',gap:16,flexWrap:'wrap',alignItems:'center',marginBottom:10}}>
            {allPlatforms.map(platform => (
              <label key={platform} style={{display:'flex',alignItems:'center',gap:6,cursor:'pointer',fontSize:12,padding:'4px 8px',background:'rgba(139,92,246,0.1)',borderRadius:6,border:'1px solid rgba(139,92,246,0.3)'}}>
                <input 
                  type="checkbox" 
                  checked={selectedPlatforms.includes(platform)}
                  onChange={(e) => {
                    if (e.target.checked) {
                      setSelectedPlatforms([...selectedPlatforms, platform])
                    } else {
                      setSelectedPlatforms(selectedPlatforms.filter(p => p !== platform))
                    }
                  }}
                  style={{width:16,height:16,cursor:'pointer'}}
                />
                <span style={{fontWeight:selectedPlatforms.includes(platform)?600:400}}>{platform}</span>
              </label>
            ))}
          </div>
          <div className="muted" style={{fontSize:11}}>
            {selectedPlatforms.length === 0 ? (
              <span style={{color:'#f59e0b'}}>⚠️ No platforms selected - Charts will be empty</span>
            ) : selectedPlatforms.length === allPlatforms.length ? (
              <span>✅ Showing all {allPlatforms.length} platforms</span>
            ) : (
              <span>📊 Showing {selectedPlatforms.length} of {allPlatforms.length} platforms</span>
            )}
          </div>
        </div>
      )}
      
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit, minmax(300px, 1fr))',gap:16,marginBottom:24}}>
        <MetricCard title="Platforms" value={selectedPlatforms.length} color="#8b5cf6" />
        <MetricCard title="Total Resources" value={filteredResources.length} color="#3b82f6" />
        <MetricCard title="Products" value={Object.keys(productStats).length} color="#10b981" />
        <MetricCard title="EOL Issues" value={Object.values(eolIssuesByProductRaw).reduce((a,b)=>a+b,0)} color="#ef4444" />
        <MetricCard title="Security Risks" value={Object.values(securityIssuesByProductRaw).reduce((a,b)=>a+b,0)} color="#f59e0b" />
      </div>

      {/* Chart Limit Selector */}
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16,marginTop:16}}>
        <h2 style={{margin:0}}>Dashboard Charts</h2>
        <div style={{display:'flex',gap:8,alignItems:'center'}}>
          <span style={{fontSize:12,color:'var(--muted)'}}>Show:</span>
          <select 
            className="input" 
            value={chartLimit} 
            onChange={(e) => setChartLimit(Number(e.target.value))}
            style={{fontSize:11,padding:'4px 12px',minWidth:120}}
          >
            <option value={5}>📊 Top 5</option>
            <option value={10}>📊 Top 10</option>
            <option value={15}>📊 Top 15</option>
            <option value={20}>📊 Top 20</option>
            <option value={0}>📈 All</option>
          </select>
        </div>
      </div>

      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit, minmax(400px, 1fr))',gap:16}}>
        <div className="card">
          <h3 style={{margin:'0 0 12px 0',fontSize:16}}>Containers per Product</h3>
          <PieChart data={containersByProduct} />
        </div>
        <div className="card">
          <h3 style={{margin:'0 0 12px 0',fontSize:16}}>EOL Issues per Product</h3>
          <PieChart data={eolIssuesByProduct} />
        </div>
        <div className="card">
          <h3 style={{margin:'0 0 12px 0',fontSize:16}}>Security Issues per Product</h3>
          <PieChart data={securityIssuesByProduct} />
        </div>
        <div className="card">
          <h3 style={{margin:'0 0 12px 0',fontSize:16}}>Products by Image Repository</h3>
          <PieChart data={productsByImageRepo} />
        </div>
      </div>

      {savedCharts.length > 0 && (
        <div style={{marginTop:24}}>
          <h2 style={{marginBottom:16}}>My Saved Charts</h2>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit, minmax(400px, 1fr))',gap:16}}>
            {savedCharts.map(chart => (
              <SavedChartCard 
                key={chart.id} 
                chart={chart} 
                resources={filteredResources}
                onEdit={(id, filters) => {
                  setEditingChart({ ...chart, id, filters })
                }}
                onDelete={() => {
                  const token = sessionStorage.getItem('token') || ''
                  fetch(`/api/user-charts/${chart.id}`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } })
                    .then(() => setSavedCharts(savedCharts.filter(c => c.id !== chart.id)))
                }}
              />
            ))}
          </div>
        </div>
      )}

      <div className="card" style={{marginTop:16}}>
        <h2>Custom Chart Builder</h2>
        <p className="muted">Create and save custom charts by selecting data dimensions</p>
        <CustomChartBuilder 
          resources={filteredResources} 
          onSave={(name, groupBy, countBy, filters, chartType) => {
            const token = sessionStorage.getItem('token') || ''
            fetch('/api/user-charts', {
              method: 'POST',
              headers: { 
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
              },
              body: JSON.stringify({ 
                chart_name: name, 
                group_by: groupBy, 
                count_by: countBy,
                filters: filters.length > 0 ? filters : null,
                chart_type: chartType || 'bar'
              })
            }).then(resp => {
              if (resp.ok) {
                resp.json().then(data => {
                  setSavedCharts([{
                    id: data.id, 
                    chart_name: name, 
                    group_by: groupBy, 
                    count_by: countBy,
                    filters: filters,
                    chart_type: chartType || 'bar',
                    created_at: new Date().toISOString()
                  }, ...savedCharts])
                })
              }
            })
          }}
        />
      </div>
      
      {editingChart && (
        <EditChartModal 
          chart={editingChart}
          onClose={() => setEditingChart(null)}
          onSave={(id, name, groupBy, countBy, filters, chartType) => {
            const token = sessionStorage.getItem('token') || ''
            fetch(`/api/user-charts/${id}`, {
              method: 'PUT',
              headers: { 
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
              },
              body: JSON.stringify({ 
                chart_name: name, 
                group_by: groupBy, 
                count_by: countBy,
                filters: filters.length > 0 ? filters : null,
                chart_type: chartType || 'bar'
              })
            }).then(resp => {
              if (resp.ok) {
                setSavedCharts(savedCharts.map(c => 
                  c.id === id 
                    ? { ...c, chart_name: name, group_by: groupBy, count_by: countBy, filters: filters, chart_type: chartType }
                    : c
                ))
                setEditingChart(null)
                alert('Chart updated successfully!')
              }
            })
          }}
        />
      )}
    </div>
  )
}

function EditChartModal({chart, onClose, onSave}: {chart: any; onClose: () => void; onSave: (id: number, name: string, groupBy: string, countBy: string, filters: Array<{column: string, value: string}>, chartType: string) => void}): JSX.Element {
  const [name, setName] = useState(chart.chart_name || '')
  const [groupBy, setGroupBy] = useState(chart.group_by || 'product_name')
  const [countBy, setCountBy] = useState(chart.count_by || 'count')
  const [filters, setFilters] = useState<Array<{column: string, value: string}>>(chart.filters || [])
  const [chartType, setChartType] = useState(chart.chart_type || 'bar')
  
  const addFilter = () => {
    setFilters([...filters, {column: 'platform', value: ''}])
  }
  
  const removeFilter = (index: number) => {
    setFilters(filters.filter((_, i) => i !== index))
  }
  
  const updateFilter = (index: number, field: 'column' | 'value', newValue: string) => {
    const updated = [...filters]
    updated[index][field] = newValue
    setFilters(updated)
  }
  
  return (
    <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.85)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:1000,overflow:'auto'}} onClick={onClose}>
      <div className="card" style={{maxWidth:600,width:'95%',margin:'20px auto',background:'var(--panel)',boxShadow:'0 20px 60px rgba(0,0,0,.5)'}} onClick={(e)=>e.stopPropagation()}>
        <h2 style={{marginTop:0}}>Edit Chart</h2>
        <div style={{marginBottom:16}}>
          <label style={{display:'block',marginBottom:4,fontSize:12}}>Chart Name</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} style={{width:'100%'}} />
        </div>
        <div style={{marginBottom:16}}>
          <label style={{display:'block',marginBottom:4,fontSize:12}}>Group By</label>
          <select className="input" value={groupBy} onChange={(e) => setGroupBy(e.target.value)} style={{width:'100%'}}>
            {GROUP_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
          </select>
        </div>
        <div style={{marginBottom:16}}>
          <label style={{display:'block',marginBottom:4,fontSize:12}}>Measure</label>
          <select className="input" value={countBy} onChange={(e) => setCountBy(e.target.value)} style={{width:'100%'}}>
            {COUNT_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
          </select>
        </div>
        
        {/* Multiple Filters Section */}
        <div style={{marginBottom:16}}>
          <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
            <label style={{fontSize:12}}>Filters (Optional)</label>
            <button className="btn secondary" onClick={addFilter} style={{fontSize:10,padding:'4px 8px'}}>
              + Add Filter
            </button>
          </div>
          {filters.length > 0 && (
            <div style={{padding:12,background:'rgba(139,92,246,0.05)',border:'1px solid var(--border)',borderRadius:4}}>
              {filters.map((filter, idx) => (
                <div key={idx} style={{display:'flex',gap:8,marginBottom:8,alignItems:'center'}}>
                  <select 
                    className="input" 
                    value={filter.column} 
                    onChange={(e) => updateFilter(idx, 'column', e.target.value)}
                    style={{flex:1,fontSize:12}}
                  >
                    {FILTER_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                  </select>
                  <input 
                    className="input" 
                    placeholder="Filter value..." 
                    value={filter.value} 
                    onChange={(e) => updateFilter(idx, 'value', e.target.value)}
                    style={{flex:1,fontSize:12}}
                  />
                  <button 
                    className="btn danger" 
                    onClick={() => removeFilter(idx)}
                    style={{fontSize:10,padding:'4px 8px',minWidth:0}}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
        
        <div style={{marginBottom:16}}>
          <label style={{display:'block',marginBottom:4,fontSize:12}}>Chart Type</label>
          <select className="input" value={chartType} onChange={(e) => setChartType(e.target.value)} style={{width:'100%'}}>
            <option value="bar">📊 Bar Chart</option>
            <option value="pie">🥧 Pie Chart</option>
          </select>
        </div>
        <div style={{display:'flex',gap:8,justifyContent:'flex-end',marginTop:20}}>
          <button className="btn secondary" onClick={onClose}>Cancel</button>
          <button className="btn" onClick={() => onSave(chart.id, name, groupBy, countBy, filters, chartType)}>Save Changes</button>
        </div>
      </div>
    </div>
  )
}

function SavedChartCard({chart, resources, onDelete, onEdit}: {chart: any; resources: Resource[]; onDelete: () => void; onEdit: (id: number, filters: Array<{column: string, value: string}>) => void}): JSX.Element {
  const [viewMode, setViewMode] = React.useState<'bar' | 'pie'>(chart.chart_type || 'bar')
  
  // Auto-save chart_type when view mode changes
  const handleViewModeChange = (mode: 'bar' | 'pie') => {
    setViewMode(mode)
    const token = sessionStorage.getItem('token') || ''
    fetch(`/api/user-charts/${chart.id}`, {
      method: 'PUT',
      headers: { 
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({ 
        chart_name: chart.chart_name, 
        group_by: chart.group_by, 
        count_by: chart.count_by,
        filters: chart.filters || null,
        chart_type: mode
      })
    })
  }
  
  const groupOptions = GROUP_OPTIONS_MAP
  const countOptions = COUNT_OPTIONS_MAP
  
  // Apply ALL filters (new multi-filter support)
  let filteredResources = resources
  const chartFilters = chart.filters || []
  if (chartFilters.length > 0) {
    filteredResources = resources.filter(r => {
      return chartFilters.every((filter: {column: string, value: string}) => {
        if (!filter.column || !filter.value) return true
        let rawVal: any
        if (filter.column === 'image_repo') {
          rawVal = extractImageRepo(r.image || '')
        } else if (filter.column === 'registry') {
          rawVal = extractRegistry(r.image || '')
        } else {
          rawVal = (r as any)[filter.column]
        }
        if (rawVal == null) return false
        return String(rawVal).toLowerCase().includes(String(filter.value).toLowerCase())
      })
    })
  }
  
  const customData = calculateCustomChart(filteredResources, chart.group_by, chart.count_by)
  
  return (
    <div className="card">
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
        <h3 style={{margin:0,fontSize:15}}>{chart.chart_name}</h3>
        <div style={{display:'flex',gap:8,alignItems:'center'}}>
          <div style={{display:'flex',gap:4,background:'rgba(139,92,246,0.1)',borderRadius:4,padding:2}}>
            <button 
              className={viewMode === 'bar' ? 'btn' : 'btn secondary'}
              onClick={() => handleViewModeChange('bar')}
              style={{fontSize:10,padding:'4px 8px',minWidth:0}}
              title="Bar Chart"
            >
              📊
            </button>
            <button 
              className={viewMode === 'pie' ? 'btn' : 'btn secondary'}
              onClick={() => handleViewModeChange('pie')}
              style={{fontSize:10,padding:'4px 8px',minWidth:0}}
              title="Pie Chart"
            >
              🥧
            </button>
          </div>
          <button 
            className="btn secondary" 
            onClick={() => onEdit(chart.id, chart.filters || [])} 
            style={{fontSize:10,padding:'4px 8px'}}
          >
            Edit
          </button>
          <button className="btn danger" onClick={onDelete} style={{fontSize:10,padding:'2px 6px'}}>Delete</button>
        </div>
      </div>
      <p style={{margin:'0 0 12px 0',fontSize:11,color:'var(--muted)'}}>
        {countOptions[chart.count_by]} by {groupOptions[chart.group_by]}
        {chartFilters.length > 0 && (
          <span style={{color:'#8b5cf6',marginLeft:8}}>
            • {chartFilters.length} filter{chartFilters.length > 1 ? 's' : ''}: {chartFilters.map((f: {column: string, value: string}) => `${groupOptions[f.column] || f.column}="${f.value}"`).join(', ')}
          </span>
        )}
      </p>
      {viewMode === 'bar' ? (
        <ChartCard title="" data={customData} />
      ) : (
        <PieChart data={customData} />
      )}
    </div>
  )
}

function MetricCard({title, value, color}: {title: string; value: number; color: string}): JSX.Element {
  return (
    <div className="card" style={{borderLeft:`4px solid ${color}`}}>
      <div style={{fontSize:12,color:'var(--muted)',marginBottom:4}}>{title}</div>
      <div style={{fontSize:28,fontWeight:600,color}}>{value}</div>
    </div>
  )
}

function ChartCard({title, data}: {title: string; data: Record<string, number>}): JSX.Element {
  const entries = Object.entries(data).sort((a,b) => b[1] - a[1])
  const total = entries.reduce((sum, [,v]) => sum + v, 0)
  
  return (
    <div className="card">
      <h3 style={{margin:'0 0 12px 0',fontSize:16}}>{title}</h3>
      {entries.length === 0 ? (
        <p className="muted" style={{fontSize:12}}>No data</p>
      ) : (
        <div style={{display:'flex',flexDirection:'column',gap:8}}>
          {entries.map(([key, value]) => {
            const pct = total > 0 ? Math.round((value / total) * 100) : 0
            return (
              <div key={key}>
                <div style={{display:'flex',justifyContent:'space-between',marginBottom:4,fontSize:12}}>
                  <span>{key || 'Unknown'}</span>
                  <span><strong>{value}</strong> ({pct}%)</span>
                </div>
                <div style={{height:8,background:'var(--card)',borderRadius:4,overflow:'hidden'}}>
                  <div style={{height:'100%',background:'var(--brand)',width:`${pct}%`,transition:'width 0.3s ease'}} />
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function PieChart({data}: {data: Record<string, number>}): JSX.Element {
  // Global Top 10/All toggle controls the data, so no need for individual dropdowns
  const entries = Object.entries(data).sort((a,b) => b[1] - a[1])
  const total = entries.reduce((sum, [,v]) => sum + v, 0)
  
  if (entries.length === 0) {
    return <p className="muted" style={{fontSize:12}}>No data</p>
  }
  
  // Color palette
  const colors = [
    '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#3b82f6',
    '#ef4444', '#06b6d4', '#8b5cf6', '#f97316', '#84cc16'
  ]
  
  // Calculate pie slices
  let currentAngle = -90 // Start at top
  const slices = entries.map(([key, value], idx) => {
    const percentage = (value / total) * 100
    const angle = (value / total) * 360
    const startAngle = currentAngle
    currentAngle += angle
    
    return {
      key,
      value,
      percentage,
      startAngle,
      angle,
      color: colors[idx % colors.length]
    }
  })
  
  // SVG pie chart
  const size = 200
  const center = size / 2
  const radius = size / 2 - 10
  
  const polarToCartesian = (angle: number) => {
    const rad = (angle * Math.PI) / 180
    return {
      x: center + radius * Math.cos(rad),
      y: center + radius * Math.sin(rad)
    }
  }
  
  return (
    <div>
      <div style={{display:'flex',gap:20,alignItems:'flex-start',flexWrap:'wrap'}}>
        <svg width={size} height={size} style={{flexShrink:0}}>
        {slices.map((slice, idx) => {
          const start = polarToCartesian(slice.startAngle)
          const end = polarToCartesian(slice.startAngle + slice.angle)
          const largeArc = slice.angle > 180 ? 1 : 0
          
          const path = [
            `M ${center} ${center}`,
            `L ${start.x} ${start.y}`,
            `A ${radius} ${radius} 0 ${largeArc} 1 ${end.x} ${end.y}`,
            'Z'
          ].join(' ')
          
          return (
            <path
              key={idx}
              d={path}
              fill={slice.color}
              stroke="#0b1324"
              strokeWidth={2}
            />
          )
        })}
      </svg>
      
      <div style={{flex:1,minWidth:200,maxHeight:320,overflowY:'auto',paddingRight:8}}>
        {slices.map((slice, idx) => (
          <div key={idx} style={{display:'flex',alignItems:'center',gap:8,marginBottom:8,fontSize:12}}>
            <div style={{width:12,height:12,borderRadius:2,background:slice.color,flexShrink:0}} />
            <div style={{flex:1,display:'flex',justifyContent:'space-between',gap:8}}>
              <span style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}} title={slice.key}>
                {slice.key || 'Unknown'}
              </span>
              <span style={{fontWeight:600,whiteSpace:'nowrap'}}>
                {slice.value} ({Math.round(slice.percentage)}%)
              </span>
            </div>
          </div>
        ))}
      </div>
      </div>
    </div>
  )
}

function CustomChartBuilder({resources, onSave}: {resources: Resource[]; onSave: (name: string, groupBy: string, countBy: string, filters: Array<{column: string, value: string}>, chartType: string) => void}): JSX.Element {
  const [groupBy, setGroupBy] = useState<string>('product_name')
  const [countBy, setCountBy] = useState<string>('count')
  const [chartName, setChartName] = useState<string>('')
  const [saving, setSaving] = useState<boolean>(false)
  const [filters, setFilters] = useState<Array<{column: string, value: string}>>([])
  const [chartType, setChartType] = useState<string>('bar')
  
  const groupOptions = GROUP_OPTIONS
  const countOptions = COUNT_OPTIONS
  const filterOptions = FILTER_OPTIONS
  
  // Apply ALL filters
  let filteredResources = resources
  if (filters.length > 0) {
    filteredResources = resources.filter(r => {
      // Resource must match ALL filters
      return filters.every(filter => {
        if (!filter.column || !filter.value.trim()) return true
        
        let rawVal: any
        if (filter.column === 'image_repo') {
          rawVal = extractImageRepo(r.image)
        } else if (filter.column === 'registry') {
          rawVal = extractRegistry(r.image)
        } else {
          rawVal = (r as any)[filter.column]
        }
        if (rawVal == null) return false
        const valStr = String(rawVal).toLowerCase()
        const filterStr = filter.value.trim().toLowerCase()
        return valStr === filterStr || valStr.includes(filterStr)
      })
    })
  }
  
  const customData = calculateAdvancedChart(filteredResources, groupBy, countBy)
  
  const handleSave = () => {
    if (!chartName.trim()) {
      alert('Please enter a chart name')
      return
    }
    setSaving(true)
    onSave(chartName.trim(), groupBy, countBy, filters, chartType)
    setChartName('')
    setFilters([])
    setSaving(false)
  }
  
  const addFilter = () => {
    setFilters([...filters, {column: 'platform', value: ''}])
  }
  
  const removeFilter = (index: number) => {
    setFilters(filters.filter((_, i) => i !== index))
  }
  
  const updateFilter = (index: number, field: 'column' | 'value', newValue: string) => {
    const updated = [...filters]
    updated[index][field] = newValue
    setFilters(updated)
  }
  
  return (
    <div>
      <div style={{display:'flex',gap:12,marginBottom:16,flexWrap:'wrap',alignItems:'end'}}>
        <div>
          <label style={{display:'block',marginBottom:4,fontSize:12,color:'var(--muted)'}}>Chart Name</label>
          <input className="input" placeholder="e.g. Security by Product" value={chartName} onChange={(e) => setChartName(e.target.value)} style={{minWidth:180}} />
        </div>
        <div>
          <label style={{display:'block',marginBottom:4,fontSize:12,color:'var(--muted)'}}>Group By (X-axis)</label>
          <select className="input" value={groupBy} onChange={(e) => setGroupBy(e.target.value)} style={{minWidth:150}}>
            {groupOptions.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
          </select>
        </div>
        <div>
          <label style={{display:'block',marginBottom:4,fontSize:12,color:'var(--muted)'}}>Measure (Y-axis)</label>
          <select className="input" value={countBy} onChange={(e) => setCountBy(e.target.value)} style={{minWidth:180}}>
            {countOptions.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
          </select>
        </div>
        <div>
          <label style={{display:'block',marginBottom:4,fontSize:12,color:'var(--muted)'}}>Chart Type</label>
          <select className="input" value={chartType} onChange={(e) => setChartType(e.target.value)} style={{minWidth:120}}>
            <option value="bar">📊 Bar Chart</option>
            <option value="pie">🥧 Pie Chart</option>
          </select>
        </div>
        <button className="btn secondary" onClick={addFilter} style={{fontSize:11,padding:'6px 12px'}}>
          + Add Filter
        </button>
        <button className="btn" onClick={handleSave} disabled={saving || !chartName.trim()}>
          {saving ? 'Saving...' : 'Save Chart'}
        </button>
      </div>
      
      {/* Multiple Filters UI */}
      {filters.length > 0 && (
        <div style={{marginBottom:16,padding:12,background:'var(--panel)',border:'1px solid var(--border)',borderRadius:8}}>
          <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
            <h4 style={{margin:0,fontSize:13,color:'var(--muted)'}}>🔍 Filters (AND logic - all must match)</h4>
            <button className="btn secondary" onClick={() => setFilters([])} style={{fontSize:10,padding:'2px 6px'}}>
              Clear All
            </button>
          </div>
          {filters.map((filter, idx) => (
            <div key={idx} style={{display:'flex',gap:8,marginBottom:8,alignItems:'center'}}>
              <select 
                className="input" 
                value={filter.column} 
                onChange={(e) => updateFilter(idx, 'column', e.target.value)}
                style={{minWidth:140,fontSize:12}}
              >
                {filterOptions.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
              </select>
              <input 
                className="input" 
                placeholder="Filter value..." 
                value={filter.value} 
                onChange={(e) => updateFilter(idx, 'value', e.target.value)}
                style={{minWidth:160,fontSize:12}}
              />
              <button 
                className="btn secondary" 
                onClick={() => removeFilter(idx)}
                style={{fontSize:10,padding:'4px 8px',background:'#ef4444',color:'white'}}
              >
                ✕ Remove
              </button>
            </div>
          ))}
        </div>
      )}
      
      <div style={{marginBottom:12,fontSize:12,color:'var(--muted)'}}>
        Showing {filteredResources.length} of {resources.length} items
        {filters.length > 0 && (
          <span style={{marginLeft:8}}>
            with {filters.length} filter{filters.length > 1 ? 's' : ''}: 
            {filters.map((f, i) => (
              <span key={i} style={{marginLeft:4,padding:'2px 6px',background:'#3b82f6',color:'white',borderRadius:4,fontSize:11}}>
                {filterOptions.find(o=>o.value===f.column)?.label}="{f.value}"
              </span>
            ))}
          </span>
        )}
      </div>
      <ChartCard title={chartName || `${countOptions.find(o=>o.value===countBy)?.label} by ${groupOptions.find(o=>o.value===groupBy)?.label}`} data={customData} />
    </div>
  )
}

// Helper functions
function extractRegistry(image: string | null | undefined): string {
  if (!image) return 'unknown'
  
  // Extract registry (first part before /)
  const parts = image.split('/')
  if (parts.length === 1) return 'docker.io' // No slash = docker.io
  const hasRegistry = parts[0].includes('.') || parts[0].includes(':')
  return hasRegistry ? parts[0] : 'docker.io'
}

function extractImageRepo(image: string | null | undefined): string {
  if (!image) return 'unknown'
  
  // Examples:
  //   docker.io/bitnami/redis:7.0 -> bitnami/redis
  //   ghcr.io/dapr/placement:1.15 -> dapr/placement
  //   registry.example.com/docker-proxy/bitnami/kafka:3.6 -> docker-proxy/bitnami/kafka
  //   centos:7 -> centos
  
  const parts = image.split('/')
  if (parts.length === 1) {
    // No slashes, just image:tag (e.g., centos:7)
    return image.split(':')[0]
  } else {
    // Has registry/repo structure
    // Take everything except first part (registry) and remove tag from last part
    const lastPart = parts[parts.length - 1].split(':')[0] // Remove tag
    const repoPath = parts.slice(1, -1).concat(lastPart).join('/')
    return repoPath || 'unknown'
  }
}

function calculateProductStats(resources: Resource[]): Record<string, number> {
  const stats: Record<string, number> = {}
  resources.forEach(r => {
    const prod = r.product_name || 'unknown'
    stats[prod] = (stats[prod] || 0) + 1
  })
  return stats
}

function calculateNamespacesByProduct(resources: Resource[]): Record<string, number> {
  const map: Record<string, Set<string>> = {}
  resources.forEach(r => {
    const prod = r.product_name || 'unknown'
    if (!map[prod]) map[prod] = new Set()
    map[prod].add(r.namespace)
  })
  const result: Record<string, number> = {}
  Object.keys(map).forEach(prod => {
    result[prod] = map[prod].size
  })
  return result
}

function calculateContainersByProduct(resources: Resource[]): Record<string, number> {
  const stats: Record<string, number> = {}
  resources.forEach(r => {
    // Only count rows that have a container_name (expanded container rows)
    // OR resources without containers (single-image resources)
    if (r.container_name || !(r as any).security_info?.containers) {
      const prod = r.product_name || 'unknown'
      stats[prod] = (stats[prod] || 0) + 1
    }
  })
  return stats
}

function calculateEOLIssuesByProduct(resources: Resource[]): Record<string, number> {
  const stats: Record<string, number> = {}
  const now = new Date()
  resources.forEach(r => {
    if (r.eol_date) {
      const eolDate = new Date(r.eol_date)
      if (eolDate.getTime() < now.getTime()) {
        const prod = r.product_name || 'unknown'
        stats[prod] = (stats[prod] || 0) + 1
      }
    }
  })
  return stats
}

function calculateSecurityIssuesByProduct(resources: Resource[]): Record<string, number> {
  const stats: Record<string, number> = {}
  resources.forEach(r => {
    const tw: any = (r.security_info || {}).twistlock
    const riskCount = tw?.riskFactorCount || 0
    if (riskCount > 0) {
      const prod = r.product_name || 'unknown'
      stats[prod] = (stats[prod] || 0) + 1
    }
  })
  return stats
}

function calculateUpgradePlansByResource(resources: Resource[]): Record<string, number> {
  return {
    'With Plan': resources.filter(r => r.planned_upgrade_at).length,
    'Without Plan': resources.filter(r => !r.planned_upgrade_at).length,
  }
}

function calculateProductsByImageRepo(resources: Resource[]): Record<string, number> {
  // Group by image repository (e.g., bitnami, nginx, etc.)
  // Count unique products per repository
  const repoProducts: Record<string, Set<string>> = {}
  
  resources.forEach(r => {
    const repo = extractImageRepo(r.image)
    // Extract just the first part of the repo path (e.g., "bitnami/redis" -> "bitnami")
    const repoName = repo.split('/')[0]
    const product = r.product_name || 'unknown'
    
    if (!repoProducts[repoName]) {
      repoProducts[repoName] = new Set()
    }
    repoProducts[repoName].add(product)
  })
  
  // Convert to counts
  const stats: Record<string, number> = {}
  Object.entries(repoProducts).forEach(([repo, products]) => {
    stats[repo] = products.size
  })
  
  return stats
}

function calculateCustomChart(resources: Resource[], groupBy: string, countBy: string): Record<string, number> {
  return calculateAdvancedChart(resources, groupBy, countBy)
}

function calculateAdvancedChartWithSecondary(resources: Resource[], groupBy: string, secondaryBy: string, countBy: string): Record<string, number> {
  const stats: Record<string, number> = {}
  const uniqueSets: Record<string, Set<string>> = {}
  const now = new Date()
  
  resources.forEach(r => {
    const rawPrimary = groupBy === 'image_repo' 
      ? extractImageRepo(r.image) 
      : groupBy === 'registry'
      ? extractRegistry(r.image)
      : (r as any)[groupBy]
    const primaryVal = rawPrimary != null && rawPrimary !== '' ? String(rawPrimary) : 'unknown'
    const rawSecondary = secondaryBy === 'image_repo' 
      ? extractImageRepo(r.image) 
      : secondaryBy === 'registry'
      ? extractRegistry(r.image)
      : (r as any)[secondaryBy]
    const secondaryVal = rawSecondary != null && rawSecondary !== '' ? String(rawSecondary) : 'unknown'
    const groupKey = `${primaryVal} (${secondaryVal})`
    
    if (countBy === 'count') {
      stats[groupKey] = (stats[groupKey] || 0) + 1
    } else if (countBy === 'unique_namespaces') {
      if (!uniqueSets[groupKey]) uniqueSets[groupKey] = new Set()
      uniqueSets[groupKey].add(r.namespace)
    } else if (countBy === 'unique_containers') {
      if (!uniqueSets[groupKey]) uniqueSets[groupKey] = new Set()
      if (r.container_name) uniqueSets[groupKey].add(r.container_name)
    } else if (countBy === 'eol_issues') {
      if (r.eol_date && new Date(r.eol_date).getTime() < now.getTime()) {
        stats[groupKey] = (stats[groupKey] || 0) + 1
      }
    } else if (countBy === 'security_risks') {
      const tw: any = (r.security_info || {}).twistlock
      if ((tw?.riskFactorCount || 0) > 0) {
        stats[groupKey] = (stats[groupKey] || 0) + 1
      }
    } else if (countBy === 'high_severity') {
      const tw: any = (r.security_info || {}).twistlock
      const dist = tw?.vulnerabilityDistribution || {}
      const highCount = dist.high || 0
      if (highCount > 0) {
        stats[groupKey] = (stats[groupKey] || 0) + highCount
      }
    } else if (countBy === 'critical_severity') {
      const tw: any = (r.security_info || {}).twistlock
      const dist = tw?.vulnerabilityDistribution || {}
      const critCount = dist.critical || 0
      if (critCount > 0) {
        stats[groupKey] = (stats[groupKey] || 0) + critCount
      }
    } else if (countBy === 'missing_plans') {
      if (!r.planned_upgrade_at) {
        stats[groupKey] = (stats[groupKey] || 0) + 1
      }
    } else if (countBy === 'with_notes') {
      if (r.note) {
        stats[groupKey] = (stats[groupKey] || 0) + 1
      }
    }
  })
  
  // Convert sets to counts
  Object.keys(uniqueSets).forEach(key => {
    stats[key] = uniqueSets[key].size
  })
  
  return stats
}

function calculateAdvancedChart(resources: Resource[], groupBy: string, countBy: string): Record<string, number> {
  const stats: Record<string, number> = {}
  const uniqueSets: Record<string, Set<string>> = {}
  const now = new Date()
  
  resources.forEach(r => {
    const rawVal = groupBy === 'image_repo' 
      ? extractImageRepo(r.image)
      : groupBy === 'registry'
      ? extractRegistry(r.image)
      : (r as any)[groupBy]
    const groupKey = rawVal != null && rawVal !== '' ? String(rawVal) : 'unknown'
    
    if (countBy === 'count') {
      stats[groupKey] = (stats[groupKey] || 0) + 1
    } else if (countBy === 'unique_namespaces') {
      if (!uniqueSets[groupKey]) uniqueSets[groupKey] = new Set()
      uniqueSets[groupKey].add(r.namespace)
    } else if (countBy === 'unique_containers') {
      if (!uniqueSets[groupKey]) uniqueSets[groupKey] = new Set()
      if (r.container_name) uniqueSets[groupKey].add(r.container_name)
    } else if (countBy === 'eol_issues') {
      if (r.eol_date && new Date(r.eol_date).getTime() < now.getTime()) {
        stats[groupKey] = (stats[groupKey] || 0) + 1
      }
    } else if (countBy === 'security_risks') {
      const tw: any = (r.security_info || {}).twistlock
      if ((tw?.riskFactorCount || 0) > 0) {
        stats[groupKey] = (stats[groupKey] || 0) + 1
      }
    } else if (countBy === 'high_severity') {
      const tw: any = (r.security_info || {}).twistlock
      const dist = tw?.vulnerabilityDistribution || {}
      const highCount = dist.high || 0
      if (highCount > 0) {
        stats[groupKey] = (stats[groupKey] || 0) + highCount
      }
    } else if (countBy === 'critical_severity') {
      const tw: any = (r.security_info || {}).twistlock
      const dist = tw?.vulnerabilityDistribution || {}
      const critCount = dist.critical || 0
      if (critCount > 0) {
        stats[groupKey] = (stats[groupKey] || 0) + critCount
      }
    } else if (countBy === 'missing_plans') {
      if (!r.planned_upgrade_at) {
        stats[groupKey] = (stats[groupKey] || 0) + 1
      }
    } else if (countBy === 'with_notes') {
      if (r.note) {
        stats[groupKey] = (stats[groupKey] || 0) + 1
      }
    }
  })
  
  // Convert sets to counts
  Object.keys(uniqueSets).forEach(key => {
    stats[key] = uniqueSets[key].size
  })
  
  return stats
}

