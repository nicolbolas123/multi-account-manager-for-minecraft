"""Entry point of the packaged app (PyInstaller runs this file).

The same binary also serves as the mock game client in ``--mock`` mode, because a
packaged app has no separate Python interpreter to start it with (see mcmanager.bundle).
"""

import sys

from mcmanager.bundle import FAKE_CLIENT_FLAG

if len(sys.argv) > 1 and sys.argv[1] == FAKE_CLIENT_FLAG:
    from mcmanager.mock.fake_client import main as fake_client_main

    raise SystemExit(fake_client_main(sys.argv[2:]))

from mcmanager.app import main  # noqa: E402

raise SystemExit(main())
