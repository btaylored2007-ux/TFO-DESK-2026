#!/usr/bin/env python3
"""Review-tree install of the owner-chat package. Refuses production and activation."""
import importlib.util
import shutil
import tempfile
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("owner_chat_install", PKG / "install.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)
BASE = PKG / "fixture_base"
PINNED = PKG / "pinned_untouched"


def fail(message):
    print("FIXTURE_FAIL", message)
    raise SystemExit(1)


def main():
    if install.preflight("/opt/tfo/live/thf") != "REFUSED_ROOT":
        fail("production")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw) / "review"
        for path in BASE.rglob("*"):
            if path.is_file():
                dest = root / path.relative_to(BASE)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
        for path in PINNED.rglob("*"):
            if path.is_file():
                dest = root / path.relative_to(PINNED)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
        book = b"book-bytes-must-stay\n"
        (root / "veteran_desk/book.py").write_bytes(book)
        chatgpt = (root / "tools/chatgpt_web.py").read_bytes()
        bridge = (root / "veteran_desk/chatgpt_bridge.py").read_bytes()
        if install.apply_tree(root, activate=True) != "REFUSED_ACTIVATION":
            fail("activate")
        if (root / "veteran_desk/thf_chat_clarity.py").exists():
            fail("activate wrote")
        applied = install.apply_tree(root)
        if applied != "APPLIED_NOT_ACTIVATED":
            fail(applied)
        if install.apply_tree(root) != "REFUSED_RERUN":
            fail("repeat")
        if (root / "veteran_desk/book.py").read_bytes() != book:
            fail("book")
        if (root / "tools/chatgpt_web.py").read_bytes() != chatgpt or (root / "veteran_desk/chatgpt_bridge.py").read_bytes() != bridge:
            fail("chatgpt")
        if (root / "veteran_desk/thf_msgdesk.py").exists():
            fail("msgdesk")
        if not (root / "veteran_desk/supergrok_bridge.py").is_file():
            fail("client")
        if "systemctl" in (PKG / "install.py").read_text(encoding="utf-8"):
            fail("systemctl")
        if install.rollback_tree(root) != "ROLLED_BACK_NOT_ACTIVATED":
            fail("rollback")
        if (root / "tools/grok_web.py").read_bytes() != (BASE / "tools/grok_web.py").read_bytes():
            fail("worker not restored")
        if (root / "veteran_desk/thf_chat_clarity.py").exists():
            fail("new module remained")
        if (root / "veteran_desk/book.py").read_bytes() != book:
            fail("rollback book")
        if install.rollback_tree("/opt/tfo/live/thf") != "REFUSED_ROOT":
            fail("production rollback")
    print("FIXTURE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
