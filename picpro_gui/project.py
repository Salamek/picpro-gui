"""Portable GUI project files, independent of Qt."""

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import yaml
from picpro.PicPro import ChipRepository, MemoryType, Target


class ProjectError(ValueError):
    """An invalid or unsupported project file or configuration."""


@dataclass
class Project:
    chip: str = '12f675'
    port: str = ''
    firmware: Path | None = None
    icsp: bool = False
    pic_id: str | None = None
    fuses: dict[str, str] = field(default_factory=dict)
    memory: MemoryType = MemoryType.ROM

    SCHEMA_VERSION: ClassVar[int] = 1

    @property
    def target(self) -> Target:
        return Target(self.port, self.chip, self.icsp)

    def validate(self, chips: ChipRepository) -> None:
        try:
            chip = chips.get_chip(self.chip)
        except KeyError as error:
            msg = f'Unknown PIC chip: {self.chip}'
            raise ProjectError(msg) from error
        chip.encode_fuse_data(self.fuses)
        if chip.icsp_only and not self.icsp:
            msg = f'{self.chip} requires ICSP mode.'
            raise ProjectError(msg)
        if self.pic_id:
            try:
                value = bytes.fromhex(self.pic_id)
            except ValueError as error:
                msg = 'PIC ID must contain hexadecimal byte pairs.'
                raise ProjectError(msg) from error
            maximum = 8 if chip.core_bits == 16 else 4
            if len(value) > maximum:
                msg = f'PIC ID must be at most {maximum} bytes for {self.chip}.'
                raise ProjectError(msg)

    @classmethod
    def load(cls, path: Path, chips: ChipRepository) -> 'Project':
        path = path.resolve()
        try:
            data = yaml.safe_load(path.read_text(encoding='utf-8'))
        except yaml.YAMLError as error:
            msg = f'Invalid project YAML: {error}'
            raise ProjectError(msg) from error
        if not isinstance(data, dict):
            msg = 'The project must contain a YAML mapping.'
            raise ProjectError(msg)
        version = data.get('schema_version')
        if type(version) is not int or version != cls.SCHEMA_VERSION:
            msg = f'Unsupported project schema version: {version!r}'
            raise ProjectError(msg)
        unknown = data.keys() - {'schema_version', 'chip', 'port', 'firmware', 'icsp', 'pic_id', 'fuses', 'memory'}
        if unknown:
            msg = f'Unknown project fields: {", ".join(str(key) for key in unknown)}'
            raise ProjectError(msg)
        for name, default in [('chip', None), ('port', ''), ('memory', 'rom')]:
            if not isinstance(data.get(name, default), str):
                msg = f'{name} must be a string.'
                raise ProjectError(msg)
        for name in ['firmware', 'pic_id']:
            if data.get(name) is not None and not isinstance(data[name], str):
                msg = f'{name} must be a string or null.'
                raise ProjectError(msg)
        if not isinstance(data.get('icsp', False), bool):
            msg = 'icsp must be a boolean.'
            raise ProjectError(msg)
        fuses = data.get('fuses', {})
        if not isinstance(fuses, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in fuses.items()):
            msg = 'fuses must map fuse names to setting names.'
            raise ProjectError(msg)
        try:
            memory = MemoryType(data.get('memory', 'rom'))
        except ValueError as error:
            msg = 'memory must be rom, eeprom, or config.'
            raise ProjectError(msg) from error
        firmware = Path(data['firmware']) if data.get('firmware') else None
        if firmware is not None:
            firmware = (path.parent / firmware).resolve()
        project = cls(chip=data['chip'], port=data.get('port', ''), firmware=firmware,
                      icsp=data.get('icsp', False), pic_id=data.get('pic_id'), fuses=dict(fuses), memory=memory)
        project.validate(chips)
        return project

    def save(self, path: Path, chips: ChipRepository) -> None:
        self.validate(chips)
        path = path.resolve()
        firmware = None
        if self.firmware is not None:
            try:
                firmware = Path(os.path.relpath(self.firmware.resolve(), path.parent)).as_posix()
            except ValueError:  # Paths on different Windows drives cannot be relative.
                firmware = str(self.firmware.resolve())
        data = {'schema_version': self.SCHEMA_VERSION, 'chip': self.chip, 'port': self.port,
                'firmware': firmware, 'icsp': self.icsp, 'pic_id': self.pic_id,
                'fuses': dict(self.fuses), 'memory': self.memory.value}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix=f'.{path.name}.', delete=False) as file:
                temporary = Path(file.name)
                yaml.safe_dump(data, file, sort_keys=False)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
