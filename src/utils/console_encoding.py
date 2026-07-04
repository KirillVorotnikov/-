"""
Utility for safe UTF-8 encoding setup in Windows console.
"""
import io
import os
import sys


def setup_console_encoding():
    """
    Sets up UTF-8 encoding for Windows console in a safe way.
    Doesn't break pytest and other tools that intercept stdout.
    """
    # Only applies to Windows
    if sys.platform != "win32":
        return

    # Under pytest don't touch stdout/stderr
    if "pytest" in sys.modules:
        return

    # Already configured, don't touch
    if hasattr(sys.stdout, "_original_stream") or hasattr(sys.stderr, "_original_stream"):
        return

    # Not a terminal (possibly redirected to file) - don't touch
    if not sys.stdout.isatty() or not sys.stderr.isatty():
        return

    try:
        # Set environment variable for future processes
        if sys.version_info >= (3, 7):
            os.environ["PYTHONIOENCODING"] = "utf-8"

            # For current process use reconfigure (Python 3.7+)
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
                sys.stderr.reconfigure(encoding="utf-8")
        else:
            # For older Python versions use wrapper, but save original
            original_stdout = sys.stdout
            original_stderr = sys.stderr

            sys.stdout = io.TextIOWrapper(
                original_stdout.buffer,
                encoding="utf-8",
                line_buffering=original_stdout.line_buffering,
            )
            sys.stderr = io.TextIOWrapper(
                original_stderr.buffer,
                encoding="utf-8",
                line_buffering=original_stderr.line_buffering,
            )

            # Mark that streams were modified
            sys.stdout._original_stream = original_stdout
            sys.stderr._original_stream = original_stderr

    except Exception:
        # If something went wrong, don't crash, just work as is
        pass