# Display guard 20261107

Not deployed. The display mark is PENDING. The archived kit `deploy/display-epoch-ui-kit-20261107.tar.gz` sha `abfc2af4bdeae0959727afb14f308b0f99ebab06eb7e87bce583e872a5f955e4` is unchanged and is not applied.

Live `veteran_desk/book.py` was observed at `a7394770…` on both roots after a paper-rules code update. That file is not in this write set. This controller records the bytes it sees and refuses the apply if the two roots differ or if those bytes change before commit. It does not require the older `b4453b50…` pin and it does not rewrite the book. A book that changes during the apply is `REFUSED_BOOK_CHANGED`; the copied files are restored and the procedure stops. There is no override flag.

Recovery is explicit and is not a second apply:

```
python3 release_control.py --recover restore-files
python3 release_control.py --recover restart-dash
```

`restore-files` restores only the display write-set from `state/thf/display_release_20261107_backup/` and is allowed after `restart_begin`. `restart-dash` restarts only `tfo-dash-thf.service` when those files already match their targets. Neither command writes the book, the ChatGPT bridge, home, the engine, tick, or nginx.

`chatgpt_bridge.py` was not in the capture used for this UI and is not pinned here.
