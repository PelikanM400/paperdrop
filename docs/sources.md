# Choosing a source

PaperDrop reads one public URL that you configure. There is no built-in gallery
and no default artwork: what you point it at is entirely your choice.

## The two modes

| | Manifest mode | Image mode |
|---|---|---|
| `source.url` points at | a JSON manifest | an image file |
| Consistency guarantee | **yes** — `sha256` ties the manifest to the bytes | **none** |
| Skip the download when unchanged | yes, always | only if the server supports conditional requests |
| Pre-download size/dimension checks | yes, when declared | no |
| Logged as a weak guarantee | no | yes, on every run |
| Recommended | **yes** | only when there is no manifest |

Configure image mode with `"image_mode_acknowledged": true`. PaperDrop refuses
to run without it, so this can never be entered by accident.

## What makes a good source

- **Public and stable.** A URL that does not need a login and does not move.
- **Revision-pinned image URLs.** Prefer a URL that identifies an immutable
  revision (`.../<commit>/desk.jpg`) over a moving one (`.../main/desk.jpg`).
  With a pinned URL, what the manifest describes and what the client downloads
  are the same object permanently.
- **A manifest with `sha256`.** Without a hash, an unchanged image cannot be
  detected cheaply.
- **A reasonable size.** A 100 MB image is a bad daily download, and PaperDrop
  caps a single download at 64 MiB by default (`http.max_bytes`).
- **A resolution that matches your screens.** Set `source.min_width` and
  `source.min_height` and PaperDrop will refuse an image below that instead of
  stretching a small one across a large display.

## Sources that are not valid

- A repository or directory page — `https://github.com/user/repo`,
  `https://gitee.com/user/repo`, `.../tree/main`. These are HTML; PaperDrop
  refuses them and tells you what it expects.
- Anything requiring a token. PaperDrop sends no credentials by design, and
  adding one would mean keeping a secret on every machine that shows a wallpaper.
- Local files. `source.url` must be `http(s)`.

Check a candidate before committing to it:

```bash
python3 tools/validate-manifest.py https://example.com/desk.json --check-image
```

## Making your own source

Any static host works — a web server, an object store, a Git host's raw file
endpoint. The essential parts:

1. somewhere stable to put the image;
2. a JSON manifest next to it (see
   [manifest-spec.md](manifest-spec.md));
3. a way to update both, in that order: **image first, then manifest**. A client
   that reads a manifest describing an image that is not published yet would
   fail a hash check and keep the previous wallpaper — safe, but wasted.

Keeping your publisher's manifest correct is your job, not PaperDrop's.
PaperDrop never writes to your source.
