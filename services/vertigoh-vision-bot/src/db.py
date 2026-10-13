import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List


def get_current_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.isoformat()


def from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def calculate_next_ping(stage: int, base_time: Optional[datetime] = None) -> datetime:
    """
    Calculates the next ping deadline based on the escalation schedule:
      - Stage 0 (Initial sent) -> +12 hours
      - Stage 1 (12h ping sent) -> +8 hours
      - Stage 2 (8h ping sent) -> +4 hours
      - Stage 3+ (4h ping sent & subsequent) -> +2 hours recurring
    """
    base = base_time or get_current_utc()
    if stage == 0:
        return base + timedelta(hours=12)
    elif stage == 1:
        return base + timedelta(hours=8)
    elif stage == 2:
        return base + timedelta(hours=4)
    else:
        return base + timedelta(hours=2)


from contextlib import contextmanager


class ReviewDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self):
        """Initializes tables and indices."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tracked_assets (
                    notion_page_id TEXT PRIMARY KEY,
                    page_title TEXT NOT NULL,
                    asset_url TEXT NOT NULL,
                    discord_channel_id INTEGER,
                    discord_message_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    stage INTEGER NOT NULL DEFAULT 0,
                    initial_sent_at TEXT NOT NULL,
                    last_pinged_at TEXT NOT NULL,
                    next_ping_at TEXT NOT NULL,
                    reviewed_by TEXT,
                    reviewed_at TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_status_next_ping 
                ON tracked_assets (status, next_ping_at);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_discord_message 
                ON tracked_assets (discord_message_id);
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS discord_users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    is_bot INTEGER NOT NULL DEFAULT 0,
                    roles TEXT DEFAULT '',
                    last_synced_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_discord_user_name 
                ON discord_users (username, display_name);
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS custom_reminders (
                    reminder_id TEXT PRIMARY KEY,
                    message_text TEXT NOT NULL,
                    discord_guild_id INTEGER,
                    discord_channel_id INTEGER NOT NULL,
                    discord_message_id INTEGER NOT NULL,
                    target_user_id TEXT NOT NULL,
                    created_by_user_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    stage INTEGER NOT NULL DEFAULT 0,
                    initial_sent_at TEXT NOT NULL,
                    last_pinged_at TEXT NOT NULL,
                    next_ping_at TEXT NOT NULL,
                    acknowledged_by TEXT,
                    acknowledged_at TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_status_next_ping 
                ON custom_reminders (status, next_ping_at);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_msg_id 
                ON custom_reminders (discord_message_id);
                """
            )

    def count_tracked_assets(self) -> int:
        """Returns the total number of tracked Notion assets."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM tracked_assets").fetchone()
            return row["cnt"] if row else 0

    def seed_baseline_asset(self, notion_page_id: str, page_title: str, asset_url: str):
        """Seeds an existing historical Notion asset as reviewed baseline without triggering alerts."""
        now_str = to_iso(get_current_utc())
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tracked_assets (
                    notion_page_id, page_title, asset_url, discord_channel_id,
                    discord_message_id, status, stage, initial_sent_at,
                    last_pinged_at, next_ping_at, reviewed_by, reviewed_at
                ) VALUES (?, ?, ?, 0, 0, 'REVIEWED', 0, ?, ?, ?, 'BASELINE_SEED', ?)
                ON CONFLICT(notion_page_id) DO NOTHING;
                """,
                (notion_page_id, page_title, asset_url, now_str, now_str, now_str, now_str),
            )

    def record_new_asset(
        self,
        notion_page_id: str,
        page_title: str,
        asset_url: str,
        discord_channel_id: int,
        discord_message_id: int,
        initial_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Records a new asset detected in Notion and sets the first 12-hour reminder timer."""
        now = initial_time or get_current_utc()
        now_str = to_iso(now)
        next_ping = calculate_next_ping(stage=0, base_time=now)
        next_ping_str = to_iso(next_ping)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tracked_assets (
                    notion_page_id, page_title, asset_url, discord_channel_id,
                    discord_message_id, status, stage, initial_sent_at,
                    last_pinged_at, next_ping_at, reviewed_by, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, 'PENDING', 0, ?, ?, ?, NULL, NULL)
                ON CONFLICT(notion_page_id) DO UPDATE SET
                    page_title = excluded.page_title,
                    asset_url = excluded.asset_url,
                    discord_channel_id = excluded.discord_channel_id,
                    discord_message_id = excluded.discord_message_id,
                    status = 'PENDING',
                    stage = 0,
                    initial_sent_at = excluded.initial_sent_at,
                    last_pinged_at = excluded.last_pinged_at,
                    next_ping_at = excluded.next_ping_at,
                    reviewed_by = NULL,
                    reviewed_at = NULL;
                """,
                (
                    notion_page_id,
                    page_title,
                    asset_url,
                    discord_channel_id,
                    discord_message_id,
                    now_str,
                    now_str,
                    next_ping_str,
                ),
            )
        return self.get_asset(notion_page_id)

    def get_asset(self, notion_page_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tracked_assets WHERE notion_page_id = ?",
                (notion_page_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_asset_by_message_id(self, discord_message_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tracked_assets WHERE discord_message_id = ?",
                (discord_message_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_due_escalations(self, current_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Finds all pending assets whose next ping deadline has elapsed."""
        now = current_time or get_current_utc()
        now_str = to_iso(now)
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tracked_assets 
                WHERE status = 'PENDING' AND next_ping_at <= ?
                ORDER BY next_ping_at ASC;
                """,
                (now_str,),
            ).fetchall()
            return [dict(r) for r in rows]

    def advance_escalation_stage(
        self,
        notion_page_id: str,
        ping_time: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Increments the escalation stage and calculates the next ping timestamp."""
        asset = self.get_asset(notion_page_id)
        if not asset or asset["status"] != "PENDING":
            return None

        now = ping_time or get_current_utc()
        next_stage = asset["stage"] + 1
        next_ping = calculate_next_ping(stage=next_stage, base_time=now)

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE tracked_assets
                SET stage = ?,
                    last_pinged_at = ?,
                    next_ping_at = ?
                WHERE notion_page_id = ?;
                """,
                (next_stage, to_iso(now), to_iso(next_ping), notion_page_id),
            )
        return self.get_asset(notion_page_id)

    def mark_reviewed(
        self,
        notion_page_id: str,
        reviewer_id: str,
        reviewed_at: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Marks an asset as reviewed, halting further escalations."""
        now = reviewed_at or get_current_utc()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE tracked_assets
                SET status = 'REVIEWED',
                    reviewed_by = ?,
                    reviewed_at = ?
                WHERE notion_page_id = ?;
                """,
                (reviewer_id, to_iso(now), notion_page_id),
            )
        return self.get_asset(notion_page_id)

    def mark_reviewed_by_message_id(
        self,
        discord_message_id: int,
        reviewer_id: str,
        reviewed_at: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        asset = self.get_asset_by_message_id(discord_message_id)
        if not asset:
            return None
        return self.mark_reviewed(asset["notion_page_id"], reviewer_id, reviewed_at)

    def upsert_discord_user(
        self,
        user_id: str,
        username: str,
        display_name: str,
        is_bot: bool = False,
        roles: str = "",
        synced_at: Optional[datetime] = None,
    ):
        """Records or updates a Discord user in the local directory."""
        now_str = to_iso(synced_at or get_current_utc())
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO discord_users (user_id, username, display_name, is_bot, roles, last_synced_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    display_name = excluded.display_name,
                    is_bot = excluded.is_bot,
                    roles = excluded.roles,
                    last_synced_at = excluded.last_synced_at;
                """,
                (str(user_id), username, display_name, 1 if is_bot else 0, roles, now_str),
            )

    def get_all_discord_users(self) -> List[Dict[str, Any]]:
        """Returns all cached Discord users."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM discord_users ORDER BY username ASC").fetchall()
            return [dict(r) for r in rows]

    def find_user_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Finds a user by matching username or display name."""
        clean = name.lstrip("@").strip().lower()
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM discord_users
                WHERE LOWER(username) = ? OR LOWER(display_name) = ?
                LIMIT 1;
                """,
                (clean, clean),
            ).fetchone()
            if not row:
                row = conn.execute(
                    """
                    SELECT * FROM discord_users
                    WHERE LOWER(username) LIKE ? OR LOWER(display_name) LIKE ?
                    LIMIT 1;
                    """,
                    (f"%{clean}%", f"%{clean}%"),
                ).fetchone()
            return dict(row) if row else None

    # custom reminder queries

    def record_custom_reminder(
        self,
        reminder_id: str,
        message_text: str,
        discord_guild_id: Optional[int],
        discord_channel_id: int,
        discord_message_id: int,
        target_user_id: str,
        created_by_user_id: str,
        initial_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Records a new custom reminder set via /remind with the 12-hour first deadline."""
        now = initial_time or get_current_utc()
        now_str = to_iso(now)
        next_ping = calculate_next_ping(stage=0, base_time=now)
        next_ping_str = to_iso(next_ping)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO custom_reminders (
                    reminder_id, message_text, discord_guild_id, discord_channel_id,
                    discord_message_id, target_user_id, created_by_user_id,
                    status, stage, initial_sent_at, last_pinged_at, next_ping_at,
                    acknowledged_by, acknowledged_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?, ?, NULL, NULL)
                ON CONFLICT(reminder_id) DO UPDATE SET
                    message_text = excluded.message_text,
                    discord_guild_id = excluded.discord_guild_id,
                    discord_channel_id = excluded.discord_channel_id,
                    discord_message_id = excluded.discord_message_id,
                    target_user_id = excluded.target_user_id,
                    created_by_user_id = excluded.created_by_user_id,
                    status = 'PENDING',
                    stage = 0,
                    initial_sent_at = excluded.initial_sent_at,
                    last_pinged_at = excluded.last_pinged_at,
                    next_ping_at = excluded.next_ping_at,
                    acknowledged_by = NULL,
                    acknowledged_at = NULL;
                """,
                (
                    reminder_id,
                    message_text,
                    discord_guild_id,
                    discord_channel_id,
                    discord_message_id,
                    str(target_user_id),
                    str(created_by_user_id),
                    now_str,
                    now_str,
                    next_ping_str,
                ),
            )
        return self.get_custom_reminder(reminder_id)

    def get_custom_reminder(self, reminder_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM custom_reminders WHERE reminder_id = ?",
                (reminder_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_custom_reminder_by_message_id(self, discord_message_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM custom_reminders WHERE discord_message_id = ?",
                (discord_message_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_due_custom_reminders(self, current_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Finds all pending custom reminders whose next ping deadline has elapsed."""
        now = current_time or get_current_utc()
        now_str = to_iso(now)
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM custom_reminders 
                WHERE status = 'PENDING' AND next_ping_at <= ?
                ORDER BY next_ping_at ASC;
                """,
                (now_str,),
            ).fetchall()
            return [dict(r) for r in rows]

    def advance_custom_reminder_stage(
        self,
        reminder_id: str,
        ping_time: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Increments the reminder escalation stage and calculates the subsequent deadline."""
        rem = self.get_custom_reminder(reminder_id)
        if not rem or rem["status"] != "PENDING":
            return None

        now = ping_time or get_current_utc()
        next_stage = rem["stage"] + 1
        next_ping = calculate_next_ping(stage=next_stage, base_time=now)

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE custom_reminders
                SET stage = ?,
                    last_pinged_at = ?,
                    next_ping_at = ?
                WHERE reminder_id = ?;
                """,
                (next_stage, to_iso(now), to_iso(next_ping), reminder_id),
            )
        return self.get_custom_reminder(reminder_id)

    def mark_custom_reminder_acknowledged(
        self,
        discord_message_id: int,
        acknowledged_by: str,
        ack_time: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Marks a custom reminder acknowledged by heart reaction, halting further pings."""
        rem = self.get_custom_reminder_by_message_id(discord_message_id)
        if not rem:
            return None

        now = ack_time or get_current_utc()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE custom_reminders
                SET status = 'REVIEWED',
                    acknowledged_by = ?,
                    acknowledged_at = ?
                WHERE reminder_id = ?;
                """,
                (acknowledged_by, to_iso(now), rem["reminder_id"]),
            )
        return self.get_custom_reminder(rem["reminder_id"])
