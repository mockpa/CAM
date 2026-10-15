"""camcore-gui: PySide6 desktop app on top of the library (guide step 10).

Only editing and display live here. Toolpaths, G-code and checks come from the
library: the app saves the job file and runs `camcore check` on it, the one
path that produces programs for the machine.
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("camcore-gui needs PySide6: pip install -e .[gui]", file=sys.stderr)
        return 2
    try:                                  # QtWebEngine must be loaded before the QApplication exists
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        pass
    from .main_window import MainWindow
    app = QApplication.instance() or QApplication([sys.argv[0], *argv])
    app.setApplicationName("camcore")
    win = MainWindow(argv[0] if argv and not argv[0].startswith("-") else None)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
