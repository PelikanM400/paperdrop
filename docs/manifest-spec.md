# Manifest format (version 1)

A manifest is a small public JSON document that tells a client *which image to
use right now*. It describes exactly one image; it is not a catalogue, and it
does not host anything.

Manifest mode is the recommended way to configure PaperDrop, because the
manifest binds the image to a hash. That makes two things possible:

- skip the download entirely when the image has not changed;
- prove that the bytes on your disk are the ones the manifest describes.

## Versioning

- A manifest **may** carry `schema` (string) and `schema_version` (integer).
- If both are absent, it **must** be treated as **version 1**. Publishers that
  predate this document are therefore automatically compatible.
- If `schema_version` is present and greater than the version a client supports,
  the client **must refuse** it with a clear "upgrade the client" message.
  Guessing at an unknown version is worse than stopping.
- Unknown *extra* fields **must** be ignored, so publishers can add fields
  without breaking existing clients.
- The known `schema` value is `paperdrop-manifest`.

## Fields

### Required

| Field | Type | Rules |
|---|---|---|
| `image_url` | string | Absolute `http(s)` URL. Must not contain credentials. Immutable, revision-pinned URLs are strongly preferred |
| `sha256` | string | 64 lowercase hex characters: the SHA-256 of the image bytes |

### Recommended

Missing recommended fields are not an error; the corresponding check is simply
skipped (and reported as a note).

| Field | Type | Meaning |
|---|---|---|
| `date` | string `YYYY-MM-DD` | The day this image belongs to, in the publisher's timezone |
| `published_at` | string, ISO 8601 with offset | When it was actually published |
| `timezone` | string | Timezone of the timestamps, e.g. `Asia/Shanghai` |
| `size_bytes` | integer > 0 | Image size in bytes; lets the client check before and after the download |
| `width`, `height` | integer > 0 | Pixel dimensions; lets the client check resolution |
| `photo_id` | string **or `null`** | Identifier of the source photo. **`null` is valid** — for example when the publisher uses its own photographs |
| `source_photo_url` | string | Page for the original photo. **Optional.** Absent when the publisher uses its own images |

### Forbidden

Local filesystem paths, internal bookkeeping, or credentials of any kind. A
client must not expect them and must not read them.

## Client requirements

A conforming client:

1. **C1** fetches only over `https` (plain `http` only with an explicit user opt-in);
2. **C2** reads the manifest with a small byte cap (e.g. 256 KiB);
3. **C3** fails safely — keeping the current wallpaper — on malformed JSON, a
   missing `image_url` or `sha256`, or a malformed hash;
4. **C4** enforces `size_bytes` when present, before and after the download;
5. **C5** verifies `sha256` after downloading and discards the download on a mismatch;
6. **C6** performs a **real decode** (not just a header sniff) before accepting
   an image;
7. **C7** checks `width`/`height` when present, plus the user's minimum resolution;
8. **C8** publishes to its cache atomically, only after every check passes;
9. **C9** skips the download when the cached image already matches `sha256`;
10. **C10** never feeds a partial file, an error page or JSON to the wallpaper setter;
11. **C11** sends no credentials — no `Authorization`, no cookies;
12. **C12** follows redirects only to `https`, a bounded number of times, without credentials;
13. **C13** uses a request timeout and bounded retries with backoff;
14. **C14** keeps a bounded cache (PaperDrop keeps the current and previous image);
15. **C15** bounds its log size and records nothing sensitive.

## Examples

### A published manifest (GitHub, landscape)

```json
{
  "date": "2026-10-11",
  "published_at": "2026-10-11T04:31:07+08:00",
  "timezone": "Asia/Shanghai",
  "photo_id": "jgg6LsIL2zY",
  "image_url": "https://raw.githubusercontent.com/OWNER/REPO/8d0d90fba97ac4bd0fd53c1daaa5a812d0008c59/desk.jpg",
  "sha256": "fd0ac9e2e14066372c509d860c332827d0a5bc1fe9c621b46ea75192214c069f",
  "width": 7952,
  "height": 5304,
  "size_bytes": 1850965,
  "source_photo_url": "https://unsplash.com/photos/jgg6LsIL2zY"
}
```

Note: no version field — a client must read this as version 1. Note also the
`image_url`: it is pinned to a commit, so the manifest and the image can never
drift apart.

### A publisher using its own photographs

```json
{
  "date": "2026-10-20",
  "published_at": "2026-10-20T04:30:51+08:00",
  "timezone": "Asia/Shanghai",
  "photo_id": null,
  "image_url": "https://raw.githubusercontent.com/OWNER/REPO/0123456789abcdef0123456789abcdef01234567/desk.jpg",
  "sha256": "3f786850e387550fdab836ed7e6dc881de23001b7c7b9a1c8f2f2a1b9d0f2c77",
  "width": 6000,
  "height": 4000,
  "size_bytes": 4212345
}
```

`photo_id` is `null` and `source_photo_url` is absent. That is valid, and a
client must not treat it as an error or invent attribution.

### Minimal

```json
{
  "image_url": "https://example.com/img/abc123.jpg",
  "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

Only the two required fields. Everything else is optional.

## Publishing a manifest

PaperDrop consumes manifests; it does not publish them. But if you are building
a publisher, a manifest that follows this document needs very little:

1. publish the image somewhere with a stable, revision-pinned URL;
2. compute its SHA-256;
3. write the JSON above next to it (any name; `desk.json` is a common choice);
4. make sure the manifest itself is served fresh enough for your clients —
   clients must compare content, not assume instant freshness.

## Image mode (no manifest)

A plain image URL is also accepted, with an explicit opt-in. What is missing:

- no `sha256`, so no proof that the bytes match the publisher's intent;
- no way to know whether the image changed without downloading it, unless the
  server supports conditional requests (`ETag` / `Last-Modified` → `304`);
- no size or dimension metadata, so those checks can only happen after the
  download.

PaperDrop logs the weak guarantee on every image-mode run, and marks it in
`paperdrop status`.
