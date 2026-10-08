---
name: Telegram media caching
description: Durable rule for serving Telegram bot images and videos efficiently.
---

Character media is owned by the connected Telegram bot, but the web app should proxy it through a same-origin route. Resolve a Telegram `file_id` with `getFile`, stream the bytes into a disk cache, and serve cached files with browser cache headers and conditional/range responses. Keep the cache bounded at 1GB with least-recently-used eviction.

**Why:** Direct media URLs make browsers repeatedly request Telegram-hosted files and increase network and resource usage. A disk cache avoids repeat upstream fetches without retaining large media in process memory.

**How to apply:** Keep media references in character data as file IDs or URLs, normalize image/video field variants at render time, and let the proxy distinguish video MIME types so HTML5 video controls and seeking continue to work. Treat explicit access-time updates as the LRU signal, remove the media and its metadata together, and keep per-file downloads below 256MB.