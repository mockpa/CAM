from .base import PostProcessor
from .grbl import GrblPost
from .linuxcnc import LinuxCncPost

POSTS = {"linuxcnc": LinuxCncPost, "grbl": GrblPost}


def get_post(name: str, **options) -> PostProcessor:
    """Post by name; options (e.g. arcs, arc_tolerance) go to its constructor."""
    try:
        cls = POSTS[name.lower()]
    except KeyError:
        raise ValueError(f"unknown post '{name}', available: {', '.join(POSTS)}") from None
    return cls(**options)


def job_post(job: dict, name: str | None = None) -> PostProcessor:
    """The job's post with its "post_options" (only applied to the job's own post)."""
    own = job.get("post", "linuxcnc")
    return get_post(name or own, **(job.get("post_options", {}) if name in (None, own) else {}))
