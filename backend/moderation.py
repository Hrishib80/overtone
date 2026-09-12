import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

async def moderate_media(paths: List[Path]) -> bool:
    """
    TODO: Implement actual media moderation.
    Currently stubs out moderation, logs a warning, and returns True.
    """
    logger.warning("Media moderation is currently a stub and always returns True.")
    return True
