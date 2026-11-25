"""Feature switches (TypeScript SDK: src/config/features.ts).

``up_down``: Up/Down is not enabled yet. While it is False, every ``client.updown`` call raises ``NotDeployedError``
("Up/Down is not enabled yet.") before any network call, and ``client.get_claimables()`` returns an empty ``up_down``
section.
"""

from typing import Dict

FEATURES: Dict[str, bool] = {"up_down": False}
