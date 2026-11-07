# Payload lineage

The only archive this controller accepts is the file shipped at `fixture/display-epoch-ui-20261107.tar.gz`.

- Bytes: 49168
- sha256: `9d91e5fc5c5f84c4b98832389f5cba2be2a4daa1ccff4715677e01c7c1699abb`
- Epoch module inside it: `veteran_desk/thf_display_epoch.py` `92a42c42ed62d2430b95cf29dc7e24d446af86d308c9a124e57424b22b151e5a`

A different file also named `display-epoch-ui-20261107.tar.gz` has been seen at 49029 bytes, sha256 `c7102844b6522a68d87b5ad3cbbb790e062728029ee97f8bdccc047a6c7d7e76`. Its epoch module is `b06e38c075297d0b89ac46737af60a9b2701388d75660cfa6934f4d2a24ec59f`. That file is not this payload. It is listed in `rejected_sha256`. Passing it to this controller is `REFUSED_OLD_ARCHIVE`.

Do not replace the bound hashes above with the unbound file. The archived kit `abfc2af4bdeae0959727afb14f308b0f99ebab06eb7e87bce583e872a5f955e4` contains the bound inner archive and is not this contract package.
