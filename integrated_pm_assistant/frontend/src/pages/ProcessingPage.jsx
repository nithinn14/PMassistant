import { useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
    FileText,
    ListChecks,
    Users,
    CalendarDays,
    Bell,
    Video,
    ClipboardCheck,
    CheckCircle2,
    Loader2,
    Sparkles,
    AlertTriangle,
} from 'lucide-react'
import { pollJobStatus } from '../services/api'

const STEPS = [
    { label: 'Generating PRD...', icon: FileText, color: 'from-primary-500 to-primary-600' },
    { label: 'PRD Generated', icon: FileText, color: 'from-primary-500 to-primary-600' },
    { label: 'Generating Tasks...', icon: ListChecks, color: 'from-accent-violet to-primary-500' },
    { label: 'Assigning Resources...', icon: Users, color: 'from-accent-cyan to-primary-400' },
    { label: 'Scheduling Tasks...', icon: CalendarDays, color: 'from-accent-emerald to-accent-cyan' },
    { label: 'Sending Notifications...', icon: Bell, color: 'from-accent-amber to-accent-rose' },
    { label: 'Creating Kickoff Meeting...', icon: Video, color: 'from-accent-rose to-accent-violet' },
    { label: 'Checking Meeting RSVPs...', icon: ClipboardCheck, color: 'from-accent-violet to-primary-500' },
    { label: 'Finalizing Execution...', icon: Sparkles, color: 'from-primary-500 to-accent-violet' },
    { label: 'Done', icon: CheckCircle2, color: 'from-accent-emerald to-accent-cyan' },
]

export default function ProcessingPage({ projectCtx, setProjectCtx }) {
    const navigate = useNavigate()
    const [completedSteps, setCompletedSteps] = useState([])
    const [currentStep, setCurrentStep] = useState('Initializing...')
    const [status, setStatus] = useState('running')
    const [error, setError] = useState(null)
    const intervalRef = useRef(null)

    useEffect(() => {
        if (!projectCtx.jobId) return

        intervalRef.current = setInterval(async () => {
            try {
                const data = await pollJobStatus(projectCtx.jobId)
                setCompletedSteps(data.completed_steps || [])
                setCurrentStep(data.current_step || 'Processing...')
                setStatus(data.status)

                if (data.status === 'completed') {
                    clearInterval(intervalRef.current)
                    const actualProject = data.actual_project_name || data.project_name || projectCtx.projectName
                    if (setProjectCtx) {
                        setProjectCtx({ jobId: projectCtx.jobId, projectName: actualProject })
                    }
                    setTimeout(() => navigate(`/dashboard?project=${encodeURIComponent(actualProject)}`), 1800)
                } else if (data.status === 'failed') {
                    clearInterval(intervalRef.current)
                    setError(data.error || 'Pipeline failed')
                }
            } catch {
                // keep polling
            }
        }, 2000)

        return () => clearInterval(intervalRef.current)
    }, [projectCtx.jobId, projectCtx.projectName, setProjectCtx, navigate])

    const progress = Math.min(((completedSteps.length) / STEPS.length) * 100, 100)

    return (
        <div className="relative min-h-screen flex flex-col items-center justify-center p-6 overflow-hidden">
            {/* Background */}
            <div className="fixed inset-0 -z-10">
                <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] rounded-full bg-gradient-to-br from-primary-500/8 via-accent-violet/5 to-transparent blur-3xl" />
            </div>

            <div className="w-full max-w-xl animate-fade-in-up">
                {/* Orbital header */}
                <div className="text-center mb-10">
                    <div className="relative inline-flex items-center justify-center w-24 h-24 mb-6">
                        {/* Outer ring */}
                        <div className="absolute inset-0 rounded-full border-2 border-dashed border-primary-500/20 animate-spin-slow" />
                        {/* Orbiting dot */}
                        <div className="absolute inset-0">
                            <div className="w-3 h-3 rounded-full bg-gradient-to-r from-primary-500 to-accent-violet shadow-lg shadow-primary-500/40 animate-orbit" />
                        </div>
                        {/* Center icon */}
                        <div className={`flex items-center justify-center w-16 h-16 rounded-2xl shadow-2xl transition-all duration-500 ${status === 'failed'
                                ? 'bg-gradient-to-br from-accent-rose to-red-600 shadow-accent-rose/30'
                                : status === 'completed'
                                    ? 'bg-gradient-to-br from-accent-emerald to-teal-600 shadow-accent-emerald/30'
                                    : 'bg-gradient-to-br from-primary-500 to-accent-violet shadow-primary-500/30 animate-pulse-glow'
                            }`}>
                            {status === 'failed' ? (
                                <AlertTriangle className="w-7 h-7 text-white" />
                            ) : status === 'completed' ? (
                                <CheckCircle2 className="w-7 h-7 text-white" />
                            ) : (
                                <Loader2 className="w-7 h-7 text-white animate-spin" />
                            )}
                        </div>
                    </div>

                    <h1 className="text-2xl font-extrabold tracking-tight">
                        {status === 'completed' ? 'Pipeline Complete!' : status === 'failed' ? 'Pipeline Failed' : 'Building your project...'}
                    </h1>
                    <p className="mt-1.5 text-surface-500 text-sm font-semibold">{projectCtx.projectName}</p>
                </div>

                {/* Progress bar */}
                <div className="glass-card rounded-2xl p-5 mb-5">
                    <div className="flex items-center justify-between mb-2.5">
                        <span className="text-[10px] font-bold text-surface-400 uppercase tracking-widest">Progress</span>
                        <span className="text-sm font-extrabold gradient-text">{Math.round(progress)}%</span>
                    </div>
                    <div className="h-2.5 rounded-full bg-surface-200/60 dark:bg-surface-800/60 overflow-hidden">
                        <div
                            className="h-full rounded-full bg-gradient-to-r from-primary-600 via-accent-violet to-accent-cyan transition-all duration-700 ease-out shadow-sm shadow-primary-500/30"
                            style={{ width: `${progress}%` }}
                        />
                    </div>
                </div>

                {/* Steps */}
                <div className="glass-card rounded-2xl p-5 space-y-1">
                    {STEPS.map(({ label, icon: Icon, color }, i) => {
                        const done = completedSteps.includes(label)
                        const active = currentStep === label && !done
                        return (
                            <div
                                key={label}
                                className={`flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-300 ${done
                                        ? 'bg-accent-emerald/5 dark:bg-accent-emerald/8'
                                        : active
                                            ? 'bg-primary-500/5 dark:bg-primary-500/8 shimmer-bg'
                                            : 'opacity-30'
                                    }`}
                                style={{ animationDelay: `${i * 50}ms` }}
                            >
                                <div className={`flex items-center justify-center w-8 h-8 rounded-lg transition-all ${done
                                        ? 'bg-accent-emerald/15 text-accent-emerald'
                                        : active
                                            ? `bg-gradient-to-br ${color} text-white shadow-sm`
                                            : 'bg-surface-200/50 dark:bg-surface-800/50 text-surface-400'
                                    }`}>
                                    {done ? <CheckCircle2 className="w-4 h-4" /> : active ? <Loader2 className="w-4 h-4 animate-spin" /> : <Icon className="w-4 h-4" />}
                                </div>
                                <span className={`text-sm font-semibold ${done ? 'text-accent-emerald' : active ? 'text-primary-600 dark:text-primary-400' : 'text-surface-400'
                                    }`}>
                                    {label}
                                </span>
                                {done && <span className="ml-auto text-[10px] font-bold text-accent-emerald/60 uppercase tracking-wide">Done</span>}
                            </div>
                        )
                    })}
                </div>

                {error && (
                    <div className="mt-4 px-4 py-3 rounded-xl bg-accent-rose/5 border border-accent-rose/15 text-accent-rose text-sm font-semibold animate-fade-in-up">
                        {error}
                    </div>
                )}
            </div>
        </div>
    )
}
