from .base import PostProcessor
from .grbl import GrblPost
from .linuxcnc import LinuxCncPost

POSTS = {"linuxcnc": LinuxCncPost, "grbl": GrblPost}


def get_post(name: str) -> PostProcessor:
    try:
        return POSTS[name.lower()]()
    except KeyError:
        raise ValueError(f"unknown post '{name}', available: {', '.join(POSTS)}") from None
