# playlistpipe

Turn a YouTube playlist into a Notion database, Obsidian vault, or Anki deck.

## Quick start

```bash
pip install playlistpipe
plp
```

That's it. Run `plp` with no arguments and it walks you through the rest:

```
? Paste a YouTube playlist URL:  https://youtube.com/playlist?list=...
? Where should this go?
  ❯ Notion (recommended — full database with progress tracking)
    Notion (copy-paste markdown, no setup)
    Obsidian vault
    Anki deck
    Cancel
```

Pick a target and it prompts for whatever it needs (Notion token, vault path,
etc.), offers to remember your answers, runs the scrape, and opens the result
for you. The first run for a given target takes ~30 seconds of one-time setup;
every run after that is one command and one paste.

If you prefer flags over menus — for scripting, cron, CI — see
[For scripting](#for-scripting) at the bottom. The flag form has always worked
and still does.

## Why this exists

I was working through a 40-video React course on YouTube and wanted a
checklist I could tick off, with notes per video, that synced between my
laptop and phone. The obvious home for that is Notion, except adding 40
videos to a Notion database by hand is tedious and you give up after video
eight.

Every tool I tried either dumped a text file I still had to reformat, or
demanded a Google API key for a 30-minute setup, or was a browser extension
that broke the next time YouTube shipped a frontend change. So I wrote this.

It does one thing: take a playlist URL, pull out titles and durations and
channels, and hand them to wherever you actually keep your learning stuff.

## What you can send a playlist to

| Target       | What you get                                        | Use it for                        |
| ------------ | --------------------------------------------------- | --------------------------------- |
| `notion-api` | A real Notion database with typed columns           | Course progress tracking          |
| `notion-md`  | Markdown checklist (optionally copied to clipboard) | Pasting into any Notion page      |
| `obsidian`   | One note per video with YAML frontmatter + index    | Research, long-term reference     |
| `anki`       | A `.apkg` file with two cards per video             | Spaced review of what you watched |

Re-running on the same playlist updates in place. It won't clobber notes
you've added, and it won't duplicate Anki cards. That part matters more than
you'd think.

## Install

```bash
pip install playlistpipe
```

Or with `pipx` if you want it isolated:

```bash
pipx install playlistpipe
```

You'll also want `pyperclip` if you plan to use `--copy`:

```bash
pip install "playlistpipe[clipboard]"
```

## Getting started with each target

### Notion (API, the real version)

One-time setup:

1. Go to <https://www.notion.so/my-integrations>, click **New integration**,
   name it something like "playlistpipe". Copy the **Internal Integration
   Token** — starts with `secret_`.
2. In Notion, open the page where you want the database to live. Click the
   **...** menu in the top right, then **Connections**, and add your new
   integration.
3. Copy the page's ID. It's the 32-character chunk at the end of the URL —
   `https://notion.so/My-Page-abc123...def789` → the `abc123...def789` bit.

```bash
export NOTION_TOKEN=secret_...
plp "https://youtube.com/playlist?list=..." \
    --to notion-api --notion-parent YOUR_PAGE_ID
```

The output tells you the new database's URL. On subsequent runs, pass
`--notion-db DATABASE_ID` to append to the same database instead of making
a new one. Or put both in `~/.config/playlistpipe/config.toml` and forget
about them:

```toml
[notion]
token = "secret_..."
default_parent_page_id = "abc123..."
```

### Notion (copy-paste, no setup)

For one-off use or if you don't want to mess with API tokens:

```bash
plp "https://youtube.com/playlist?list=..." --to notion-md --copy
```

The markdown ends up on your clipboard. Paste it into any Notion page and
it becomes a real checklist. Without `--copy`, you get a `.md` file in the
output directory.

### Obsidian

```bash
plp "https://youtube.com/playlist?list=..." --to obsidian --vault ~/MyVault
```

This creates `MyVault/YouTube/Playlist Name/` with one markdown file per
video plus an `_index.md` that uses Dataview to show progress. The
frontmatter looks like:

```yaml
---
title: "Intro to Hooks"
url: "https://youtu.be/..."
channel: "Dev Ed"
duration: 745
position: 3
playlist: "React Full Course"
watched: false
tags: [youtube]
---
```

The `watched: false` field is the key bit — flip it to `true` as you go and
Dataview will show you your progress in the index note. If you edit the
video notes (adding your own notes, changing tags), re-running the tool
preserves those edits. The index gets regenerated every time.

### Anki

```bash
plp "https://youtube.com/playlist?list=..." --to anki
```

Outputs a `.apkg` file you can drag into Anki. Each video becomes one note
with two cards: a recall card (channel + duration → title) and a review
card (title + link → "do you remember what this covers?"). Useful for
study-heavy playlists — lectures, conference talks, language videos.

Add `--thumbnails` if you want the video thumbnails embedded. They're
downloaded from YouTube's CDN with a size cap, so large playlists don't
blow up.

Re-running updates existing cards in place, so editing a playlist on
YouTube and re-exporting won't duplicate your deck.

## For scripting

All the prompts in interactive mode map to CLI flags. If you're wiring this
into a cron job, a CI pipeline, or a shell alias, skip the menu:

```bash
plp "https://youtube.com/playlist?list=..." --to notion-api --notion-parent PAGE_ID
plp "https://youtube.com/playlist?list=..." --to notion-md --copy
plp "https://youtube.com/playlist?list=..." --to obsidian --vault ~/MyVault
plp "https://youtube.com/playlist?list=..." --to anki --thumbnails
```

Passing a URL positionally makes `--to` required, and the interactive flow is
skipped entirely. Tokens still only come from env or config — never argv —
because argv ends up in shell history.

Full flag reference is `plp --help`.

## Compared to alternatives

Being honest because I'd want someone to be honest with me:

**`yt-dlp --flat-playlist --print "%(title)s"`** — the serious answer for
anyone who just wants a list of titles. If you're technical and that's all
you need, use it. This tool uses yt-dlp under the hood for scraping and
then focuses entirely on the export side, which yt-dlp doesn't do.

**TubeBuddy / VidIQ / YouTube browser extensions** — more features, but
closed-source, ad-supported, and they don't integrate with Notion or
Obsidian. If you want an analytics tool, they're better. If you want to
turn a playlist into a study system, they're not the right shape.

**Notion Web Clipper** — works one video at a time. That's the problem
this tool solves.

## Configuration

All settings live in `~/.config/playlistpipe/config.toml` (or
`$XDG_CONFIG_HOME/playlistpipe/config.toml` if you set that). Full example:

```toml
[notion]
token = "secret_..."
default_parent_page_id = "abc123..."
# default_database_id = "xyz789..."    # if you always append to the same DB

[obsidian]
vault_path = "~/MyVault"

[defaults]
output_dir = "~/playlistpipe-out"
```

Environment variables override the config file:

- `NOTION_TOKEN` or `PLAYLISTPIPE_NOTION_TOKEN`
- `PLAYLISTPIPE_OBSIDIAN_VAULT`
- `PLAYLISTPIPE_OUTPUT_DIR`

Tokens on the command line are deliberately not supported — they end up in
your shell history, and that's a bad default.

## What it won't do

- **Download videos.** Use `yt-dlp` for that. This is metadata only.
- **Extract transcripts.** `youtube-transcript-api` does that well.
- **Summarize with AI.** Out of scope. Pipe the output to your tool of
  choice.
- **Scrape private playlists.** We don't ask for your YouTube login. If a
  playlist is public or unlisted, it works; if it's private, it doesn't.
- **Work on channel pages.** Playlists only. A channel's "uploads" is a
  playlist — use that URL instead of the channel URL.

## Development

```bash
git clone https://github.com/srimur/playlistpipe
cd playlistpipe
pip install -e ".[dev]"
pytest
```

The test suite is strict on purpose — security-relevant behavior (URL
validation, HTML escaping in Anki cards, path traversal checks) has
regression tests you shouldn't remove without replacing.

Architecture:

```
src/playlistpipe/
├── cli.py                   # argparse, dispatch to exporters
├── interactive.py           # questionary-driven menu flow (plp with no args)
├── config.py                # XDG config, env precedence, save()
├── logging_setup.py         # token redaction filter
├── core/
│   ├── models.py            # Video, Playlist, Exporter protocol
│   ├── scraper.py           # yt-dlp wrapper, returns Playlist
│   └── utils.py             # url parsing, path safety, http session
└── exporters/
    ├── notion_api.py
    ├── notion_markdown.py
    ├── obsidian.py
    └── anki.py
```

Adding a new exporter is three things: a class implementing the `Exporter`
protocol, a config dataclass, and wiring in `cli.py`. See existing
exporters for the pattern.

## Security notes

- Notion tokens are read from env or config only, never from argv; they're
  redacted from all log output via a `logging.Filter`.
- Every user-sourced string that becomes an Anki card field is passed
  through `html.escape(quote=True)`. Anki's renderer is a WebEngine that
  executes JS, so this matters.
- Thumbnail downloads are restricted to YouTube's CDN hosts, capped at
  2 MiB, and have explicit timeouts. No arbitrary HTTP fetches.
- Output paths are validated with `Path.is_relative_to()` against the
  configured output directory. No traversal via `--deck-name "../../"`.

If you find something I missed, open an issue or email me directly.

## License

MIT.

## Credits

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) does the actual scraping.
- [genanki](https://github.com/kerrickstaley/genanki) builds the Anki packages.
- Notion's API is documented well enough that the exporter was an evening's
  work. That's rare, and appreciated.
