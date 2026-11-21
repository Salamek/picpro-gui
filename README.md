# PicPro GUI

A Qt desktop interface for the [PicPro](https://github.com/Salamek/picpro)
PIC programmer. It requires PicPro 0.5.0 or later.

Install `picpro-gui` from PyPI with `pip install picpro-gui`, or from a
configured package repository with `apt install picpro-gui` on Debian or
`pacman -S picpro-gui` on Arch Linux. Launch it with `picpro-gui` or pass
a project file as an argument.

From a checkout, install with `pip install -e '.[dev,test]'`. For development
alongside a PicPro checkout, install both from this directory with
`pip install -e ../picpro -e '.[dev,test]'`.

## Projects

Use **File → New/Open/Save/Save As** to manage projects. Project files store the
PIC type, serial port, firmware reference, ICSP mode, PIC ID, fuse overrides, and
selected read memory. Firmware references are relative to the project file;
Save As recalculates them to keep referring to the same firmware. Missing
firmware or an unplugged serial port does not prevent opening a project.

```yaml
schema_version: 1
chip: 12f675
port: /dev/ttyUSB0
firmware: firmware.hex
icsp: false
pic_id: null
fuses:
  WDT: Disabled
memory: rom
```

Fuse controls come from the selected chip definition. **From firmware** leaves
the setting in the HEX file unchanged. Program applies overrides and verifies
ROM/EEPROM; Verify compares ROM/EEPROM against the firmware. Read Configuration
displays detected values without changing project settings. Memory reads export
Intel HEX or binary files, using the same ROM byte order as CLI dumps.

Device operations run in a worker thread, with one active operation per window.
The activity log shows stages and verification results. Cancel waits for the
current device command to finish, and the window stays open until the worker
has released the device. Progress is indeterminate during transfers.

GUI tests run headlessly with `QT_QPA_PLATFORM=offscreen pytest tests/test_gui.py`.

## Building packages

Build a wheel with `python -m build`. On Debian, install the dependencies
listed in `debian/control` and run `dpkg-buildpackage -us -uc -b`. The
Debian build needs the `python3-picpro` package. On Arch, install `picpro`
from its package repository and run `makepkg -s` from this checkout.
