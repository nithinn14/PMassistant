import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CalendarDays, CheckCircle2, XCircle, Clock, HelpCircle, RefreshCw } from 'lucide-react'
import { getProjectResults, syncMeetings } from '../services/api'

function Badge({ children, color = 'gray' }) {
    const styles = {
        green: 'bg-accent-emerald/10 text-accent-emerald border-accent-emerald/15',
        orange: 'bg-accent-amber/10 text-accent-amber border-accent-amber/15',
        indigo: 'bg-primary-500/10 text-primary-500 border-primary-500/15',
        gray: 'bg-surface-100 text-surface-500 dark:bg-surface-800 dark:text-surface-400 border-surface-200 dark:border-surface-700',
    }
    return (
        <span className={`inline-flex items-center px-2.5 py-1 rounded-lg text-[10px] font-bold tracking-wide border ${styles[color]}`}>
            {children}
        </span>
    )
}

export default function MeetingsPage({ projectCtx }) {
    const [searchParams, setSearchParams] = useSearchParams()
    const projectParam = searchParams.get('project')
    const activeProject = projectParam || projectCtx?.projectName || ''

    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [ambiguousMatches, setAmbiguousMatches] = useState([])
    const [syncing, setSyncing] = useState(false)
    const [projectInput, setProjectInput] = useState(activeProject)

    async function loadData(name) {
        if (!name) return
        setLoading(true)
        setError(null)
        setAmbiguousMatches([])
        try {
            const res = await getProjectResults(name)
            if (res.ambiguous) {
                setAmbiguousMatches(res.matches || [])
                setData(null)
            } else {
                setData(res)
                if (res.project_name && res.project_name !== activeProject) {
                    setSearchParams({ project: res.project_name })
                    setProjectInput(res.project_name)
                }
            }
        } catch (err) {
            const msg = err.response?.data?.error || `No project found matching "${name}". Check the Projects page for available projects.`
            setError(msg)
            setData(null)
        } finally {
            setLoading(false)
        }
    }

    async function handleSync() {
        if (!data?.project_name || syncing) return
        setSyncing(true)
        try {
            const meetingType = data.meeting_summary?.meeting_type || 'Kickoff'
            const updatedData = await syncMeetings(data.project_name, meetingType)
            setData(updatedData)
        } catch { alert('Failed to sync meetings.') }
        setSyncing(false)
    }

    useEffect(() => {
        if (activeProject) {
            setProjectInput(activeProject)
            if (data?.project_name !== activeProject) {
                loadData(activeProject)
            }
        } else {
            setData(null)
            setError(null)
            setAmbiguousMatches([])
            setLoading(false)
        }
    }, [activeProject])

    function handleManualLoad(name) {
        if (!name?.trim()) return
        const trimmed = name.trim()
        if (projectParam !== trimmed) {
            setSearchParams({ project: trimmed })
        } else {
            loadData(trimmed)
        }
    }

    if (!loading && !data) {
        return (
            <div className="empty-state animate-fade-in-up">
                <div className="empty-state-icon bg-gradient-to-br from-accent-amber/10 to-accent-rose/10">
                    <CalendarDays className="w-9 h-9 text-accent-amber" />
                </div>
                <h2 className="text-2xl font-black tracking-tight mb-2">Meetings</h2>
                <p className="text-surface-500 text-sm mb-6 max-w-sm leading-relaxed">
                    Enter a project name to view meeting status and RSVPs.
                </p>
                <div className="project-input-group">
                    <input type="text" placeholder="Project name..."
                        value={projectInput} onChange={(e) => setProjectInput(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleManualLoad(projectInput)}
                        className="border border-surface-200/80 dark:border-surface-700/50 bg-surface-50/50 dark:bg-surface-800/40 focus:outline-none focus:ring-2 focus:ring-primary-500/30"
                    />
                    <button onClick={() => handleManualLoad(projectInput)}
                        className="bg-gradient-to-r from-primary-600 to-accent-violet text-white shadow-xl shadow-primary-500/20 hover:shadow-primary-500/35">
                        Load
                    </button>
                </div>
                {error && (
                    <div className="mt-4 p-3.5 rounded-xl bg-accent-rose/10 border border-accent-rose/20 text-accent-rose text-sm font-semibold text-center w-full max-w-md animate-fade-in-up">
                        {error}
                    </div>
                )}
                {ambiguousMatches.length > 0 && (
                    <div className="mt-6 w-full max-w-md text-left animate-fade-in-up">
                        <p className="text-xs font-semibold uppercase tracking-wider text-surface-400 mb-2.5">
                            Multiple projects match "{projectInput || activeProject}". Please select one:
                        </p>
                        <div className="flex flex-col gap-2 max-h-56 overflow-y-auto pr-1">
                            {ambiguousMatches.map((name) => (
                                <button
                                    key={name}
                                    type="button"
                                    onClick={() => handleManualLoad(name)}
                                    className="flex items-center justify-between w-full px-4 py-3 text-sm font-medium text-surface-700 dark:text-surface-200 bg-surface-100/70 hover:bg-primary-50 dark:bg-surface-800/60 dark:hover:bg-primary-950/40 border border-surface-200/80 dark:border-surface-700/60 hover:border-primary-500/40 rounded-xl transition-all text-left group"
                                >
                                    <span className="truncate group-hover:text-primary-600 dark:group-hover:text-primary-400 transition-colors">
                                        {name}
                                    </span>
                                    <span className="text-xs text-surface-400 group-hover:text-primary-500 transition-colors font-semibold ml-2 shrink-0">
                                        Select →
                                    </span>
                                </button>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        )
    }

    if (loading) {
        return (
            <div className="min-h-screen flex items-center justify-center">
                <div className="w-10 h-10 border-3 border-primary-500/20 border-t-primary-500 rounded-full animate-spin" />
            </div>
        )
    }

    const ms = data?.meeting_summary
    const participants = data?.participants || []

    return (
        <div className="page-container space-y-6">
            <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 animate-fade-in-up">
                <div>
                    <p className="page-subtitle">Meetings</p>
                    <h1 className="page-title gradient-text">{data.project_name}</h1>
                </div>
                <button onClick={handleSync} disabled={syncing}
                    className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold transition-all ${syncing
                        ? 'bg-surface-800 text-surface-500 cursor-not-allowed'
                        : 'bg-primary-500/10 text-primary-500 hover:bg-primary-500/20 border border-primary-500/15'}`}>
                    <RefreshCw className={`w-4 h-4 ${syncing ? 'animate-spin' : ''}`} />
                    {syncing ? 'Syncing...' : 'Sync RSVPs'}
                </button>
            </div>

            {/* RSVP Stat Cards */}
            {ms && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 animate-fade-in-up" style={{ animationDelay: '100ms' }}>
                    {[
                        { label: 'Accepted', value: ms.Accepted, icon: CheckCircle2, gradient: 'from-accent-emerald to-teal-500', glow: 'glow-emerald' },
                        { label: 'Declined', value: ms.Declined, icon: XCircle, gradient: 'from-accent-rose to-red-500', glow: 'glow-rose' },
                        { label: 'Tentative', value: ms.Tentative, icon: HelpCircle, gradient: 'from-accent-amber to-orange-500', glow: '' },
                        { label: 'Awaiting', value: ms.Awaiting, icon: Clock, gradient: 'from-accent-cyan to-blue-500', glow: 'glow-cyan' },
                    ].map(({ label, value, icon: StatIcon, gradient, glow }) => (
                        <div key={label} className={`stat-card glass-card text-center ${glow}`}>
                            <div className={`inline-flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br ${gradient} shadow-lg mb-2`}>
                                <StatIcon className="w-5 h-5 text-white" />
                            </div>
                            <p className="text-3xl font-black tracking-tight">{value ?? 0}</p>
                            <p className="text-[10px] font-bold text-surface-400 mt-1 uppercase tracking-wider">{label}</p>
                        </div>
                    ))}
                </div>
            )}

            {/* Participants Table */}
            {participants.length > 0 && (
                <div className="glass-card dash-card animate-fade-in-up" style={{ animationDelay: '200ms' }}>
                    <h2 className="text-base font-extrabold tracking-tight mb-4">Participants</h2>
                    <div className="overflow-x-auto rounded-2xl border border-surface-200/40 dark:border-surface-700/30">
                        <table className="premium-table">
                            <thead>
                                <tr className="bg-surface-50/80 dark:bg-surface-800/40 text-surface-400">
                                    <th className="text-left rounded-tl-2xl">Email</th>
                                    <th className="text-left">Response</th>
                                    <th className="text-left rounded-tr-2xl">Meeting ID</th>
                                </tr>
                            </thead>
                            <tbody>
                                {participants.map((p, i) => {
                                    const status = (p.RSVP_Status || p.Response || 'Awaiting').toLowerCase()
                                    const color = status === 'accepted' ? 'green' : status === 'declined' ? 'orange' : 'gray'
                                    return (
                                        <tr key={i}>
                                            <td className="text-sm font-medium">{p.Email}</td>
                                            <td><Badge color={color}>{status.toUpperCase()}</Badge></td>
                                            <td className="text-xs text-surface-400 font-mono">{p.Meeting_ID || '—'}</td>
                                        </tr>
                                    )
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    )
}
