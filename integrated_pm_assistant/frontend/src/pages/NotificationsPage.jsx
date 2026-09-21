import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Bell, CheckCircle2 } from 'lucide-react'
import { getProjectResults } from '../services/api'

export default function NotificationsPage({ projectCtx }) {
    const [searchParams, setSearchParams] = useSearchParams()
    const projectParam = searchParams.get('project')
    const activeProject = projectParam || projectCtx?.projectName || ''

    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [ambiguousMatches, setAmbiguousMatches] = useState([])
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
                <div className="empty-state-icon bg-gradient-to-br from-accent-emerald/10 to-accent-cyan/10">
                    <Bell className="w-9 h-9 text-accent-emerald" />
                </div>
                <h2 className="text-2xl font-black tracking-tight mb-2">Notifications</h2>
                <p className="text-surface-500 text-sm mb-6 max-w-sm leading-relaxed">
                    Enter a project name to view Telegram notifications sent.
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

    const notifications = data?.telegram_notifications || []

    return (
        <div className="page-container space-y-6">
            <div className="page-header animate-fade-in-up">
                <p className="page-subtitle">Notifications</p>
                <h1 className="page-title gradient-text">{data.project_name}</h1>
                <p className="text-surface-500 text-sm font-semibold mt-1">{notifications.length} notifications sent via Telegram</p>
            </div>

            {notifications.length > 0 ? (
                <div className="glass-card dash-card animate-fade-in-up" style={{ animationDelay: '100ms' }}>
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                        {notifications.map((name, i) => (
                            <div
                                key={i}
                                className="flex items-center gap-3 px-4 py-3 rounded-xl bg-accent-emerald/5 border border-accent-emerald/10 hover:bg-accent-emerald/10 transition-colors"
                            >
                                <CheckCircle2 className="w-4 h-4 text-accent-emerald shrink-0" />
                                <span className="text-sm font-semibold text-accent-emerald truncate">{name}</span>
                            </div>
                        ))}
                    </div>
                </div>
            ) : (
                <div className="glass-card dash-card text-center py-16">
                    <p className="text-surface-500 text-sm font-semibold">No notifications recorded for this project.</p>
                </div>
            )}
        </div>
    )
}
