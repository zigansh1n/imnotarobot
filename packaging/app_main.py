"""Entry point of the bundled app. `--selftest` checks the bundle without any window or menu bar icon;
`--mcp` runs the MCP server over stdio for agents."""

import sys


def selftest() -> int:
    import asyncio
    import json
    import tempfile

    import rumps  # noqa: F401
    import uvicorn  # noqa: F401
    # the permission check talks to these, so the bundle must carry them
    import ServiceManagement  # noqa: F401
    import UserNotifications  # noqa: F401

    from imnotarobot import testset
    from imnotarobot.config import Config
    from imnotarobot.service import Service

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(layers=["local"], state_path=f"{tmp}/state.json")
        case = testset.generated(1, seed=3)[0]
        r = asyncio.run(Service(cfg).chain.solve(case["task"]))
    import WebKit  # noqa: F401

    from imnotarobot.menubar import ICON
    from imnotarobot.ui import PAGE

    from imnotarobot import config

    probe = "__selftest__"
    config.set_secret(probe, "ok")
    keychain = config.get_secret(probe) == "ok"
    config.set_secret(probe, "")

    print(json.dumps({"icon": ICON.exists(), "page": PAGE.exists(), "keychain": keychain, "expected": case["expected"],
                      "solution": r.solution, "layer": r.layer, "confidence": round(r.confidence, 3)}))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--mcp" in sys.argv:
        from imnotarobot.mcp_server import main as mcp_main

        mcp_main()
        sys.exit(0)
    from imnotarobot.menubar import main

    main()
