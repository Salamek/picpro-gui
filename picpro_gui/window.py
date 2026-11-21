"""Project-oriented desktop interface to PicPro."""

from pathlib import Path
from typing import TYPE_CHECKING

from intelhex import IntelHex
from picpro.PicPro import ChipConfigEvent, MemoryType, PicPro, PicProEvent, ProgrammerInfo, StageEvent, VerificationEvent, VerificationResult
from picpro.protocol.ChipConfig import ChipConfig
from picpro.tools import swab_bytes
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QAction, QCloseEvent, QFontDatabase, QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from serial.tools.list_ports import comports

from picpro_gui.project import Project, ProjectError
from picpro_gui.socket import SocketPreviewWidget
from picpro_gui.worker import Operation, OperationThread, ReadRegion, ServiceFactory

if TYPE_CHECKING:
    from collections.abc import Callable

_PROJECT_FILTER = 'PicPro projects (*.picpro.yml);;YAML files (*.yml *.yaml);;All files (*)'
_SOCKET_PACKAGES = {'dip8': 8, 'dip14': 14, 'dip18': 18, 'dip28': 28, 'dip40': 40,
                    '8pin': 8, '14pin': 14, '18pin': 18, '28Npin': 28, '40pin': 40}


def hex_dump(data: bytes) -> str:
    lines = []
    for offset in range(0, min(len(data), 4096), 16):
        chunk = data[offset:offset + 16]
        text = ''.join(chr(byte) if 32 <= byte < 127 else '.' for byte in chunk)
        lines.append(f'{offset:06X}  {chunk.hex(" "):<47}  {text}')
    if len(data) > 4096:
        lines.append(f'Preview limited to 4096 of {len(data)} bytes. Exports include all bytes.')
    return '\n'.join(lines)


class PicProGUI(QMainWindow):
    def __init__(self, service: PicPro | None = None, service_factory: ServiceFactory | None = None) -> None:
        super().__init__()
        self.service = service if service is not None else PicPro()
        repository = self.service.chip_data
        self._chips = self.service.list_chips()
        self._service_factory = service_factory if service_factory is not None else lambda handler: PicPro(chip_data=repository, on_event=handler)
        self.project_path: Path | None = None
        self.worker: OperationThread | None = None
        self._output_file: Path | None = None
        self._loading = True
        self._fuse_controls: dict[str, QComboBox] = {}
        self._fuse_chip: str | None = None
        self._project_actions: list[QAction] = []
        self.status = QStatusBar(self)
        self.setStatusBar(self.status)
        self.resize(1100, 800)
        self._build_menu()
        self._build_ui()
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self._preview)
        self.refresh_ports()
        self._apply_project(self._empty_project())

    def _empty_project(self) -> Project:
        chips = self._chips
        name = '12f675' if '12f675' in chips else next(iter(chips))
        return Project(chip=name, icsp=chips[name].icsp_only)

    def _build_menu(self) -> None:
        menu = self.menuBar()
        if menu is None:
            return
        file_menu = menu.addMenu('&File')
        if file_menu is None:
            return
        commands: list[tuple[str, QKeySequence.StandardKey, Callable[[], object]]] = [
            ('&New project', QKeySequence.StandardKey.New, self.new_project),
            ('&Open project…', QKeySequence.StandardKey.Open, self.open_project),
            ('&Save project', QKeySequence.StandardKey.Save, self.save_project),
            ('Save project &as…', QKeySequence.StandardKey.SaveAs, lambda: self.save_project(save_as=True)),
        ]
        for title, shortcut, callback in commands:
            action = QAction(title, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            file_menu.addAction(action)
            self._project_actions.append(action)
        file_menu.addSeparator()
        exit_action = file_menu.addAction('E&xit')
        if exit_action is not None:
            exit_action.setShortcut(QKeySequence.StandardKey.Quit)
            exit_action.triggered.connect(self.close)

    def _build_ui(self) -> None:  # noqa: PLR0915 - declare and connect the main window layout
        central = QWidget()
        layout = QVBoxLayout(central)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.settings = QWidget()
        settings_layout = QVBoxLayout(self.settings)
        target = QGroupBox('Project configuration')
        form = QFormLayout(target)
        self.chip_selector = QComboBox()
        self.chip_selector.addItems(sorted(self._chips))
        self.chip_selector.setEditable(True)
        self.chip_selector.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.chip_selector.currentTextChanged.connect(self._chip_changed)
        chip_editor = self.chip_selector.lineEdit()
        if chip_editor is not None:
            chip_editor.editingFinished.connect(self._chip_changed)
        form.addRow('PIC type', self.chip_selector)
        self.port_selector = QComboBox()
        self.port_selector.setEditable(True)
        self.port_selector.currentTextChanged.connect(self._edited)
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh_ports)
        form.addRow('Serial port', self._row(self.port_selector, self.refresh_button))
        self.firmware_edit = QLineEdit()
        self.firmware_edit.setPlaceholderText('Choose an Intel HEX file')
        self.firmware_edit.textChanged.connect(self._edited)
        self.browse_button = QPushButton('Browse…')
        self.browse_button.clicked.connect(self._browse_firmware)
        form.addRow('Firmware', self._row(self.firmware_edit, self.browse_button))
        self.icsp_check = QCheckBox('Use ICSP connection')
        self.icsp_check.toggled.connect(self._edited)
        form.addRow('Connection', self.icsp_check)
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText('From firmware')
        self.id_edit.textChanged.connect(self._edited)
        form.addRow('PIC ID (hex)', self.id_edit)
        settings_layout.addWidget(target)
        self.fuse_group = QGroupBox('Fuse overrides')
        self.fuse_form = QFormLayout(self.fuse_group)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.fuse_group)
        settings_layout.addWidget(scroll, 1)
        hint = QLabel('“From firmware” keeps the setting in the HEX file.\nProgram applies overrides; Verify checks ROM and EEPROM.')
        hint.setWordWrap(True)
        settings_layout.addWidget(hint)
        splitter.addWidget(self.settings)

        inspection = QWidget()
        inspection_layout = QVBoxLayout(inspection)
        socket_row = QHBoxLayout()
        self.socket_preview = SocketPreviewWidget(chip_pins=8, socket_position=13)
        self.socket_preview.setMinimumSize(180, 260)
        self.socket_preview.setMaximumWidth(260)
        socket_row.addWidget(self.socket_preview)
        self.chip_summary = QLabel()
        self.chip_summary.setWordWrap(True)
        socket_row.addWidget(self.chip_summary, 1)
        inspection_layout.addLayout(socket_row)
        self.tabs = QTabWidget()
        self.preview = self._text_view()
        self.preview.setPlaceholderText('Choose firmware to preview ROM, EEPROM, and memory usage.')
        self.log = self._text_view()
        self.log.setMaximumBlockCount(2000)
        self.readback = self._text_view()
        self.tabs.addTab(self.preview, 'Firmware')
        self.tabs.addTab(self.readback, 'Readback')
        self.tabs.addTab(self.log, 'Activity')
        inspection_layout.addWidget(self.tabs, 1)
        splitter.addWidget(inspection)
        splitter.setSizes([420, 650])
        layout.addWidget(splitter, 1)
        operations = QHBoxLayout()
        self.operation_buttons: dict[Operation, QPushButton] = {}
        for operation in Operation:
            button = QPushButton(operation.value)
            button.clicked.connect(lambda _checked=False, op=operation: self.start_operation(op))
            operations.addWidget(button)
            self.operation_buttons[operation] = button
        self.cancel_button = QPushButton('Cancel')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.cancel_button.setToolTip('Stop between device commands; the active transfer must finish first.')
        operations.addWidget(self.cancel_button)
        layout.addLayout(operations)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.setCentralWidget(central)
        self.status.showMessage('Ready')

    @staticmethod
    def _row(first: QWidget, second: QWidget) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(first, 1)
        row.addWidget(second)
        return widget

    @staticmethod
    def _text_view() -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        return view

    def refresh_ports(self) -> None:
        try:
            ports = sorted(port.device for port in comports())
        except OSError as error:
            self._error('Unable to list serial ports', str(error))
            return
        text = self.port_selector.currentText()
        self.port_selector.blockSignals(True)  # noqa: FBT003 - Qt API
        self.port_selector.clear()
        self.port_selector.addItems(ports)
        self.port_selector.setCurrentText(text)
        self.port_selector.blockSignals(False)  # noqa: FBT003 - Qt API

    def _chip_changed(self) -> None:
        if self._loading:
            return
        if self.chip_selector.currentText().strip().lower() != self._fuse_chip:
            self._rebuild_fuses({})
        self._edited()

    def _rebuild_fuses(self, overrides: dict[str, str]) -> None:
        try:
            chip = self.service.get_chip(self.chip_selector.currentText())
        except KeyError:
            self.socket_preview.set_chip(None)
            self.chip_summary.setText('Choose a supported PIC type.')
            return
        self._fuse_controls.clear()
        while self.fuse_form.rowCount():
            self.fuse_form.removeRow(0)
        self._fuse_chip = chip.chip_name.lower()
        for name, choices in chip.fuses.items():
            control = QComboBox()
            control.addItem('From firmware', None)
            for value in choices:
                control.addItem(value, value)
            control.setCurrentIndex(control.findData(overrides.get(name)))
            control.currentIndexChanged.connect(self._edited)
            self.fuse_form.addRow(name, control)
            self._fuse_controls[name] = control
        self.icsp_check.setEnabled(not chip.icsp_only)
        if chip.icsp_only or not self._loading:
            self.icsp_check.setChecked(chip.icsp_only)
        self._update_socket()

    def _update_socket(self) -> None:
        try:
            chip = self.service.get_chip(self.chip_selector.currentText())
        except KeyError:
            return
        pins = _SOCKET_PACKAGES.get(chip.socket_image or '')
        use_socket = pins is not None and not self.icsp_check.isChecked() and not chip.icsp_only
        position = int(chip.pin1_location_text.rsplit(' ', 1)[1]) if use_socket else 1
        self.socket_preview.set_chip(pins if use_socket else None, position)
        placement = f'Pin 1: {chip.pin1_location_text}' if use_socket else 'Connect through ICSP.'
        self.chip_summary.setText(f'{chip.chip_name.upper()}\n\nROM: {chip.rom_size} bytes\nEEPROM: {chip.eeprom_size} bytes\n\n{placement}')

    def _collect_project(self) -> Project:
        firmware = self.firmware_edit.text().strip()
        base = self.project_path.parent if self.project_path is not None else Path.cwd()
        return Project(chip=self.chip_selector.currentText().strip().lower(), port=self.port_selector.currentText().strip(),
                       firmware=(base / firmware).resolve() if firmware else None,
                       icsp=self.icsp_check.isChecked(), pic_id=self.id_edit.text().strip() or None,
                       fuses={name: control.currentData() for name, control in self._fuse_controls.items() if control.currentData() is not None})

    def _apply_project(self, project: Project, path: Path | None = None) -> None:
        self._loading = True
        self.project_path = path
        self.chip_selector.setCurrentText(project.chip)
        self.port_selector.setCurrentText(project.port)
        self.firmware_edit.setText(str(project.firmware) if project.firmware else '')
        self.icsp_check.setChecked(project.icsp)
        self.id_edit.setText(project.pic_id or '')
        self._rebuild_fuses(project.fuses)
        self._saved_project = self._collect_project()
        self._loading = False
        self.setWindowModified(False)
        self._update_title()
        self._preview()

    def _update_title(self) -> None:
        name = self.project_path.name if self.project_path else 'Untitled project'
        self.setWindowTitle(f'{name}[*] — PicPro')

    def _edited(self) -> None:
        if self._loading:
            return
        self.setWindowModified(self._collect_project() != self._saved_project)
        self._update_socket()
        self._preview_timer.start()

    def _browse_firmware(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, 'Choose firmware', self.firmware_edit.text(), 'Intel HEX files (*.hex);;All files (*)')
        if filename:
            self.firmware_edit.setText(str(Path(filename).resolve()))

    def _preview(self) -> None:
        project = self._collect_project()
        if project.firmware is None:
            self.preview.clear()
            return
        try:
            project.validate(self.service.chip_data)
            data = self.service.inspect_hex(project.chip, project.firmware, pic_id=project.pic_id, fuses=project.fuses)
            content = (f'ROM: {data.rom_buffer.raw_size} / {data.chip_info.rom_size} bytes used\n'
                       f'EEPROM: {data.eeprom_buffer.raw_size} / {data.chip_info.eeprom_size} bytes used\n\n'
                       f'ROM (device byte order)\n{hex_dump(data.rom_data)}\n\nEEPROM\n{hex_dump(data.eeprom_data)}')
            self.preview.setPlainText(content)
        except Exception as error:  # noqa: BLE001 - present malformed or inaccessible firmware without breaking the event loop
            self.preview.setPlainText(f'Unable to preview firmware: {error}')

    def _confirm_discard(self) -> bool:
        if not self.isWindowModified():
            return True
        answer = QMessageBox.question(self, 'Unsaved project', 'Save changes to the current project?',
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return answer == QMessageBox.StandardButton.Discard

    def new_project(self) -> None:
        if self.worker is None and self._confirm_discard():
            self._apply_project(self._empty_project())
            self.readback.clear()
            self.log.clear()

    def open_project(self) -> None:
        if self.worker is not None or not self._confirm_discard():
            return
        filename, _ = QFileDialog.getOpenFileName(self, 'Open project', '', _PROJECT_FILTER)
        if filename:
            self.load_project(Path(filename))

    def load_project(self, path: Path) -> bool:
        if self.worker is not None:
            return False
        try:
            project = Project.load(path, self.service.chip_data)
        except Exception as error:  # noqa: BLE001 - project failures must preserve the current document
            self._error('Unable to open project', str(error))
            return False
        self._apply_project(project, path.resolve())
        self.readback.clear()
        self.log.clear()
        return True

    def save_project(self, *, save_as: bool = False) -> bool:
        if self.worker is not None:
            return False
        path = self.project_path
        if save_as or path is None:
            filename, _ = QFileDialog.getSaveFileName(self, 'Save project', str(path or 'Untitled.picpro.yml'), _PROJECT_FILTER)
            if not filename:
                return False
            path = Path(filename)
            if not path.suffix:
                path = path.with_suffix('.picpro.yml')
        try:
            project = self._collect_project()
            project.save(path, self.service.chip_data)
        except Exception as error:  # noqa: BLE001 - failed saves must retain the dirty document
            self._error('Unable to save project', str(error))
            return False
        self._apply_project(project, path.resolve())
        self.status.showMessage('Project saved')
        return True

    def _operation_project(self, operation: Operation) -> Project:
        project = self._collect_project()
        if not project.port:
            msg = 'Choose a serial port first.'
            raise ProjectError(msg)
        if operation == Operation.INFO:
            return project
        project.validate(self.service.chip_data)
        if operation in (Operation.PROGRAM, Operation.VERIFY) and (project.firmware is None or not project.firmware.is_file()):
            msg = 'Choose an existing Intel HEX firmware file first.'
            raise ProjectError(msg)
        return project

    def _choose_read_region(self, project: Project) -> ReadRegion | None:
        regions: dict[str, ReadRegion] = {}
        # PicPro 0.5.0 still supports individual reads.
        if hasattr(self.service, 'read_backup'):
            regions['All memory (Intel HEX backup)'] = 'all'
        regions['Program memory (ROM)'] = MemoryType.ROM
        if self.service.get_chip(project.chip).has_eeprom:
            regions['EEPROM'] = MemoryType.EEPROM
        regions['Configuration'] = MemoryType.CONFIG
        label, accepted = QInputDialog.getItem(self, 'Read memory', 'Memory region', list(regions), current=0, editable=False)
        return regions[label] if accepted else None

    def start_operation(self, operation: Operation) -> None:
        if self.worker is not None:
            return
        try:
            project = self._operation_project(operation)
        except Exception as error:  # noqa: BLE001 - validation failures must not start the device worker
            self._error('Unable to start operation', str(error))
            return
        if operation == Operation.ERASE and QMessageBox.question(self, 'Erase chip', 'Erase the selected chip?',
                                                               QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                                               QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._output_file = None
        memory = None
        if operation == Operation.READ:
            memory = self._choose_read_region(project)
            if memory is None:
                return
            tag = memory if memory == 'all' else memory.value
            formats = 'Intel HEX (*.hex)' if memory == 'all' else 'Intel HEX (*.hex);;Binary (*.bin)'
            filename, selected_filter = QFileDialog.getSaveFileName(self, 'Export memory readback', f'{project.chip}-{tag}.hex', formats)
            if not filename:
                return
            self._output_file = Path(filename)
            if not self._output_file.suffix:
                suffix = '.bin' if selected_filter.startswith('Binary') else '.hex'
                self._output_file = self._output_file.with_suffix(suffix)
            if memory == 'all' and self._output_file.suffix.lower() != '.hex':
                self._error('Unable to export backup', 'Choose an Intel HEX filename ending in .hex.')
                return
        self._preview_timer.stop()
        self.worker = OperationThread(operation, project, self._service_factory, memory=memory)
        self.worker.progress_event.connect(self._on_event)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._operation_failed)
        self.worker.cancelled.connect(self._operation_cancelled)
        self.worker.finished.connect(self._operation_finished)
        self._set_busy(busy=True)
        self.tabs.setCurrentWidget(self.log)
        self.log.appendPlainText(f'\n{operation.value}: {project.chip} on {project.port}')
        self.status.showMessage(operation.value)
        self.worker.start()

    def _set_busy(self, *, busy: bool) -> None:
        self.settings.setEnabled(not busy)
        for action in self._project_actions:
            action.setEnabled(not busy)
        for button in self.operation_buttons.values():
            button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.progress.setRange(0, 0 if busy else 1)
        self.progress.setValue(0)

    def cancel_operation(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status.showMessage('Waiting for the current device command to finish…')

    @pyqtSlot(object)
    def _on_event(self, event: PicProEvent) -> None:
        if isinstance(event, StageEvent):
            message = event.stage.value.replace('_', ' ').capitalize()
        elif isinstance(event, VerificationEvent):
            message = f'{event.memory.value.upper()} verification: {"passed" if event.matches else "FAILED"}'
            if event.maybe_locked:
                message += ' (ROM may be read-protected)'
        elif isinstance(event, ChipConfigEvent):
            message = f'Chip ID: {event.config.chip_id:#x}; calibration: {event.config.calibrate:#x}'
            # Older PicPro versions provide the config event without the mismatch check.
            if getattr(event, 'device_id_mismatch', False):
                message += (
                    f'\nWarning: Device ID mismatch for {event.chip.chip_name}. '
                    f'Expected {event.chip.chip_id:#06x}, read {event.config.chip_id:#06x}.'
                )
        else:
            return
        self.log.appendPlainText(message)
        self.status.showMessage(message)

    @pyqtSlot(object)
    def _on_success(self, result: object) -> None:
        try:
            if isinstance(result, VerificationResult) and not result.success:
                self._error('Verification failed', 'Device contents differ from the firmware. See the activity log.')
                return
            if isinstance(result, bytes) and self.worker is not None:
                data = swab_bytes(result) if self.worker.memory == MemoryType.ROM else result
                self.readback.setPlainText(hex_dump(data))
                self.tabs.setCurrentWidget(self.readback)
                if self._output_file is not None:
                    if self._output_file.suffix.lower() == '.bin':
                        self._output_file.write_bytes(data)
                    else:
                        image = IntelHex()
                        image.frombytes(data)
                        image.write_hex_file(str(self._output_file))
                    self.log.appendPlainText(f'Saved readback to {self._output_file}')
            elif isinstance(result, IntelHex) and self._output_file is not None:
                result.write_hex_file(str(self._output_file))
                regions = '\n'.join(f'0x{start:06X} to 0x{end - 1:06X}' for start, end in result.segments())
                self.readback.setPlainText(f'Backup saved to {self._output_file}\n\nHEX byte ranges\n{regions}')
                self.tabs.setCurrentWidget(self.readback)
                self.log.appendPlainText(f'Saved backup to {self._output_file}')
            elif isinstance(result, ChipConfig):
                chip = self.service.get_chip(self._collect_project().chip)
                self.readback.setPlainText(f'Chip ID: {result.chip_id:#x}\nPIC ID: {result.id.hex()}\nCalibration: {result.calibrate:#x}\n\n'
                                          + '\n'.join(f'{name}: {value}' for name, value in chip.decode_fuse_data(result.fuses).items()))
                self.tabs.setCurrentWidget(self.readback)
            elif isinstance(result, ProgrammerInfo):
                self.log.appendPlainText(f'Firmware: {result.firmware_version}\nProtocol: {result.protocol}')
            self.log.appendPlainText('Operation completed.')
            self.status.showMessage('Operation completed')
        except Exception as error:  # noqa: BLE001 - export/presentation failures must not escape Qt slots
            self._error('Unable to display or export result', str(error))

    @pyqtSlot(str)
    def _operation_failed(self, message: str) -> None:
        self._error('Operation failed', message)

    @pyqtSlot()
    def _operation_cancelled(self) -> None:
        message = 'Operation cancelled.'
        if self.worker is not None and self.worker.operation == Operation.PROGRAM:
            message += ' Programming may be incomplete.'
        self.log.appendPlainText(message)
        self.status.showMessage('Operation cancelled')

    @pyqtSlot()
    def _operation_finished(self) -> None:
        worker = self.worker
        self.worker = None
        self._set_busy(busy=False)
        if worker is not None:
            worker.deleteLater()

    def _error(self, title: str, message: str) -> None:
        self.log.appendPlainText(f'{title}: {message}')
        self.status.showMessage(title)
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event: QCloseEvent | None) -> None:  # noqa: N802 - Qt virtual method
        if event is None:
            return
        if self.worker is not None:
            QMessageBox.information(self, 'Operation in progress', 'Cancel the operation and wait for it to finish before closing.')
            event.ignore()
        elif self._confirm_discard():
            event.accept()
        else:
            event.ignore()
