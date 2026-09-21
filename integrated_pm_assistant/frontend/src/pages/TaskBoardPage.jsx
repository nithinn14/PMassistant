import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ListChecks, Sparkles } from 'lucide-react'
import { getProjectResults } from '../services/api'

function Badge({ children, color = 'gray' }) {
    const styles = {
        green: 'bg-accent-emerald/10 text-accent-emerald border-accent-emerald/15',
        red: 'bg-accent-rose/10 text-accent-rose border-accent-rose/15',
        orange: 'bg-accent-amber/10 text-accent-amber border-accent-amber/15',
        blue: 'bg-accent-cyan/10 text-accent-cyan border-accent-cyan/15',
        violet: 'bg-accent-violet/10 text-accent-violet border-accent-violet/15',
        gray: 'bg-surface-100 text-surface-500 dark:bg-surface-800 dark:text-surface-400 border-surface-200 dark:border-surface-700',
    }
    return (
        <span className={`inline-flex items-center px-2.5 py-1 rounded-lg text-[10px] font-bold tracking-wide border ${styles[color]}`}>
            {children}
        </span>
    )
}

export default function TaskBoardPage({ projectCtx }) {
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

    const priorityColor = (p) => {
        const pl = String(p).toLowerCase()
        if (pl === 'high' || pl === 'critical') return 'red'
        if (pl === 'medium') return 'orange'
        return 'green'
    }

    if (!loading && !data) {
        return (
            <div className="empty-state animate-fade-in-up">
                <div className="empty-state-icon bg-gradient-to-br from-accent-violet/10 to-primary-500/10">
                    <ListChecks className="w-9 h-9 text-accent-violet" />
                </div>
                <h2 className="text-2xl font-black tracking-tight mb-2">Task Board</h2>
                <p className="text-surface-500 text-sm mb-6 max-w-sm leading-relaxed">
                    Enter a project name to view its generated tasks.
                </p>
                <div className="project-input-group">
                    <input
                        type="text"
                        placeholder="Project name..."
                        value={projectInput}
                        onChange={(e) => setProjectInput(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleManualLoad(projectInput)}
                        className="border border-surface-200/80 dark:border-surface-700/50 bg-surface-50/50 dark:bg-surface-800/40 focus:outline-none focus:ring-2 focus:ring-primary-500/30"
                    />
                    <button
                        onClick={() => handleManualLoad(projectInput)}
                        className="bg-gradient-to-r from-primary-600 to-accent-violet text-white shadow-xl shadow-primary-500/20 hover:shadow-primary-500/35"
                    >
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

    const tasks = data?.tasks || []

    return (
        <div className="page-container space-y-6">
            <div className="page-header animate-fade-in-up">
                <p className="page-subtitle">Task Board</p>
                <h1 className="page-title gradient-text">{data.project_name}</h1>
                <p className="text-surface-500 text-sm font-semibold mt-1">{tasks.length} tasks generated</p>
            </div>

            <div className="glass-card dash-card animate-fade-in-up" style={{ animationDelay: '100ms' }}>
                <div className="overflow-x-auto rounded-2xl border border-surface-200/40 dark:border-surface-700/30">
                    <table className="premium-table">
                        <thead>
                            <tr className="bg-surface-50/80 dark:bg-surface-800/40 text-surface-400">
                                <th className="text-left rounded-tl-2xl">ID</th>
                                <th className="text-left">Task Name</th>
                                <th className="text-left">Role</th>
                                <th className="text-left">Priority</th>
                                <th className="text-right">Days</th>
                                <th className="text-left rounded-tr-2xl">Dependencies</th>
                            </tr>
                        </thead>
                        <tbody>
                            {tasks.map((t, i) => (
                                <tr key={i}>
                                    <td className="font-mono text-xs text-surface-400 font-bold">{t.task_id}</td>
                                    <td className="font-semibold max-w-[280px]">{t.task_name}</td>
                                    <td className="text-surface-500 text-xs font-medium">{t.assigned_role}</td>
                                    <td><Badge color={priorityColor(t.priority)}>{t.priority}</Badge></td>
                                    <td className="text-right font-mono font-bold text-sm">{t.time_days}</td>
                                    <td className="text-surface-400 text-xs font-medium">{Array.isArray(t.dependencies) ? t.dependencies.join(', ') || '—' : t.dependencies || '—'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    )
}
