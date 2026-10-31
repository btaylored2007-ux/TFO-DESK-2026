"""Boot hook for Mac-local Kraken callers that are NOT in this repo.

The Indonesian ISP sinkholes api.kraken.com and futures.kraken.com (202.169.44.80).
Any LaunchAgent or script that lives only on the Mac (for example under
~/Applications/KrakenQuantDesk) should import this before its first Kraken call:

    import runpy
    runpy.run_path("/ABSOLUTE/PATH/TO/MultiVenueDesk/macpatches/kraken_doh_boot.py")

or, if the repo is already on PYTHONPATH:

    import macpatches.kraken_doh_boot  # side effect: installs both resolvers

Spot, futures and websocket hosts then resolve through the desk DoH pin.
Public market data only. No keys, no orders.
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import kraken_dns, desk_doh
kraken_dns.install()
desk_doh.install()
