from importlib.resources.abc import Traversable
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from picpro.ChipDataReader import ChipDataReader
from picpro.exceptions import FuseError
from picpro.PicPro import MemoryType

from picpro_gui.project import Project, ProjectError


@pytest.fixture
def chips(yaml_chip_data_path: Traversable) -> ChipDataReader:
    return ChipDataReader(yaml_chip_data_path)


def test_project_round_trip_and_relative_firmware(tmp_path: Path, chips: ChipDataReader) -> None:
    firmware = tmp_path / 'firmware.hex'
    firmware.write_text(':00000001FF\n', encoding='ascii')
    project = Project(chip='12f675', port='/dev/ttyUSB2', firmware=firmware, icsp=True,
                      pic_id='1234ABCD', fuses={'WDT': 'Disabled'}, memory=MemoryType.EEPROM)
    path = tmp_path / 'board.picpro.yml'
    project.save(path, chips)
    saved = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert saved['firmware'] == 'firmware.hex'
    assert saved['schema_version'] == 1
    assert Project.load(path, chips) == project
    assert project.target.port == '/dev/ttyUSB2'
    assert project.target.chip_name == '12f675'
    assert project.target.icsp


def test_projects_move_with_their_firmware(tmp_path: Path, chips: ChipDataReader) -> None:
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    Project(firmware=first / 'firmware.hex').save(first / 'board.yml', chips)
    (second / 'board.yml').write_bytes((first / 'board.yml').read_bytes())
    project = Project.load(second / 'board.yml', chips)
    assert project.firmware == second / 'firmware.hex'
    # A missing file must not prevent opening/editing a portable project.
    assert not project.firmware.exists()


def test_save_as_rebases_the_firmware_reference(tmp_path: Path, chips: ChipDataReader) -> None:
    subdirectory = tmp_path / 'projects'
    subdirectory.mkdir()
    project = Project(firmware=tmp_path / 'firmware.hex')
    path = subdirectory / 'board.yml'
    project.save(path, chips)
    assert yaml.safe_load(path.read_text(encoding='utf-8'))['firmware'] == '../firmware.hex'
    assert Project.load(path, chips) == project


def test_blank_project_can_be_saved_without_hardware_or_firmware(tmp_path: Path, chips: ChipDataReader) -> None:
    path = tmp_path / 'blank.yml'
    Project().save(path, chips)
    assert Project.load(path, chips) == Project()


@pytest.mark.parametrize('changes', [
    {'schema_version': 2}, {'schema_version': True}, {'chip': 12}, {'chip': 'UNKNOWN'},
    {'port': 123}, {'firmware': ['file.hex']}, {'icsp': 'false'}, {'pic_id': 123},
    {'fuses': []}, {'fuses': {'WDT': 1}}, {'memory': 'unknown'}, {'typo': True},
    {'pic_id': 'XX'}, {'pic_id': '0011223344'},
])
def test_invalid_project_files_are_rejected(tmp_path: Path, chips: ChipDataReader, changes: dict) -> None:
    path = tmp_path / 'bad.yml'
    path.write_text(yaml.safe_dump({'schema_version': 1, 'chip': '12f675', **changes}), encoding='utf-8')
    with pytest.raises(ProjectError):
        Project.load(path, chips)


@pytest.mark.parametrize('content', ['[not: valid', 'a plain string', '- a list', ''])
def test_malformed_or_non_mapping_yaml_is_rejected(tmp_path: Path, chips: ChipDataReader, content: str) -> None:
    path = tmp_path / 'bad.yml'
    path.write_text(content, encoding='utf-8')
    with pytest.raises(ProjectError):
        Project.load(path, chips)


@pytest.mark.parametrize('fuses', [{'UNKNOWN': 'Enabled'}, {'WDT': 'unknown'}])
def test_invalid_fuses_do_not_replace_existing_project(tmp_path: Path, chips: ChipDataReader, fuses: dict[str, str]) -> None:
    path = tmp_path / 'existing.yml'
    path.write_text('original', encoding='utf-8')
    with pytest.raises(FuseError):
        Project(fuses=fuses).save(path, chips)
    assert path.read_text(encoding='utf-8') == 'original'


def test_failed_atomic_replace_preserves_original_and_cleans_temporary_file(tmp_path: Path, chips: ChipDataReader) -> None:
    path = tmp_path / 'existing.yml'
    path.write_text('original', encoding='utf-8')
    with patch.object(Path, 'replace', side_effect=PermissionError('denied')), pytest.raises(PermissionError):
        Project().save(path, chips)
    assert path.read_text(encoding='utf-8') == 'original'
    assert list(tmp_path.iterdir()) == [path]


def test_icsp_only_chips_require_icsp(chips: ChipDataReader) -> None:
    chip = next(chip for chip in chips.chip_entries.values() if chip.icsp_only)
    project = Project(chip=chip.chip_name)
    with pytest.raises(ProjectError, match='requires ICSP'):
        project.validate(chips)
    project.icsp = True
    project.validate(chips)


def test_gui_launcher_handles_missing_optional_qt_dependency() -> None:
    import subprocess  # noqa: PLC0415 - separate interpreter verifies optional dependency isolation
    import sys  # noqa: PLC0415

    code = """
import importlib.abc
import sys
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('PyQt6'):
            raise ModuleNotFoundError('No Qt', name='PyQt6')
sys.meta_path.insert(0, NoQt())
import picpro_gui.project
assert 'PyQt6' not in sys.modules
from picpro_gui.__main__ import main
sys.exit(main())
"""
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=False, timeout=10)  # noqa: S603 - fixed executable/code
    assert result.returncode == 1
    assert 'PyQt6 is required.' in result.stderr
    assert result.stdout == ''
    assert 'Traceback' not in result.stderr
