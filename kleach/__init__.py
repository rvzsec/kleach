"""kleach - smart, aggressive cleanup for low-storage devices and abandoned projects."""
from .cli import main
from .config import __version__

__all__ = ["main", "__version__"]
