"""Operator data-key file. The secret is never printed.

This is a server file for a later hosting step. It does not start a listener.
"""

import os
import stat

from cryptography.fernet import Fernet


def initialize_data_key(path):
    path = os.path.abspath(path)
    if os.path.exists(path):
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode != 0o600:
            raise SystemExit("existing key file is not private")
        return {"path": path, "secret_shown": False, "wrote": False}
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, mode=0o700, exist_ok=True)
    key = Fernet.generate_key()
    temporary = path + ".tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, key + b"\n")
    finally:
        os.close(descriptor)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return {"path": path, "secret_shown": False, "wrote": True}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Write a private server data-key file. The key is not printed.")
    parser.add_argument("--path", required=True)
    args = parser.parse_args(argv)
    result = initialize_data_key(args.path)
    print("wrote" if result["wrote"] else "present")
    print("secret_shown", result["secret_shown"])


if __name__ == "__main__":
    main()
