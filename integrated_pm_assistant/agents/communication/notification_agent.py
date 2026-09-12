"""
notification_agent.py
----------------------
Coordinates all notification delivery: email (primary) + Telegram (secondary).

Architecture:
    NotificationAgent
    ├── EmailNotifier       ← SMTP, always attempted first
    └── TelegramNotifier    ← Bot API, attempted after email, failures are non-fatal

Per-employee DM flow:
    notify_task_assigned()          → email full details + Telegram DM to assigned employee
    notify_project_assignment()     → email + Telegram DM to each employee

Broadcast flow:
    broadcast_telegram()            → send to all telegram-enabled employees

PM-level system alerts:
    notify_resource_shortage()      → email PM + Telegram to PM chat (original behavior)
    notify_meeting_created()        → Telegram to PM + per-employee DMs where available
"""

from typing import Any, Dict, List, Optional

from agents.communication.notifiers.email_notifier import EmailNotifier, is_valid_email
from agents.communication.notifiers.telegram_notifier import TelegramNotifier
from tools.resource_validator import record_notification_issues


class NotificationAgent:
    """
    Coordinates all notification delivery.
    Receives EmailNotifier and TelegramNotifier via constructor (dependency injection).
    Each public method maps to one business event.
    """

    def __init__(self, email_notifier: EmailNotifier, telegram_notifier: TelegramNotifier):
        self._email = email_notifier
        self._telegram = telegram_notifier
        self.notification_issues: List[Dict[str, Any]] = []

    # ── Public notification methods ─────────────────────────────────────────

    def notify_task_assigned(
        self,
        to_email: Optional[str],
        employee_name: str,
        project_name: str,
        task_name: str,
        deadline: str,
        priority: str = "",
        telegram_chat_id: Optional[str] = None,
    ) -> bool:
        """
        Notify a single employee of a task assignment.

        Step 1: Send full-detail email to the employee (if email is valid).
        Step 2: If the employee has a Telegram chat_id, send a direct DM.

        Args:
            to_email:          Employee's email address.
            employee_name:     Employee's display name.
            project_name:      Project the task belongs to.
            task_name:         Name of the assigned task.
            deadline:          Task deadline (formatted string).
            priority:          Optional priority (High / Medium / Low).
            telegram_chat_id:  If provided, sends a direct Telegram DM.
                               Pass None / empty to skip Telegram (e.g., employee not registered).
        """
        email_sent = False
        if is_valid_email(to_email):
            email_sent = self._email.send(
                to_email=str(to_email).strip(),
                subject=f"Task Assigned: {task_name} | {project_name}",
                message=self._task_assignment_email_body(
                    employee_name, project_name, task_name, deadline, priority
                ),
            )
        else:
            issue = {
                "recipient": employee_name,
                "task": task_name,
                "email": to_email if to_email is not None else None,
                "reason": f"Missing or invalid email address ('{to_email}')",
                "channel": "email",
                "action_impacted": "task_assignment",
            }
            self.notification_issues.append(issue)
            print(
                f"⚠️ [NotificationAgent] Skipping task assignment email for employee '{employee_name}' "
                f"(task: '{task_name}'): invalid or missing email address '{to_email}'."
            )
            record_notification_issues(project_name, self.notification_issues)

        # 2. Telegram DM — only if employee has registered
        if telegram_chat_id and str(telegram_chat_id).strip().lower() not in {"nan", "none", "null", ""}:
            try:
                self._telegram.send_task_assignment(
                    chat_id=str(telegram_chat_id).strip(),
                    employee_name=employee_name,
                    project_name=project_name,
                    task_name=task_name,
                    deadline=deadline,
                    priority=priority,
                )
            except Exception as exc:
                print(f"⚠️ Telegram DM to {employee_name} failed: {exc}")

        return email_sent

    def notify_task_assignments_batch(
        self,
        project_name: str,
        assignments: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Notify multiple employees of their task assignments.
        Skips invalid recipient emails gracefully with logging and records notification issues.

        Args:
            project_name: Name of the project.
            assignments: List of dicts, each containing:
                - to_email: str
                - employee_name: str
                - task_name: str
                - deadline: str
                - priority: Optional[str]
                - telegram_chat_id: Optional[str]

        Returns:
            Dict summary: {"total": int, "sent": int, "skipped": int, "issues": list}
        """
        sent_count = 0
        skipped_count = 0

        for item in assignments:
            to_email = item.get("to_email")
            emp_name = item.get("employee_name", "Team Member")
            task_name = item.get("task_name", "Task")
            deadline = item.get("deadline", "N/A")
            priority = item.get("priority", "")
            chat_id = item.get("telegram_chat_id")

            sent = self.notify_task_assigned(
                to_email=to_email,
                employee_name=emp_name,
                project_name=project_name,
                task_name=task_name,
                deadline=deadline,
                priority=priority,
                telegram_chat_id=chat_id,
            )
            if sent:
                sent_count += 1
            else:
                skipped_count += 1

        return {
            "total": len(assignments),
            "sent": sent_count,
            "skipped": skipped_count,
            "issues": list(self.notification_issues),
        }

    def notify_project_assignment(
        self,
        to_email: Optional[str],
        employee_name: str,
        project_name: str,
        tasks: List[Dict[str, Any]],
        telegram_chat_id: Optional[str] = None,
    ) -> bool:
        """
        Email full assignment details + optional Telegram DM summary.

        Args:
            telegram_chat_id: If provided, sends Telegram DM to this employee directly.
                              Pass None to skip Telegram (employee not yet registered).
        """
        email_sent = False
        if is_valid_email(to_email):
            email_sent = self._email.send(
                to_email=str(to_email).strip(),
                subject=f"Project Assignment: {project_name}",
                message=self._assignment_email_body(employee_name, project_name, tasks),
            )
        else:
            issue = {
                "recipient": employee_name,
                "email": to_email if to_email is not None else None,
                "reason": f"Missing or invalid email address ('{to_email}')",
                "channel": "email",
                "action_impacted": "project_assignment",
            }
            self.notification_issues.append(issue)
            print(
                f"⚠️ [NotificationAgent] Skipping project assignment email for employee '{employee_name}': "
                f"invalid or missing email address '{to_email}'."
            )
            record_notification_issues(project_name, self.notification_issues)

        if telegram_chat_id and str(telegram_chat_id).strip().lower() not in {"nan", "none", "null", ""}:
            task_summary = ", ".join(t.get("task_name", "Task") for t in tasks[:3])
            if len(tasks) > 3:
                task_summary += f" (+{len(tasks) - 3} more)"
            try:
                self._telegram.send_to_chat(
                    chat_id=str(telegram_chat_id).strip(),
                    message=(
                        f"🚨 *Project Assignment*\n\n"
                        f"Hello {employee_name},\n\n"
                        f"You have been assigned to *{TelegramNotifier.escape_md(project_name)}*\n\n"
                        f"Tasks: {TelegramNotifier.escape_md(task_summary)}\n\n"
                        f"📧 Check your email for the full assignment details."
                    ),
                )
            except Exception as exc:
                print(f"⚠️ Telegram message to {employee_name} failed: {exc}")
        else:
            # Fallback: PM-channel alert (original behavior)
            try:
                self._telegram.send(
                    f"🚨 *Project Assignment*\n"
                    f"Employee *{employee_name}* assigned to: *{project_name}*\n"
                    f"Check email for full details."
                )
            except Exception as exc:
                print(f"⚠️ Telegram PM alert failed: {exc}")

        return email_sent

    def notify_resource_shortage(
        self,
        pm_email: Optional[str],
        project_name: str,
        missing_roles: Dict[str, int],
        impacted_tasks: List[Dict[str, Any]],
        diagnostic_file_path: str,
        workflow_state_path: str,
    ) -> bool:
        """
        Send the full formatted shortage report email + Telegram alert to PM.
        Email body matches the original bordered format.
        """
        email_sent = False
        if is_valid_email(pm_email):
            email_sent = self._email.send(
                to_email=str(pm_email).strip(),
                subject=f"🚨 Resource Shortage Detected – Action Required | {project_name}",
                message=self._shortage_email_body(
                    project_name, missing_roles, impacted_tasks,
                    diagnostic_file_path, workflow_state_path,
                ),
            )
        else:
            issue = {
                "recipient": "Project Manager",
                "role": "Project Manager",
                "email": pm_email if pm_email is not None else None,
                "reason": f"Missing or invalid email address ('{pm_email}')",
                "channel": "email",
                "action_impacted": "resource_shortage_alert",
            }
            self.notification_issues.append(issue)
            print(
                f"⚠️ [NotificationAgent] Skipping shortage alert email to PM: "
                f"invalid or missing email address '{pm_email}'."
            )
            record_notification_issues(project_name, self.notification_issues)

        roles_lines = "\n".join(
            f"  • {TelegramNotifier.escape_md(role)} — {count}"
            for role, count in missing_roles.items()
        )
        try:
            self._telegram.send(
                f"🚨 *RESOURCE SHORTAGE ALERT*\n\n"
                f"*Project:* {TelegramNotifier.escape_md(project_name)}\n\n"
                f"*Missing Roles:*\n{roles_lines}\n\n"
                f"📧 Please check your email for the full diagnostic report\\."
            )
        except Exception as exc:
            print(f"⚠️ Telegram alert to PM failed: {exc}")

        return email_sent

    def notify_meeting_created(
        self,
        project_name: str,
        meeting_type: str,
        meeting_time: str,
        attendees: List[str],
        attendee_telegram_map: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Telegram ping when a new meeting is scheduled.

        Sends a PM-channel alert (original behavior) + individual DMs to attendees
        who have registered their Telegram accounts.

        Args:
            attendee_telegram_map: Optional dict mapping employee name → telegram_chat_id.
                                   If provided, sends personalized DMs to registered attendees.
                                   Example: {"Alice Smith": "123456789"}
        """
        # PM-channel group alert (original behavior — preserved)
        attendee_list = "\n".join(f"  • {a}" for a in attendees)
        self._telegram.send(
            f"📅 *Meeting Scheduled*\n"
            f"*Project:* {TelegramNotifier.escape_md(project_name)}\n"
            f"*Type:* {meeting_type}\n"
            f"*Time:* {meeting_time}\n\n"
            f"*Attendees:*\n{attendee_list}"
        )

        # Per-employee DMs (new behavior)
        if attendee_telegram_map:
            for emp_name, chat_id in attendee_telegram_map.items():
                if chat_id:
                    self._telegram.send_meeting_alert(
                        chat_id=chat_id,
                        employee_name=emp_name,
                        project_name=project_name,
                        meeting_type=meeting_type,
                        meeting_time=meeting_time,
                    )

    def broadcast_telegram(
        self,
        message: str,
        employees: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Send a broadcast Telegram message to all registered employees.

        Args:
            message:   The message to send.
            employees: List of employee dicts with 'telegram_chat_id' key.
                       If None, caller must provide the list (use get_telegram_enabled_employees()).

        Returns:
            {"sent": [...], "failed": [...]} broadcast summary.
        """
        if not employees:
            return {"sent": [], "failed": [], "note": "No employees provided for broadcast"}

        return self._telegram.broadcast(employees, message)

    def send_registration_invites(
        self,
        employees: List[Dict[str, Any]],
        bot_username: str,
    ) -> None:
        """
        Send Telegram registration invite emails to a list of employees.

        Called when onboarding new team members. Each employee receives an email
        with the bot link and instructions to press Start.

        Args:
            employees:    List of employee dicts with at least 'Email' and 'Employee_Name'.
            bot_username: The bot's Telegram username (from config.yaml telegram.bot_username).
        """
        for emp in employees:
            email = emp.get("Email", "")
            name = emp.get("Employee_Name", "Team Member")
            if not is_valid_email(email):
                issue = {
                    "recipient": name,
                    "email": email if email is not None else None,
                    "reason": f"Missing or invalid email address ('{email}')",
                    "channel": "email",
                    "action_impacted": "registration_invite",
                }
                self.notification_issues.append(issue)
                print(
                    f"⚠️ [NotificationAgent] Skipping registration invite for '{name}': "
                    f"invalid or missing email address '{email}'."
                )
                continue
            try:
                self._email.send_telegram_registration_invite(
                    to_email=str(email),
                    employee_name=str(name),
                    bot_username=bot_username,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"❌ Could not send registration invite to {email}: {exc}")

    # ── Private message builders ─────────────────────────────────────────────

    def _task_assignment_email_body(
        self,
        employee_name: str,
        project_name: str,
        task_name: str,
        deadline: str,
        priority: str,
    ) -> str:
        priority_line = f"Priority: {priority}\n" if priority else ""
        return (
            f"Hello {employee_name},\n\n"
            f"You have been assigned a new task on project: {project_name}\n\n"
            f"Task: {task_name}\n"
            f"Deadline: {deadline}\n"
            f"{priority_line}"
            f"\nPlease review your schedule and confirm your availability.\n\n"
            f"— PM Assistant"
        )

    def _assignment_email_body(
        self, employee_name: str, project_name: str, tasks: List[Dict]
    ) -> str:
        task_lines = "\n".join(
            f"  • {t.get('task_name', 'Task')} — {t.get('time_days', '?')} days"
            for t in tasks
        )
        return (
            f"Hello {employee_name},\n\n"
            f"You have been assigned to project: {project_name}\n\n"
            f"Your Tasks:\n{task_lines}\n\n"
            f"Please review your schedule and confirm availability.\n\n— PM Assistant"
        )

    def _shortage_email_body(
        self,
        project_name: str,
        missing_roles: Dict[str, int],
        impacted_tasks: List[Dict],
        diagnostic_path: str,
        workflow_path: str,
    ) -> str:
        roles_section = "\n".join(
            f"  • {role}: {count} needed" for role, count in missing_roles.items()
        )
        tasks_section = "\n".join(
            f"  • {t.get('task', 'Unknown')} — missing {t.get('skill_required', 'Unknown Role')}"
            for t in impacted_tasks
        )
        return (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  RESOURCE SHORTAGE NOTIFICATION\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Project: {project_name}\n\n"
            f"The system detected insufficient resources during task allocation.\n"
            f"The workflow has been PAUSED until you provide a resolution.\n\n"
            f"────────────────────────────────\n"
            f"  MISSING ROLES\n"
            f"────────────────────────────────\n"
            f"{roles_section}\n\n"
            f"────────────────────────────────\n"
            f"  IMPACTED TASKS\n"
            f"────────────────────────────────\n"
            f"{tasks_section}\n\n"
            f"────────────────────────────────\n"
            f"  DIAGNOSTIC REPORT\n"
            f"────────────────────────────────\n"
            f"  File: {diagnostic_path}\n\n"
            f"────────────────────────────────\n"
            f"  RESOLUTION OPTIONS\n"
            f"────────────────────────────────\n"
            f"  Please choose one of the following actions:\n\n"
            f"  1. ADD_RESOURCE\n"
            f"     → Add new employees or free up existing ones in employees data\n\n"
            f"  2. EXTEND_TIMELINE\n"
            f"     → Allow longer timelines for affected tasks\n\n"
            f"  3. REDUCE_SCOPE\n"
            f"     → Remove or defer tasks that cannot be staffed\n\n"
            f"────────────────────────────────\n"
            f"  HOW TO RESOLVE\n"
            f"────────────────────────────────\n"
            f"  1. If adding resources: update employees data with new/freed employees\n"
            f"  2. Open the workflow state file:\n"
            f"     {workflow_path}\n"
            f'  3. Change the "resolution" field from PENDING to your chosen option\n'
            f"     Example: resolution: ADD_RESOURCE\n"
            f"  4. Save the file — the system will automatically resume\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  This is an automated message from PM Assistant.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )
