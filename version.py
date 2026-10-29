"""App version. Bump APP_VERSION for every release (shown in the sidebar and Settings).

The GitHub release tag must be "v" + this number (for example v0.5.0); the GitHub build checks that.
"""
APP_VERSION = "0.6.1"

# "owner/repo" on GitHub, used for the update notice and the Help button. The GitHub build writes it into
# build_info.py automatically, so there is nothing to fill in. Local builds (build.bat) have no update check.
try:
    from build_info import GITHUB_REPO  # type: ignore  # noqa: F401
except ImportError:
    GITHUB_REPO = ""
