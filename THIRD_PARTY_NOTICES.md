# Third-party notices

Multi-Account Manager for Minecraft itself is released under the MIT License (`LICENSE`). The packaged app
(`Multi-Account Manager for Minecraft.app`) also contains the following third-party software. Their license texts are included in the app at
`Contents/Resources/licenses/` and in this repository under `packaging/licenses/` (or, for
permissively licensed packages, are copied from the installed package at build time).

| Component | Version built with | License | Source |
|---|---|---|---|
| Python | 3.14 | PSF License | https://www.python.org/ |
| Qt 6 (QtCore, QtGui, QtWidgets, QtSvg, plus supporting modules) via PySide6 | 6.12 | LGPL-3.0 | https://download.qt.io/ · https://code.qt.io/ |
| PySide6 / Shiboken6 | 6.12 | LGPL-3.0 | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| psutil | 7.2 | BSD-3-Clause | https://github.com/giampaolo/psutil |
| PyObjC (pyobjc-core and framework wrappers) | 12.2 | MIT | https://github.com/ronaldoussoren/pyobjc |
| PyInstaller bootloader | 6.22 | GPL-2.0-or-later **with the PyInstaller bootloader exception**, which permits distributing the packaged app under any license | https://github.com/pyinstaller/pyinstaller |

## Qt and PySide6 (LGPL-3.0)

Qt and PySide6 are used under the GNU Lesser General Public License v3.0
(`packaging/licenses/LGPL-3.0.txt`, which incorporates the GNU GPL v3.0,
`packaging/licenses/GPL-3.0.txt`). They are not modified. In the packaged app they are separate,
dynamically loaded libraries in `Contents/Frameworks/PySide6/`, so they can be replaced with
other compatible builds; the complete source of this application is in this repository, and the
build can be reproduced with `packaging/build_app.sh`. The corresponding Qt and PySide6 source
code is available from the links above (versions as listed).

## Not included

Prism Launcher, Minecraft and Java are **not** bundled or modified; the app starts the
user's own Prism Launcher installation through its documented command-line options.

## Trademarks

Minecraft is a trademark of Mojang Synergies AB / Microsoft. This project is not an official
Minecraft product and is not approved by or associated with Mojang or Microsoft. Prism Launcher
is a separate project and is not affiliated with this one.
