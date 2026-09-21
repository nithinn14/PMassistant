import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Users } from 'lucide-react'
import { getProjectResults } from '../services/api'

function Badge({ children, color = 'gray' }) {
    const styles = {
        violet: 'bg-accent-violet/10 text-accent-violet border-accent-violet/15',
        gray: 'bg-surface-100 text-surface-500 dark:bg-surface-800 dark:text-surface-400 border-surface-200 dark:border-surface-700',
    }
    return (
        <span className={`inline-flex items-center px-2.5 py-1 rounded-lg text-[10px] font-bold tracking-wide border ${styles[color]}`}>
            {children}
        </span>
    )
}

export default function ResourcesPage({ projectCtx }) {
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
                <div className="empty-state-icon bg-gradient-to-br from-accent-cyan/10 to-primary-500/10">
                    <Users className="w-9 h-9 text-accent-cyan" />
                </div>
                <h2 className="text-2xl font-black tracking-tight mb-2">Resources</h2>
                <p className="text-surface-500 text-sm mb-6 max-w-sm leading-relaxed">
                    Enter a project name to view resource assignments.
                </p>
                <div className="project-input-group">
                    <input
                        type="text" placeholder="Project name..."
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

    const assigned = data?.assigned || []
    const grouped = assigned.reduce((acc, t) => {
        const name = t.Assigned_Employee || t.assigned_empl || 'Unassigned'
        if (!acc[name]) acc[name] = { email: t.assigned_email || t.Email || '', tasks: [] }
        acc[name].tasks.push(t.task_name || t.task || 'Task')
        return acc
    }, {})

    return (
        <div className="page-container space-y-6">
            <div className="page-header animate-fade-in-up">
                <p className="page-subtitle">Resources</p>
                <h1 className="page-title gradient-text">{data.project_name}</h1>
                <p className="text-surface-500 text-sm font-semibold mt-1">{Object.keys(grouped).length} team members assigned</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {Object.entries(grouped).map(([name, info], i) => (
                    <div key={name} className="glass-card dash-card group animate-fade-in-up" style={{ animationDelay: `${i * 50}ms` }}>
                        <div className="flex items-center gap-3 mb-3">
                            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500 to-accent-violet flex items-center justify-center text-white text-sm font-black shadow-lg shadow-primary-500/15 group-hover:shadow-primary-500/30 transition-shadow">
                                {name.charAt(0).toUpperCase()}
                            </div>
                            <div className="flex-1 min-w-0">
                                <p className="text-sm font-bold truncate">{name}</p>
                                <p className="text-[10px] text-surface-400 font-medium truncate">{info.email || 'No email'}</p>
                            </div>
                            <Badge color="violet">{info.tasks.length}</Badge>
                        </div>
                        <ul className="space-y-1.5">
                            {info.tasks.map((t, j) => (
                                <li key={j} className="text-xs text-surface-500 dark:text-surface-400 flex items-start gap-2 leading-relaxed">
                                    <span className="text-primary-400 mt-0.5 shrink-0">▸</span> {t}
                                </li>
                            ))}
                        </ul>
                    </div>
                ))}
            </div>
        </div>
    )
}
