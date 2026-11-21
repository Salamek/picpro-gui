"""Desktop entry point; Qt is imported only when the GUI is launched."""

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog='picpro-gui', description='PicPro desktop programmer')
    parser.add_argument('project', nargs='?', type=Path, help='Project file to open')
    arguments = parser.parse_args()
    try:
        from PyQt6.QtCore import QSettings  # noqa: PLC0415 - load Qt only when launching
        from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: PLC0415 - load Qt only when launching

        from picpro_gui.window import PicProGUI  # noqa: PLC0415 - keep Qt out of core imports
    except ModuleNotFoundError as error:
        if error.name is None or not error.name.startswith('PyQt6'):
            raise
        sys.stderr.write('PyQt6 is required. Reinstall picpro-gui with your package manager.\n')
        return 1
    app = QApplication(sys.argv)
    app.setApplicationName('PicPro')
    app.setOrganizationName('PicPro')
    try:
        window = PicProGUI(settings=QSettings())
    except Exception as error:  # noqa: BLE001 - report startup failures before the event loop starts
        QMessageBox.critical(None, 'Unable to start PicPro', str(error))
        return 1
    if arguments.project is not None:
        window.load_project(arguments.project)
    else:
        window.restore_last_project()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
