"""Session persistence and cost tracking.

PRIVACY: All session data stored locally at ~/.karna/sessions/sessions.db
No session data is ever sent to any external service.
Sessions can be deleted: nellie history delete <id>
Full wipe: rm -rf ~/.karna/sessions/
"""

from karna.sessions.db import SessionDB
from karna.sessions.cost import CostTracker

__all__ = ["SessionDB", "CostTracker"]
