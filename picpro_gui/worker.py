"""Run a single synchronous PicPro operation outside the UI thread."""

from collections.abc import Callable
from copy import deepcopy
from enum import StrEnum
from threading import Event
from typing import Literal, TypeAlias

from picpro.PicPro import EventHandler, MemoryType, OperationCancelledError, PicPro
from PyQt6.QtCore import QThread, pyqtSignal

from picpro_gui.project import Project

ServiceFactory = Callable[[EventHandler], PicPro]
ReadRegion: TypeAlias = MemoryType | Literal['all']


class Operation(StrEnum):
    PROGRAM = 'Program'
    VERIFY = 'Verify'
    ERASE = 'Erase'
    READ = 'Read memory…'
    CONFIG = 'Read configuration'
    INFO = 'Programmer information'


class OperationThread(QThread):
    progress_event = pyqtSignal(object, arguments=['event'])
    succeeded = pyqtSignal(object, arguments=['result'])
    failed = pyqtSignal(str, arguments=['message'])
    cancelled = pyqtSignal(arguments=[])

    def __init__(self, operation: Operation, project: Project, service_factory: ServiceFactory, *, memory: ReadRegion | None = None) -> None:
        super().__init__()
        self.operation = operation
        self.project = deepcopy(project)
        self.memory = memory
        self._service_factory = service_factory
        self._cancel = Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            service = self._service_factory(self.progress_event.emit)
            result = self._execute(service)
        except OperationCancelledError:
            self.cancelled.emit()
        except Exception as error:  # noqa: BLE001 - report worker failures to the UI, never escape a Qt callback
            self.failed.emit(f'{type(error).__name__}: {error}')
        else:
            self.succeeded.emit(result)

    def _execute(self, service: PicPro) -> object:
        project = self.project
        cancel = self._cancel.is_set
        if self.operation in (Operation.PROGRAM, Operation.VERIFY):
            if project.firmware is None:
                msg = 'Choose an Intel HEX firmware file first.'
                raise ValueError(msg)
            if self.operation == Operation.PROGRAM:
                return service.program(project.target, project.firmware, pic_id=project.pic_id, fuses=project.fuses, should_cancel=cancel)
            return service.verify(project.target, project.firmware, should_cancel=cancel)
        if self.operation == Operation.ERASE:
            service.erase(project.target, should_cancel=cancel)
            return None
        if self.operation == Operation.READ:
            if self.memory is None:
                msg = 'Choose a memory region to read.'
                raise ValueError(msg)
            if self.memory == 'all':
                read_backup = getattr(service, 'read_backup', None)
                if read_backup is None:
                    msg = 'Whole-chip backups require a newer version of PicPro.'
                    raise ValueError(msg)
                return read_backup(project.target, should_cancel=cancel)
            return service.read_memory(project.target, self.memory, should_cancel=cancel)
        if self.operation == Operation.CONFIG:
            return service.read_config(project.target, should_cancel=cancel)
        return service.programmer_info(project.port, should_cancel=cancel)
