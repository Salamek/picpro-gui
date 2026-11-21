# ruff: noqa: SLF001 - inspect GUI document and worker state at the adapter boundary

import os
import threading
import time
from collections.abc import Iterator
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
from intelhex import IntelHex

pytest.importorskip('PyQt6.QtWidgets')

from picpro.ChipDataReader import ChipDataReader
from picpro.PicPro import MemoryType, OperationCancelledError, PicPro, ProgrammerInfo, Stage, StageEvent, VerificationResult
from picpro.protocol.ChipConfig import ChipConfig
from PyQt6.QtCore import QSettings, QThread
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from picpro_gui.project import Project
from picpro_gui.socket import SocketPreviewWidget
from picpro_gui.window import _RECENT_PROJECT_LIMIT, PicProGUI
from picpro_gui.worker import Operation


@pytest.fixture(scope='module')
def application() -> Iterator[QApplication]:
    previous = os.environ.get('QT_QPA_PLATFORM')
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    app = cast('QApplication | None', QApplication.instance()) or QApplication([])
    assert isinstance(app, QApplication)
    yield app
    app.processEvents()
    if previous is None:
        os.environ.pop('QT_QPA_PLATFORM', None)
    else:
        os.environ['QT_QPA_PLATFORM'] = previous


@pytest.fixture(scope='module')
def gui_service(yaml_chip_data_path: Traversable) -> PicPro:
    chips = ChipDataReader(yaml_chip_data_path).chip_entries
    repository = MagicMock(spec=ChipDataReader)
    repository.chip_entries = chips
    repository.get_chip.side_effect = lambda name: chips[name.lower()]
    return PicPro(chip_data=repository)


def _factory(window: PicProGUI) -> MagicMock:
    return cast('MagicMock', window._service_factory)


@pytest.fixture
def window(application: QApplication, gui_service: PicPro, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[PicProGUI]:
    monkeypatch.setattr(QMessageBox, 'critical', MagicMock())
    monkeypatch.setattr(QMessageBox, 'information', MagicMock())
    monkeypatch.setattr(QMessageBox, 'question', MagicMock(return_value=QMessageBox.StandardButton.Discard))
    monkeypatch.setattr('picpro_gui.window.comports', list)
    service = gui_service
    fake = MagicMock(spec=PicPro)
    fake.program.return_value = VerificationResult(rom_matches=True, eeprom_matches=True)
    fake.verify.return_value = VerificationResult(rom_matches=True, eeprom_matches=True)
    fake.read_memory.return_value = b'\x11\x22\x33\x44'
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    gui = PicProGUI(service, MagicMock(return_value=fake), settings=settings)
    gui.show()
    application.processEvents()
    yield gui
    if gui.worker is not None:
        gui.worker.cancel()
        assert gui.worker.wait(5000)
        application.processEvents()
    gui.setWindowModified(False)
    gui.close()
    gui.deleteLater()
    application.processEvents()


def wait_for_operation(window: PicProGUI, application: QApplication) -> None:
    deadline = time.monotonic() + 5
    while window.worker is not None and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    assert window.worker is None, 'GUI worker did not finish'


def test_chip_selection_fuses_and_socket_follow_definitions(window: PicProGUI) -> None:
    assert window.socket_preview.chip_pins == 8
    assert window.socket_preview.socket_position == 13
    assert 'WDT' in window._fuse_controls
    window.chip_selector.setCurrentText('18f2550')
    assert window.socket_preview.chip_pins == 28
    assert window.socket_preview.socket_position == 1
    assert set(window._fuse_controls) == set(window.service.get_chip('18f2550').fuses)
    window.icsp_check.setChecked(True)
    assert window.socket_preview.chip_pins is None
    assert window.isWindowModified()


def test_new_project_discards_old_state_only_when_confirmed(window: PicProGUI, monkeypatch: pytest.MonkeyPatch) -> None:
    window.port_selector.setCurrentText('/dev/custom')
    monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.StandardButton.Cancel)
    window.new_project()
    assert window.port_selector.currentText() == '/dev/custom'
    monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.StandardButton.Discard)
    window.new_project()
    assert window.port_selector.currentText() == ''
    assert not window.isWindowModified()
    assert window.project_path is None


def test_save_open_and_save_as_preserve_configuration(window: PicProGUI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / 'board.picpro.yml'
    firmware = tmp_path / 'firmware.hex'
    window.port_selector.setCurrentText('/dev/custom')
    window.firmware_edit.setText(str(firmware))
    window.icsp_check.setChecked(True)
    window.id_edit.setText('1234ABCD')
    fuse = window._fuse_controls['WDT']
    fuse.setCurrentIndex(fuse.findData('Disabled'))
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_args: (str(path), ''))
    assert window.save_project()
    assert not window.isWindowModified()
    saved = window._collect_project()
    window.new_project()
    assert window.load_project(path)
    assert window._collect_project() == saved
    nested = tmp_path / 'nested'
    nested.mkdir()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_args: (str(nested / 'copy.yml'), ''))
    assert window.save_project(save_as=True)
    assert Project.load(nested / 'copy.yml', window.service.chip_data).firmware == firmware


def test_failed_open_and_save_keep_current_document(window: PicProGUI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window.port_selector.setCurrentText('/dev/custom')
    bad = tmp_path / 'bad.yml'
    bad.write_text('not a project', encoding='utf-8')
    assert not window.load_project(bad)
    assert window.port_selector.currentText() == '/dev/custom'
    assert window.isWindowModified()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_args: (str(tmp_path / 'missing' / 'file.yml'), ''))
    assert not window.save_project()
    assert window.project_path is None
    assert window.isWindowModified()


def test_cancelled_save_prevents_new_project(window: PicProGUI, monkeypatch: pytest.MonkeyPatch) -> None:
    window.port_selector.setCurrentText('/dev/custom')
    monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_args: ('', ''))
    window.new_project()
    assert window.port_selector.currentText() == '/dev/custom'


def test_port_refresh_preserves_manual_and_unavailable_ports(window: PicProGUI, monkeypatch: pytest.MonkeyPatch) -> None:
    window.port_selector.setCurrentText('/dev/unplugged')
    monkeypatch.setattr('picpro_gui.window.comports', lambda: [MagicMock(device='/dev/ttyUSB0')])
    window.refresh_ports()
    assert window.port_selector.currentText() == '/dev/unplugged'
    assert window.port_selector.itemText(0) == '/dev/ttyUSB0'


def test_switching_from_icsp_only_chip_defaults_to_socket(window: PicProGUI) -> None:
    chip = next(chip for chip in window.service.list_chips().values() if chip.icsp_only)
    window.chip_selector.setCurrentText(chip.chip_name)
    assert window.icsp_check.isChecked()
    assert not window.icsp_check.isEnabled()
    assert window.socket_preview.chip_pins is None

    window.chip_selector.setCurrentText('12f675')
    assert window.icsp_check.isEnabled()
    assert not window.icsp_check.isChecked()
    assert window.socket_preview.chip_pins == 8


def test_firmware_preview_uses_core_without_hardware(window: PicProGUI, hex_file_path: Path) -> None:
    window.firmware_edit.setText(str(hex_file_path))
    window._preview()
    assert 'ROM:' in window.preview.toPlainText()
    assert 'EEPROM:' in window.preview.toPlainText()
    _factory(window).assert_not_called()


@pytest.mark.parametrize('operation', [Operation.PROGRAM, Operation.VERIFY, Operation.CONFIG, Operation.INFO, Operation.ERASE])
def test_operations_run_in_worker_and_restore_controls(window: PicProGUI, application: QApplication, hex_file_path: Path,
                                                       operation: Operation, monkeypatch: pytest.MonkeyPatch) -> None:
    window.port_selector.setCurrentText('/dev/fake')
    window.firmware_edit.setText(str(hex_file_path))
    window._fuse_controls['WDT'].setCurrentIndex(window._fuse_controls['WDT'].findData('Disabled'))
    fake = _factory(window).return_value
    fake.erase.return_value = None
    fake.read_config.return_value = ChipConfig(0xfc0, b'12345678', [0x31ff] + [0] * 6, 0x3456)
    fake.programmer_info.return_value = ProgrammerInfo(3, 'P18A')
    thread_ids: list[QThread] = []

    def factory(_handler: object) -> PicPro:
        thread_ids.append(QThread.currentThread())
        return fake

    _factory(window).side_effect = factory
    monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.StandardButton.Yes)
    window.start_operation(operation)
    assert window.worker is not None
    assert not window.settings.isEnabled()
    assert window.cancel_button.isEnabled()
    wait_for_operation(window, application)
    assert window.settings.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert thread_ids
    assert thread_ids[0] != application.thread()
    assert 'Operation completed' in window.status.currentMessage()
    if operation == Operation.PROGRAM:
        args, kwargs = fake.program.call_args
        assert args[0].port == '/dev/fake'
        assert args[1] == hex_file_path
        assert kwargs['fuses'] == {'WDT': 'Disabled'}


@pytest.mark.parametrize('memory', [MemoryType.ROM, MemoryType.EEPROM, MemoryType.CONFIG])
@pytest.mark.parametrize('binary', [True, False])
def test_read_exports_correct_byte_order(window: PicProGUI, application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       memory: MemoryType, *, binary: bool) -> None:
    output = tmp_path / ('read.bin' if binary else 'read.hex')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_args: (str(output), ''))
    window.port_selector.setCurrentText('/dev/fake')
    labels = {MemoryType.ROM: 'Program memory (ROM)', MemoryType.EEPROM: 'EEPROM', MemoryType.CONFIG: 'Configuration'}
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *_args, **_kwargs: (labels[memory], True))
    window.start_operation(Operation.READ)
    wait_for_operation(window, application)
    args, _ = _factory(window).return_value.read_memory.call_args
    assert args[1] == memory
    actual = output.read_bytes() if binary else bytes(IntelHex(str(output)).tobinarray())
    assert actual == (b'\x22\x11\x44\x33' if memory == MemoryType.ROM else b'\x11\x22\x33\x44')


def test_read_dialog_hides_eeprom_and_cancel_leaves_project_unchanged(window: PicProGUI, monkeypatch: pytest.MonkeyPatch) -> None:
    window.chip_selector.setCurrentText('10f200')
    window.port_selector.setCurrentText('/dev/fake')
    window.setWindowModified(False)
    choose = MagicMock(return_value=('', False))
    save = MagicMock()
    monkeypatch.setattr(QInputDialog, 'getItem', choose)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', save)

    window.start_operation(Operation.READ)

    choices = choose.call_args.args[3]
    assert 'EEPROM' not in choices
    assert choices[-2:] == ['Program memory (ROM)', 'Configuration']
    save.assert_not_called()
    _factory(window).assert_not_called()
    assert window.worker is None
    assert not window.isWindowModified()


def test_invalid_input_prevents_worker_and_serial_access(window: PicProGUI) -> None:
    window.start_operation(Operation.PROGRAM)
    assert window.worker is None
    window.port_selector.setCurrentText('/dev/fake')
    window.start_operation(Operation.PROGRAM)
    assert window.worker is None
    window.id_edit.setText('invalid')
    window.start_operation(Operation.CONFIG)
    assert window.worker is None
    _factory(window).assert_not_called()


def test_short_pic_id_prevents_programming(window: PicProGUI, hex_file_path: Path) -> None:
    window.port_selector.setCurrentText('/dev/fake')
    window.firmware_edit.setText(str(hex_file_path))
    window.id_edit.setText('1234')
    window.start_operation(Operation.PROGRAM)
    assert window.worker is None
    assert 'PIC ID must be exactly 4 bytes' in window.log.toPlainText()
    _factory(window).assert_not_called()


def test_verification_failure_is_reported(window: PicProGUI, application: QApplication, hex_file_path: Path) -> None:
    window.port_selector.setCurrentText('/dev/fake')
    window.firmware_edit.setText(str(hex_file_path))
    fake = _factory(window).return_value
    fake.verify.return_value = VerificationResult(rom_matches=False, eeprom_matches=True)
    window.start_operation(Operation.VERIFY)
    wait_for_operation(window, application)
    assert window.status.currentMessage() == 'Verification failed'
    assert 'Operation completed.' not in window.log.toPlainText()


def test_worker_exception_is_reported_and_controls_reenabled(window: PicProGUI, application: QApplication) -> None:
    window.port_selector.setCurrentText('/dev/fake')
    _factory(window).return_value.read_config.side_effect = OSError('unplugged')
    window.start_operation(Operation.CONFIG)
    wait_for_operation(window, application)
    assert 'unplugged' in window.log.toPlainText()
    assert window.settings.isEnabled()


def test_cancellation_and_busy_close_are_cooperative(window: PicProGUI, application: QApplication, hex_file_path: Path) -> None:
    window.port_selector.setCurrentText('/dev/fake')
    window.firmware_edit.setText(str(hex_file_path))
    entered = threading.Event()
    release = threading.Event()

    def program(*_args: object, **kwargs: object) -> None:
        entered.set()
        assert release.wait(3)
        check = kwargs['should_cancel']
        assert callable(check)
        assert check()
        raise OperationCancelledError

    _factory(window).return_value.program.side_effect = program
    window.start_operation(Operation.PROGRAM)
    try:
        assert entered.wait(2)
        worker = window.worker
        window.start_operation(Operation.INFO)
        assert window.worker is worker
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        window.cancel_operation()
        assert window.worker is worker
        assert not window.cancel_button.isEnabled()
    finally:
        release.set()
    wait_for_operation(window, application)
    assert window.status.currentMessage() == 'Operation cancelled'
    assert window.settings.isEnabled()


def test_stage_events_reach_the_activity_log(window: PicProGUI, application: QApplication) -> None:
    window.port_selector.setCurrentText('/dev/fake')

    def factory(handler: object) -> PicPro:
        assert callable(handler)
        handler(StageEvent(Stage.CONNECTING))
        return MagicMock(spec=PicPro)

    _factory(window).side_effect = factory
    window.start_operation(Operation.INFO)
    wait_for_operation(window, application)
    assert 'Connecting' in window.log.toPlainText()


def test_socket_redraw_does_not_accumulate_items(application: QApplication) -> None:
    application.processEvents()
    socket = SocketPreviewWidget(chip_pins=8, socket_position=13)
    scene = socket.scene()
    assert scene is not None
    count = len(scene.items())
    socket.set_chip(8, 13)
    assert len(scene.items()) == count
    socket.set_chip(None)
    assert len(scene.items()) == count - 2
    with pytest.raises(ValueError, match='invalid'):
        socket.set_chip(40, 13)
    socket.deleteLater()


def test_program_through_real_service_and_fake_serial_hardware(window: PicProGUI, application: QApplication,
                                                               hex_file_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.MockedDevice import MockedDevice  # noqa: PLC0415 - integration fixture

    device = MockedDevice(b'P18A')
    device.work_thread.chip_config = ChipConfig(0xfc0, b'12345678', [0x31ff] + [0] * 6, 0x3456)
    listener = threading.Thread(target=device.work_thread.listen_for_commands, daemon=True)
    monkeypatch.setattr('serial.Serial', MagicMock(return_value=device))
    monkeypatch.setattr('picpro.protocol.IConnection.time.sleep', lambda _seconds: None)
    repository = window.service.chip_data
    window._service_factory = lambda handler: PicPro(chip_data=repository, on_event=handler)
    window.port_selector.setCurrentText('/dev/fake')
    window.firmware_edit.setText(str(hex_file_path))
    listener.start()
    try:
        window.start_operation(Operation.PROGRAM)
        wait_for_operation(window, application)
        assert 'ROM verification: passed' in window.log.toPlainText()
        assert 'EEPROM verification: passed' in window.log.toPlainText()
        assert 'Operation completed' in window.status.currentMessage()
        assert not device.is_open
        assert not device.work_thread.is_programming_voltages_on
    finally:
        device.close()
        listener.join(timeout=2)
        assert not listener.is_alive()


def test_failed_port_refresh_preserves_current_port(window: PicProGUI, monkeypatch: pytest.MonkeyPatch) -> None:
    window.port_selector.setCurrentText('/dev/custom')
    monkeypatch.setattr('picpro_gui.window.comports', MagicMock(side_effect=OSError('cannot enumerate')))
    window.refresh_ports()
    assert window.port_selector.currentText() == '/dev/custom'
    assert 'cannot enumerate' in window.log.toPlainText()


def test_programmer_information_needs_only_a_port(window: PicProGUI, application: QApplication) -> None:
    window.port_selector.setCurrentText('/dev/fake')
    window.chip_selector.setCurrentText('unknown')
    window.id_edit.setText('not hex')
    window.start_operation(Operation.INFO)
    wait_for_operation(window, application)
    _factory(window).return_value.programmer_info.assert_called_once()


def test_loaded_uppercase_chip_does_not_create_false_unsaved_changes(window: PicProGUI, tmp_path: Path) -> None:
    path = tmp_path / 'uppercase.yml'
    Project(chip='12F675').save(path, window.service.chip_data)
    assert window.load_project(path)
    window.port_selector.setCurrentText('/dev/fake')
    assert window.isWindowModified()
    window.port_selector.setCurrentText('')
    assert not window.isWindowModified()


@pytest.mark.skipif(not hasattr(PicPro, 'read_backup'), reason='Requires the core backup API')
def test_read_all_saves_addressed_hex(window: PicProGUI, application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image = IntelHex({0: 0x83, 1: 0x12, 0x4200: 0x11})
    output = tmp_path / 'backup.hex'
    fake = _factory(window).return_value
    fake.read_backup.return_value = image
    choose = MagicMock(return_value=('All memory (Intel HEX backup)', True))
    save = MagicMock(return_value=(str(output), 'Intel HEX (*.hex)'))
    monkeypatch.setattr(QInputDialog, 'getItem', choose)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', save)
    window.port_selector.setCurrentText('/dev/fake')

    window.start_operation(Operation.READ)
    wait_for_operation(window, application)

    assert 'All memory (Intel HEX backup)' in choose.call_args.args[3]
    assert save.call_args.args[3] == 'Intel HEX (*.hex)'
    fake.read_backup.assert_called_once()
    fake.read_memory.assert_not_called()
    assert IntelHex(str(output)).todict() == image.todict()


def test_reopens_last_saved_project_in_new_window(window: PicProGUI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / 'board.picpro.yml'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_args: (str(path), ''))
    window.port_selector.setCurrentText('/dev/saved')
    assert window.save_project()
    assert window.close()
    restored = PicProGUI(window.service, settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat))
    restored.restore_last_project()
    assert restored.project_path == path
    assert restored.port_selector.currentText() == '/dev/saved'
    assert not restored.isWindowModified()
    restored.close()


def test_recent_projects_keep_configured_limit_and_reopen_latest(
    window: PicProGUI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [tmp_path / f'board-{index}.picpro.yml' for index in range(_RECENT_PROJECT_LIMIT + 1)]
    for path in paths:
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_args, filename=str(path): (filename, ''))
        assert window.save_project(save_as=True)

    window._recent_menu.aboutToShow.emit()
    assert [action.text() for action in window._recent_menu.actions()] == [str(path) for path in reversed(paths[1:])]
    window.port_selector.setCurrentText('/dev/unsaved')
    monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.StandardButton.Cancel)
    window._recent_menu.actions()[-1].trigger()
    assert window.project_path == paths[-1]
    assert window.isWindowModified()

    monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.StandardButton.Discard)
    window._recent_menu.actions()[-1].trigger()
    assert window.project_path == paths[1]
    assert not window.isWindowModified()
    restored = PicProGUI(window.service, settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat))
    restored.restore_last_project()
    assert restored.project_path == paths[1]
    restored._recent_menu.aboutToShow.emit()
    assert [action.text() for action in restored._recent_menu.actions()] == [str(paths[1]), *(str(path) for path in reversed(paths[2:]))]
    restored.close()


def test_startup_skips_missing_recent_project(window: PicProGUI, tmp_path: Path) -> None:
    path = tmp_path / 'board.picpro.yml'
    Project().save(path, window.service.chip_data)
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    settings.setValue('projects/recent_paths', [str(tmp_path / 'missing.picpro.yml'), str(path)])
    restored = PicProGUI(window.service, settings=settings)
    restored.restore_last_project()
    assert restored.project_path == path
    assert settings.value('projects/recent_paths', type=list) == [str(path)]
    restored.close()


def test_migrates_legacy_last_project_setting(window: PicProGUI, tmp_path: Path) -> None:
    path = tmp_path / 'legacy.picpro.yml'
    Project().save(path, window.service.chip_data)
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    settings.setValue('projects/last_path', str(path))
    restored = PicProGUI(window.service, settings=settings)
    restored.restore_last_project()
    assert restored.project_path == path
    assert settings.value('projects/recent_paths', type=list) == [str(path)]
    assert not settings.contains('projects/last_path')
    restored.close()


def test_fuse_dropdown_distinguishes_firmware_default_and_override(window: PicProGUI, tmp_path: Path) -> None:
    firmware = tmp_path / 'firmware.hex'
    window.firmware_edit.setText(str(firmware))
    wdt = window._fuse_controls['WDT']

    IntelHex({0x400e: 0xff, 0x400f: 0x31}).write_hex_file(str(firmware))
    window._preview()
    assert wdt.currentText() == 'Enabled (From firmware, Default)'
    assert 'WDT' not in window._collect_project().fuses

    wdt.setCurrentIndex(wdt.findData('Disabled'))
    assert window._collect_project().fuses == {'WDT': 'Disabled'}
    assert not window._fuse_override_labels['WDT'].isHidden()

    IntelHex({0x400e: 0xf7, 0x400f: 0x31}).write_hex_file(str(firmware))
    window._preview()
    assert wdt.itemText(0) == 'Disabled (From firmware)'
    assert wdt.itemText(wdt.findData('Enabled')) == 'Enabled (Default)'
    assert wdt.currentData() == 'Disabled'
    wdt.setCurrentIndex(wdt.findData('Enabled'))
    assert window._collect_project().fuses == {'WDT': 'Enabled'}
    wdt.setCurrentIndex(0)
    assert 'WDT' not in window._collect_project().fuses
    assert window._fuse_override_labels['WDT'].isHidden()


def test_fuse_dropdown_does_not_label_hex_padding_as_firmware(window: PicProGUI, tmp_path: Path) -> None:
    firmware = tmp_path / 'firmware.hex'
    window.firmware_edit.setText(str(firmware))
    wdt = window._fuse_controls['WDT']

    IntelHex({0: 0x83, 1: 0x12}).write_hex_file(str(firmware))
    window._preview()
    assert wdt.currentText() == 'Enabled (Default)'
    assert wdt.currentData() is None

    IntelHex({0x400e: 0xf7}).write_hex_file(str(firmware))
    window._preview()
    assert wdt.currentText() == 'Use firmware or default'
    assert wdt.currentData() is None
