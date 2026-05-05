# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

WrapBot is a Windows-only Tkinter desktop app for an Amazon-India poster-printing fulfillment workflow. It pulls unshipped orders from Amazon SP-API (or, as fallback, parses an uploaded invoice PDF), classifies each line item, locates the matching artwork in Google Drive, downloads it, and prints to A4/A3 via the Windows GDI printer API.

## Commands

Activate the bundled venv before running anything:

```bash
venv/Scripts/activate
```

Run the GUI app:

```bash
python app.py
```

Run the standalone print-queue watcher (separate, n8n-driven workflow — watches `PRINT_QUEUE_PATH/A3` and `/A4` and prints any image dropped in):

```bash
python auto_print.py
```

Build the single-file Windows executable (output: `dist/WrapBot.exe`):

```bash
pyinstaller WrapBot.spec
```

There is no test suite, linter, or formatter configured.

## Architecture

Three loosely-coupled pieces:

- **[app.py](app.py)** — the `App` Tkinter class is the whole GUI app. All worker logic (fetch, parse, classify, download, print) runs on background threads and posts back via `self.log()`. State lives on the instance: `self.current_items` is the print queue, `self.master_folders` caches Drive folder IDs, `self.gemini_key_index` rotates through Gemini keys.
- **[amazon_api.py](amazon_api.py)** — thin SP-API client. `fetch_all_pending_items()` is the entry point used by the GUI; it does LWA refresh-token → access-token exchange and paginates `/orders/v0/orders` (Unshipped, last 90 days) then `/orderItems` per order. India marketplace, EU endpoint.
- **[auto_print.py](auto_print.py)** — independent watchdog-based daemon. Not imported by `app.py`. Used in the n8n integration path where another system writes images to a watched folder.

### Key flows in app.py

1. **Order ingestion**: Either `run_fetch_orders()` (Amazon SP-API → `parse_order_items()`) or `run_analyze()` (PDF fallback → `extract_text_from_pdf()` → `parse_invoice_text()`). Both produce `self.current_items` entries with `title`, `size`, `category` (Music/Manual), `searchTitle`.
2. **Music classification**: `classify_music_with_gemini()` calls Gemini 2.5 Flash-Lite with rotation across `gemini_api_keys` in `amazon_config.json`. On 429s it advances `self.gemini_key_index` and after a full cycle sleeps 60s. There is a fallback heuristic when no keys are configured.
3. **Drive lookup**: `get_master_folders()` resolves three top-level Drive folder IDs (`Album Posters Python`, `Album Posters Manual`, `Album Posters Manual Freelance`). Music items use `fuzzy_find_artist_folder()` (difflib-based) then `find_pack_folder()` for size; non-music uses `find_folder()`. Files are downloaded into per-item temp dirs.
4. **Printing**: `print_image()` opens the printer DC, applies a saved DEVMODE blob (`a4_preset_blob` / `a3_preset_blob` in `amazon_config.json`) so Epson driver-specific settings like `MediaType` and `PrintQuality` survive, then renders via `PIL.ImageWin.Dib`. `get_devmode_from_preset()` deserializes the base64 `DriverData`.

### Resource paths under PyInstaller

`amazon_api._get_config_path()` and `app.authenticate_services()` both branch on `sys.frozen` to locate `credentials.json`, `token.json`, and `amazon_config.json` next to `WrapBot.exe` rather than inside the bundled archive. When adding new on-disk config, follow the same pattern and add the file to `WrapBot.spec`'s `datas`.

## Configuration files (gitignored intent — they contain real secrets)

- **amazon_config.json** — SP-API LWA credentials (`client_id`, `client_secret`, `refresh_token`), `marketplace_id`, `endpoint`, `gemini_api_keys` list, and the `a4_preset_blob` / `a3_preset_blob` DEVMODE captures. The repo currently has live secrets checked in; do not paste its contents into commits, issues, or external tools.
- **credentials.json** — Google OAuth client (Drive readonly + Gmail send scopes).
- **token.json** — cached Google user token, refreshed on startup.
- **.env** — only consumed by `auto_print.py` (`PRINT_QUEUE_PATH`, `PRINTER_NAME`). The GUI app does not read `.env`.

## Platform notes

- Hard Windows dependency via `win32print` / `win32ui` / `win32con` / `win32gui` — the app cannot run or be meaningfully tested on Linux/macOS.
- Errors from background threads are emailed to `TARGET_EMAIL` (currently `nkusharoraa@gmail.com`, hardcoded in [app.py](app.py)) via the authenticated Gmail service.
