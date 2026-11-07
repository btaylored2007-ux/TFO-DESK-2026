#!/usr/bin/env python3
"""Write the TAKEOVER export from a read-only capture. No signal and no spawn."""
import argparse
import json
import socket
from pathlib import Path

from deploy.paper_rules_engine_activation_t2824.ownership import LIVE, OwnershipError, classify, write_takeover


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    capture = json.loads(Path(args.capture).read_text())
    if not args.persist:
        print(json.dumps(classify(capture), indent=2))
        return 0
    root = args.root or LIVE
    if socket.gethostname() != "tfo-sgp1" and (root == LIVE or str(root).startswith("/opt/tfo")):
        raise OwnershipError("REFUSED_ROOT")
    path, body = write_takeover(root, capture)
    print(json.dumps(dict(wrote=str(path), status=body["status"], verdict=body["verdict"], retire_command=None)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OwnershipError as exc:
        raise SystemExit(exc.code)
