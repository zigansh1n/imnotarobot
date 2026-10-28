__version__ = "0.6.1"

from .server import create_app  # noqa: E402  (the server reads __version__)

__all__ = ["create_app", "__version__"]
