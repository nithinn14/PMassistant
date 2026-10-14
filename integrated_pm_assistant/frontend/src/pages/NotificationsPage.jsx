import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Bell, CheckCircle2, XCircle } from 'lucide-react'
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
    const [view, setView] = useState('all')

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
                    Enter a project name to view notification delivery status.
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

    const ns = data?.notification_status || null
    const toItems = (names, channel) => (names || []).map((name) => ({ name, channel }))

    const toneStyles = {
        emerald: {
            card: 'bg-accent-emerald/5 border-accent-emerald/10 hover:bg-accent-emerald/10',
            text: 'text-accent-emerald',
        },
        amber: {
            card: 'bg-accent-amber/5 border-accent-amber/10 hover:bg-accent-amber/10',
            text: 'text-accent-amber',
        },
        rose: {
            card: 'bg-accent-rose/5 border-accent-rose/10 hover:bg-accent-rose/10',
            text: 'text-accent-rose',
        },
    }

    const sections = ns ? [
        { key: 'both', title: 'Notified on both channels', items: toItems(ns.both, 'Telegram + Email'), Icon: CheckCircle2, tone: 'emerald' },
        { key: 'single', title: 'Notified on one channel', items: [...toItems(ns.telegram_only, 'Telegram only'), ...toItems(ns.email_only, 'Email only')], Icon: CheckCircle2, tone: 'amber' },
        { key: 'not_reached', title: 'Not reached', items: toItems(ns.not_reached, 'No successful delivery'), Icon: XCircle, tone: 'rose' },
    ] : []

    const tabs = ns ? [
        { key: 'all', label: 'All', count: ns.total },
        { key: 'both', label: 'Both channels', count: sections[0].items.length },
        { key: 'single', label: 'One channel', count: sections[1].items.length },
        { key: 'not_reached', label: 'Not reached', count: sections[2].items.length },
    ] : []

    const visibleSections = sections.filter(
        (s) => (view === 'all' || view === s.key) && s.items.length > 0
    )

    return (
        <div className="page-container space-y-6">
            <div className="page-header animate-fade-in-up">
                <p className="page-subtitle">Notifications</p>
                <h1 className="page-title gradient-text">{data.project_name}</h1>
                {ns && (
                    <p className="text-surface-500 text-sm font-semibold mt-1">
                        {ns.notified} of {ns.total} notified ({ns.email_count} via Email, {ns.telegram_count} via Telegram, {(ns.not_reached || []).length} not reached)
                    </p>
                )}
            </div>

            {!ns ? (
                <div className="glass-card dash-card text-center py-16">
                    <p className="text-surface-500 text-sm font-semibold">
                        Delivery tracking isn't available for this project. Run it again to see real notification results.
                    </p>
                </div>
            ) : (
                <>
                    <div className="flex flex-wrap gap-2 animate-fade-in-up">
                        {tabs.map((t) => (
                            <button
                                key={t.key}
                                type="button"
                                onClick={() => setView(t.key)}
                                className={`px-4 py-2 rounded-xl text-sm font-semibold border transition-all ${
                                    view === t.key
                                        ? 'bg-primary-500/10 border-primary-500/30 text-primary-600 dark:text-primary-400'
                                        : 'border-surface-200/80 dark:border-surface-700/50 text-surface-500 hover:border-primary-500/30'
                                }`}
                            >
                                {t.label} <span className="ml-1 opacity-70">{t.count}</span>
                            </button>
                        ))}
                    </div>

                    {visibleSections.length === 0 ? (
                        <div className="glass-card dash-card text-center py-16">
                            <p className="text-surface-500 text-sm font-semibold">Nobody in this group.</p>
                        </div>
                    ) : (
                        visibleSections.map((section, si) => {
                            const tone = toneStyles[section.tone]
                            const Icon = section.Icon
                            return (
                                <div
                                    key={section.key}
                                    className="glass-card dash-card animate-fade-in-up"
                                    style={{ animationDelay: `${si * 80}ms` }}
                                >
                                    <p className={`text-xs font-bold uppercase tracking-wider mb-3 ${tone.text}`}>
                                        {section.title} ({section.items.length})
                                    </p>
                                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                                        {section.items.map((item) => (
                                            <div
                                                key={item.name}
                                                className={`flex items-center gap-3 px-4 py-3 rounded-xl border transition-colors ${tone.card}`}
                                            >
                                                <Icon className={`w-4 h-4 shrink-0 ${tone.text}`} />
                                                <div className="min-w-0">
                                                    <p className={`text-sm font-semibold truncate ${tone.text}`}>{item.name}</p>
                                                    <p className="text-[10px] text-surface-400 font-medium truncate">{item.channel}</p>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )
                        })
                    )}
                </>
            )}
        </div>
    )
}