import pandas as pd
from pathlib import Path

class ConsensusService:
    """
    Handles the semi-automated consensus workflow:
    1. Sends "A key participant suggested a new time..." requests.
    2. Checks if consensus (all R/A participants agree) has been reached.
    """

    def __init__(self, output_dir: Path, email_notifier, telegram_notifier=None):
        self._output_dir = output_dir
        self._email_notifier = email_notifier
        self._telegram_notifier = telegram_notifier

    def request_consensus(self, project_name: str, event_id: str, proposer_email: str, proposed_time: str) -> None:
        """
        Send consensus requests to all participants except the proposer.
        """
        participant_file = self._output_dir / f"{project_name}_Participants.xlsx"
        df = pd.read_excel(participant_file)
        
        # Get proposer name for the message
        proposer_row = df[df["Email"].str.lower() == proposer_email.lower()]
        proposer_name = proposer_row.iloc[0]["Employee"] if not proposer_row.empty else "a team member"

        # List of participants to notify (everyone else assigned to this meeting)
        others = df[(df["Meeting_ID"] == event_id) & (df["Email"].str.lower() != proposer_email.lower())]

        for _, row in others.iterrows():
            email = row["Email"]
            name = row["Employee"]
            
            # 1. Send Email
            self._email_notifier.send_consensus_confirmation_request(
                to_email=email,
                project_name=project_name,
                proposer_name=proposer_name,
                proposed_time=proposed_time
            )

            # 2. Send Telegram (if available)
            if self._telegram_notifier:
                chat_id = self._lookup_chat_id(email)
                if chat_id:
                    self._telegram_notifier.send_consensus_confirmation_request(
                        chat_id=chat_id,
                        employee_name=name,
                        project_name=project_name,
                        proposer_name=proposer_name,
                        proposed_time=proposed_time
                    )

    def check_consensus(self, project_name: str, event_id: str) -> bool:
        """
        Consensus is reached if all R/A participants have 'AGREE' in Consensus_Response.
        """
        participant_file = self._output_dir / f"{project_name}_Participants.xlsx"
        df = pd.read_excel(participant_file)
        
        # Filter for this meeting's R/A participants
        ra_participants = df[(df["Meeting_ID"] == event_id) & (df["Role"].isin(["R", "A"]))]
        
        if ra_participants.empty:
            return True # No R/A? (Shouldn't happen)

        # Check if any R/A hasn't agreed yet
        # We also need to check if they are the proposer (they effectively agree by proposing)
        # However, MeetingRescheduler should have moved the proposer to 'AGREE' or 
        # we can just check if everyone in R/A has 'AGREE'
        
        all_agreed = all(ra_participants["Consensus_Response"].str.upper() == "AGREE")
        any_disagreed = any(ra_participants["Consensus_Response"].str.upper() == "DISAGREE")
        
        if any_disagreed:
            # If anyone disagrees, consensus fails
            return False

        return all_agreed

    def _lookup_chat_id(self, email: str) -> str:
        try:
            employees_df = pd.read_excel(self._output_dir.parent / "employees.xlsx")
            emp_row = employees_df[employees_df["Email"].str.strip().str.lower() == email.lower()]
            if not emp_row.empty:
                chat_id = str(emp_row.iloc[0].get("telegram_chat_id", "")).strip()
                if chat_id.endswith(".0"): chat_id = chat_id[:-2]
                if chat_id and chat_id.lower() not in ("", "nan", "none"):
                    return chat_id
        except Exception:
            pass
        return None
