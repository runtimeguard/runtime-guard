import React, { useEffect, useMemo, useRef, useState } from 'react'

const API_BASE = 'http://127.0.0.1:5001'
const RAIL_ITEMS = [
  { id: 'approvals', label: 'Approvals', icon: '🔔' },
  { id: 'commands', label: 'Commands', icon: '🛡️' },
  { id: 'reports', label: 'Reports', icon: '📊' },
  { id: 'settings', label: 'Settings', icon: '⚙️' }
]
const COMMAND_TABS = ['all', 'macos', 'linux', 'github', 'email', 'network']
const TAB_LABELS = {
  all: 'All Commands',
  macos: 'macOS',
  linux: 'Linux',
  github: 'GitHub/Git',
  email: 'Email/Notify',
  network: 'Network'
}
const COLUMN_DEFS = [
  { key: 'allowed', label: 'Allowed' },
  { key: 'requires_simulation', label: 'Simulation' },
  { key: 'requires_confirmation', label: 'Requires Approval' },
  { key: 'blocked', label: 'Blocked' }
]

const STATUS_STYLE = {
  allowed: 'bg-green-100 text-green-700 border-green-200',
  requires_simulation: 'bg-blue-100 text-blue-700 border-blue-200',
  requires_confirmation: 'bg-amber-100 text-amber-700 border-amber-200',
  blocked: 'bg-red-100 text-red-700 border-red-200'
}

const STATUS_LABEL = {
  allowed: 'Allowed',
  requires_simulation: 'Simulation',
  requires_confirmation: 'Requires Approval',
  blocked: 'Blocked'
}

function deepClone(v) {
  return JSON.parse(JSON.stringify(v))
}

function tierFor(policy, cmd) {
  if ((policy?.blocked?.commands || []).includes(cmd)) return 'blocked'
  if ((policy?.requires_confirmation?.commands || []).includes(cmd)) return 'requires_confirmation'
  if ((policy?.requires_simulation?.commands || []).includes(cmd)) return 'requires_simulation'
  return 'allowed'
}

function setTier(policy, cmd, tier) {
  const next = deepClone(policy)
  const remove = (arr = []) => arr.filter((x) => x !== cmd)
  next.blocked.commands = remove(next.blocked?.commands)
  next.requires_confirmation.commands = remove(next.requires_confirmation?.commands)
  next.requires_simulation.commands = remove(next.requires_simulation?.commands)
  if (tier === 'blocked') next.blocked.commands.push(cmd)
  if (tier === 'requires_confirmation') next.requires_confirmation.commands.push(cmd)
  if (tier === 'requires_simulation') next.requires_simulation.commands.push(cmd)
  return next
}

function getOverride(policy, cmd) {
  return policy?.ui_overrides?.commands?.[cmd] || {}
}

function setOverride(policy, cmd, patch) {
  const next = deepClone(policy)
  next.ui_overrides = next.ui_overrides || {}
  next.ui_overrides.commands = next.ui_overrides.commands || {}
  const merged = { ...(next.ui_overrides.commands[cmd] || {}), ...patch }
  if (merged.retry_override === undefined) delete merged.retry_override
  if (!merged.budget || Object.keys(merged.budget).length === 0) delete merged.budget
  if (!merged.retry_override && !merged.budget) delete next.ui_overrides.commands[cmd]
  else next.ui_overrides.commands[cmd] = merged
  return next
}

function relativeTime(iso) {
  try {
    const d = new Date(iso)
    const sec = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000))
    if (sec < 60) return `${sec} seconds ago`
    const min = Math.floor(sec / 60)
    if (min < 60) return `${min} minute${min === 1 ? '' : 's'} ago`
    const hr = Math.floor(min / 60)
    return `${hr} hour${hr === 1 ? '' : 's'} ago`
  } catch {
    return iso
  }
}

export default function App() {
  const [activeRail, setActiveRail] = useState('approvals')
  const [activeTab, setActiveTab] = useState('all')
  const [policyHash, setPolicyHash] = useState('')
  const [appliedPolicy, setAppliedPolicy] = useState(null)
  const [draftPolicy, setDraftPolicy] = useState(null)
  const [search, setSearch] = useState('')
  const [jsonOpen, setJsonOpen] = useState(false)
  const [jsonText, setJsonText] = useState('')
  const [jsonError, setJsonError] = useState('')
  const [message, setMessage] = useState('')
  const [pendingApprovals, setPendingApprovals] = useState([])
  const [descriptions, setDescriptions] = useState({})
  const [contexts, setContexts] = useState({})
  const [allCommands, setAllCommands] = useState([])
  const [removing, setRemoving] = useState({})
  const [loaded, setLoaded] = useState(false)
  const pollRef = useRef(null)

  const unsaved = useMemo(() => {
    if (!appliedPolicy || !draftPolicy) return false
    return JSON.stringify(appliedPolicy) !== JSON.stringify(draftPolicy)
  }, [appliedPolicy, draftPolicy])

  async function fetchPolicy() {
    const res = await fetch(`${API_BASE}/policy`)
    if (!res.ok) throw new Error(`Policy load failed (${res.status})`)
    const payload = await res.json()
    setPolicyHash(payload.hash || '')
    setAppliedPolicy(payload.policy)
    setDraftPolicy(deepClone(payload.policy))
    setJsonText(JSON.stringify(payload.policy, null, 2))
    setDescriptions(payload.descriptions || {})
    setContexts(payload.contexts || {})
    setAllCommands(payload.all_commands || [])
    setJsonError('')
    setLoaded(true)
  }

  async function fetchApprovals() {
    const res = await fetch(`${API_BASE}/approvals/pending`)
    if (!res.ok) return
    const payload = await res.json()
    setPendingApprovals(payload.pending || [])
  }

  useEffect(() => {
    // Poll pending approvals every 3 seconds so operator actions/agent requests
    // from other processes appear without manual refresh.
    fetchPolicy().catch((err) => setMessage(err.message))
    fetchApprovals()
    pollRef.current = setInterval(fetchApprovals, 3000)
    return () => clearInterval(pollRef.current)
  }, [])

  useEffect(() => {
    // Table edits are source-of-truth while editing. Keep JSON textarea in sync
    // with the draft policy state to support dual editing surfaces.
    if (draftPolicy) {
      setJsonText(JSON.stringify(draftPolicy, null, 2))
    }
  }, [draftPolicy])

  const commandRows = useMemo(() => {
    const base = activeTab === 'all'
      ? allCommands
      : allCommands.filter((cmd) => (contexts[cmd] || []).map((c) => c.toLowerCase()).includes(TAB_LABELS[activeTab].toLowerCase()))
    const q = search.trim().toLowerCase()
    return base.filter((cmd) => !q || cmd.toLowerCase().includes(q))
  }, [allCommands, activeTab, contexts, search])

  function onJsonChange(next) {
    setJsonText(next)
    try {
      // Bidirectional sync: valid JSON edits replace table-backed draft state.
      // Invalid JSON is tolerated in textarea without destroying current table state.
      const parsed = JSON.parse(next)
      setDraftPolicy(parsed)
      setJsonError('')
    } catch (e) {
      setJsonError('Invalid JSON. Table state remains unchanged until valid parse.')
    }
  }

  async function onValidate() {
    if (!draftPolicy) return
    const res = await fetch(`${API_BASE}/policy/validate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ policy: draftPolicy })
    })
    const payload = await res.json()
    if (res.ok) setMessage('Validation passed')
    else setMessage(payload.error || 'Validation failed')
  }

  async function onApply() {
    if (!draftPolicy) return
    const res = await fetch(`${API_BASE}/policy/apply`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Actor': 'control-plane-v3' }, body: JSON.stringify({ policy: draftPolicy })
    })
    const payload = await res.json()
    if (!res.ok) {
      setMessage(payload.error || 'Apply failed')
      return
    }
    await fetchPolicy()
    setMessage('Policy applied')
  }

  async function onReload() {
    if (unsaved && !window.confirm('Discard unsaved edits and reload from backend?')) return
    await fetchPolicy()
    setMessage('Reloaded latest policy')
  }

  async function approve(token, command) {
    const res = await fetch(`${API_BASE}/approvals/approve`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token, command })
    })
    if (!res.ok) {
      const payload = await res.json().catch(() => ({}))
      setMessage(payload.error || `Approve failed (${res.status})`)
      return
    }
    setRemoving((r) => ({ ...r, [token]: true }))
    setTimeout(() => setPendingApprovals((prev) => prev.filter((p) => p.token !== token)), 180)
  }

  async function deny(token) {
    const res = await fetch(`${API_BASE}/approvals/deny`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token })
    })
    if (!res.ok) {
      const payload = await res.json().catch(() => ({}))
      setMessage(payload.error || `Deny failed (${res.status})`)
      return
    }
    setRemoving((r) => ({ ...r, [token]: true }))
    setTimeout(() => setPendingApprovals((prev) => prev.filter((p) => p.token !== token)), 180)
  }

  function renderRailItem(item) {
    const isActive = activeRail === item.id
    const pending = item.id === 'approvals' ? pendingApprovals.length : 0
    return (
      <button
        key={item.id}
        onClick={() => setActiveRail(item.id)}
        className={`relative flex flex-col items-center justify-center gap-1 py-3 rounded-xl text-xs transition border ${
          isActive ? 'bg-brand text-white border-brand' : 'bg-white text-slate-700 border-slate-200 hover:border-brand/40'
        } ${pending > 0 && item.id === 'approvals' ? 'ring-1 ring-amber-300 bg-amber-50 text-amber-700' : ''}`}
      >
        <span className="text-base">{item.icon}</span>
        <span>{item.label}</span>
        {pending > 0 && (
          <span className="absolute -top-1 -right-1 text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500 text-white">{pending}</span>
        )}
      </button>
    )
  }

  function CommandRow({ cmd }) {
    const currentTier = tierFor(draftPolicy, cmd)
    const appliedTier = tierFor(appliedPolicy, cmd)
    const applied = getOverride(appliedPolicy, cmd)
    const draftOverride = getOverride(draftPolicy, cmd)
    const contextList = (contexts[cmd] || []).join(', ') || 'Unmapped'
    const allowAdvanced = currentTier !== 'allowed'

    const onRetry = (value) => {
      const next = value === '' ? undefined : Math.max(0, Math.min(10, parseInt(value, 10) || 0))
      setDraftPolicy((p) => setOverride(p, cmd, { retry_override: next }))
    }

    const onBudget = (field, value) => {
      const existing = { ...(draftOverride.budget || {}) }
      if (value === '') delete existing[field]
      else existing[field] = Math.max(0, parseInt(value, 10) || 0)
      setDraftPolicy((p) => setOverride(p, cmd, { budget: Object.keys(existing).length ? existing : undefined }))
    }

    return (
      <div className="grid grid-cols-[minmax(320px,1fr)_repeat(4,90px)_80px_100px_110px_120px] gap-2 items-center border-b border-slate-200 py-2 text-sm">
        <div>
          <div className="font-semibold text-slate-800 flex items-center gap-2">
            <span className="font-mono">{cmd}</span>
            <span className="text-slate-400 cursor-help" title={descriptions[cmd] || 'No description available'}>ⓘ</span>
            <span className={`px-2 py-0.5 rounded-full border text-xs ${STATUS_STYLE[appliedTier]}`}>{STATUS_LABEL[appliedTier]}</span>
            {applied.retry_override !== undefined && <span className="text-xs px-2 py-0.5 border rounded-full text-slate-600">Retry {applied.retry_override}</span>}
            {applied.budget && <span className="text-xs px-2 py-0.5 border rounded-full text-slate-600">Budget set</span>}
          </div>
          <div className="text-xs text-slate-400">{contextList}</div>
        </div>
        {COLUMN_DEFS.map((col) => (
          <label key={col.key} className="flex justify-center">
            <input
              type="radio"
              name={`tier-${cmd}`}
              checked={currentTier === col.key}
              onChange={() => setDraftPolicy((p) => setTier(p, cmd, col.key))}
            />
          </label>
        ))}
        <input
          type="number"
          min={0}
          max={10}
          placeholder="-"
          disabled={!allowAdvanced}
          title={!allowAdvanced ? 'Retry override is not relevant when command is Allowed' : 'Per-command metadata (runtime enforcement pending)'}
          className="border rounded px-2 py-1 disabled:bg-slate-100 disabled:text-slate-400"
          value={draftOverride.retry_override ?? ''}
          onChange={(e) => onRetry(e.target.value)}
        />
        <input
          type="number"
          min={0}
          placeholder="-"
          disabled={!allowAdvanced}
          title={!allowAdvanced ? 'Budget metadata disabled when command is Allowed' : 'Per-command metadata (runtime enforcement pending)'}
          className="border rounded px-2 py-1 disabled:bg-slate-100 disabled:text-slate-400"
          value={draftOverride?.budget?.max_ops_per_session ?? ''}
          onChange={(e) => onBudget('max_ops_per_session', e.target.value)}
        />
        <input
          type="number"
          min={0}
          placeholder="-"
          disabled={!allowAdvanced}
          className="border rounded px-2 py-1 disabled:bg-slate-100 disabled:text-slate-400"
          value={draftOverride?.budget?.max_unique_paths_per_session ?? ''}
          onChange={(e) => onBudget('max_unique_paths_per_session', e.target.value)}
        />
        <input
          type="number"
          min={0}
          placeholder="-"
          disabled={!allowAdvanced}
          className="border rounded px-2 py-1 disabled:bg-slate-100 disabled:text-slate-400"
          value={draftOverride?.budget?.max_bytes_per_session ?? ''}
          onChange={(e) => onBudget('max_bytes_per_session', e.target.value)}
        />
      </div>
    )
  }

  function ApprovalsPanel() {
    if (!pendingApprovals.length) {
      return (
        <div className="bg-white rounded-xl border border-slate-200 p-8 text-center shadow-sm">
          <div className="text-3xl mb-2">🔔</div>
          <div className="text-green-700 font-semibold">No pending approvals</div>
        </div>
      )
    }
    return (
      <div className="space-y-3">
        <div className="text-slate-700 font-medium">{pendingApprovals.length} command(s) awaiting approval</div>
        {pendingApprovals.map((item) => {
          const urgency = item.seconds_remaining < 60
          return (
            <div
              key={item.token}
              className={`bg-white border-l-4 ${urgency ? 'border-red-400' : 'border-amber-400'} rounded-xl border border-slate-200 p-4 shadow-sm transition-all duration-200 ${removing[item.token] ? 'opacity-0 -translate-y-1' : 'opacity-100 translate-y-0'}`}
            >
              <div className="font-mono text-base font-semibold text-slate-800">{item.command}</div>
              <div className="text-xs text-slate-500 mt-1">Requested {relativeTime(item.requested_at)} • session <span className="font-mono">{item.session_id || 'n/a'}</span></div>
              <div className={`text-xs mt-1 ${urgency ? 'text-red-600 font-semibold' : 'text-slate-500'}`}>Expires in {item.seconds_remaining}s</div>
              {item.affected_paths?.length > 0 && (
                <details className="mt-2 text-sm">
                  <summary className="cursor-pointer text-slate-600">Affected paths ({item.affected_paths.length})</summary>
                  <ul className="mt-2 text-xs font-mono bg-slate-50 rounded p-2 border border-slate-200 max-h-32 overflow-auto">
                    {item.affected_paths.map((p) => <li key={p}>{p}</li>)}
                  </ul>
                </details>
              )}
              <div className="mt-3 flex gap-2">
                <button onClick={() => approve(item.token, item.command)} className="px-3 py-1.5 rounded-lg bg-green-600 text-white text-sm font-medium">Approve</button>
                <button onClick={() => deny(item.token)} className="px-3 py-1.5 rounded-lg border border-red-300 text-red-700 text-sm">Deny</button>
              </div>
            </div>
          )
        })}
      </div>
    )
  }

  function CommandsPanel() {
    return (
      <>
        <div className="flex items-center justify-between mb-3">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full border border-slate-300 rounded-lg px-3 py-2 bg-white"
            placeholder="Filter commands..."
          />
        </div>
        <div className="bg-white border border-slate-200 rounded-xl p-3 shadow-sm overflow-auto">
          <div className="grid grid-cols-[minmax(320px,1fr)_repeat(4,90px)_80px_100px_110px_120px] gap-2 text-xs font-semibold text-slate-500 border-b border-slate-200 pb-2">
            <div>Command</div>
            <div className="text-center">Allowed</div>
            <div className="text-center">Simulation</div>
            <div className="text-center">Requires Approval</div>
            <div className="text-center">Blocked</div>
            <div className="text-center">Retry</div>
            <div className="text-center">Budget Ops</div>
            <div className="text-center">Budget Paths</div>
            <div className="text-center">Budget Bytes</div>
          </div>
          {commandRows.map((cmd) => <CommandRow key={cmd} cmd={cmd} />)}
        </div>
        <div className="mt-4 bg-white border border-slate-200 rounded-xl p-3 shadow-sm">
          <button onClick={() => setJsonOpen((x) => !x)} className="text-sm font-medium text-slate-700">
            {jsonOpen ? '▾' : '▸'} Advanced JSON
          </button>
          {jsonOpen && (
            <textarea
              value={jsonText}
              onChange={(e) => onJsonChange(e.target.value)}
              className="mt-3 w-full h-72 border border-slate-300 rounded-lg p-3 font-mono text-xs"
            />
          )}
          {jsonError && <div className="mt-2 text-sm text-red-600">{jsonError}</div>}
          {message && <div className="mt-2 text-sm text-slate-700">{message}</div>}
          <div className="mt-4 flex gap-2">
            <button onClick={onReload} className="px-4 py-2 rounded-lg border border-slate-300 bg-white text-slate-700">Reload</button>
            <button onClick={onValidate} className="px-4 py-2 rounded-lg bg-blue-600 text-white">Validate</button>
            <button onClick={onApply} className="px-4 py-2 rounded-lg bg-brand text-white">Apply</button>
          </div>
        </div>
      </>
    )
  }

  return (
    <div className="min-h-screen bg-warmbg text-slate-800 font-[system-ui]">
      <div className="border-b border-slate-200 bg-white/80 backdrop-blur px-5 py-4 sticky top-0 z-10">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold">Policy Control Plane</h1>
            <div className="text-xs text-slate-500 mt-1">Policy hash: <span className="font-mono">{policyHash || '-'}</span></div>
          </div>
          <div className="flex items-center gap-2">
            {['allowed', 'requires_simulation', 'requires_confirmation', 'blocked'].map((k) => (
              <span key={k} className={`px-2 py-0.5 rounded-full border text-xs ${STATUS_STYLE[k]}`}>{STATUS_LABEL[k]}</span>
            ))}
            <span className="text-xs italic text-slate-500">Status badges reflect applied policy (after Apply), not unsaved edits</span>
            {unsaved && <span className="text-xs text-amber-700 font-medium flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-500" /> Unsaved changes</span>}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-[74px_170px_1fr] gap-0 min-h-[calc(100vh-84px)]">
        <nav className="border-r border-slate-200 p-2 bg-white">
          <div className="space-y-2">
            {renderRailItem(RAIL_ITEMS[0])}
          </div>
          <div className="my-3 border-t border-slate-200" />
          <div className="space-y-2">
            {RAIL_ITEMS.slice(1).map(renderRailItem)}
          </div>
        </nav>

        {activeRail === 'commands' ? (
          <aside className="border-r border-slate-200 bg-white p-3 space-y-2">
            {COMMAND_TABS.map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`w-full text-left px-3 py-2 rounded-lg text-sm ${activeTab === tab ? 'bg-brand text-white' : 'text-slate-700 hover:bg-slate-100'}`}
              >
                {TAB_LABELS[tab]}
              </button>
            ))}
          </aside>
        ) : (
          <aside className="border-r border-slate-200 bg-white p-3" />
        )}

        <main className="p-4">
          {!loaded && <div className="text-slate-500">Loading...</div>}
          {loaded && activeRail === 'approvals' && <ApprovalsPanel />}
          {loaded && activeRail === 'commands' && <CommandsPanel />}
          {loaded && (activeRail === 'reports' || activeRail === 'settings') && (
            <div className="bg-white border border-slate-200 rounded-xl p-6 shadow-sm text-slate-500">Coming soon</div>
          )}
        </main>
      </div>
    </div>
  )
}
