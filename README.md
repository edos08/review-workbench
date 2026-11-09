# Review Workbench

A small self-hosted web app for writing **peer reviews** (conference papers, journal manuscripts, workshop
submissions) in a browser instead of a Word file or a web form you cannot save.

- **Your form, defined by a text file.** Each venue's review form is a small "map" file, in the spirit of
  openHAB sitemaps. A new venue is a new file, no code.
- **Built-in help for newcomers.** A map can attach a long, plain-language explanation to every field
  ("What does this mean?"): what is asked, what a good answer contains, common mistakes.
- **The paper next to the form.** Open the paper's PDF in a side pane with highlight, underline and strikethrough,
  comments, and a **Back button** after you click a citation, so you return to where you were reading.
- **Autosave and backup.** Every review is a folder with your answers, its map and the PDF. Download it as a
  `.zip`, restore it anywhere.
- **Runs anywhere.** One Docker container, or plain Python. No database, no dependencies beyond the Python
  standard library.

> **There is no login.** Anyone who can reach the port can read and edit every review, and reviews are usually
> confidential. By default the container only listens on `127.0.0.1`. See [Security](#security-and-privacy)
> before exposing it.

---

## Contents

1. [Quick start](#quick-start)
2. [Configuration](#configuration)
3. [Deploying with Dockhand (or any Git-based container manager)](#deploying-with-dockhand-or-any-git-based-container-manager)
4. [Using the app](#using-the-app)
5. [Maps: defining a review form](#maps-defining-a-review-form)
6. [Example maps](#example-maps)
7. [Data, backups and moving between machines](#data-backups-and-moving-between-machines)
8. [Security and privacy](#security-and-privacy)
9. [Troubleshooting](#troubleshooting)
10. [Development](#development)
11. [License](#license)

---

## Quick start

### With Docker

    git clone https://github.com/edos08/review-workbench.git
    cd review-workbench
    docker compose up -d --build

Open <http://localhost:8000>. Your data appears in `./data/` next to `compose.yaml`.

Change anything (port, data folder, ...) with environment variables, either inline

    HOST_PORT=9000 DATA_PATH=/srv/reviews docker compose up -d --build

or by copying `.env.example` to `.env` and editing it. See [Configuration](#configuration).

### Without Docker

Needs Python 3.11 or newer. Nothing to install.

    python3 server.py                # http://127.0.0.1:8000, data in ./data
    PORT=9000 DATA_DIR=~/reviews python3 server.py

---

## Configuration

Everything is an environment variable with a default. `compose.yaml` reads the first group; the app itself
reads the second.

**Container settings (read by `compose.yaml`)**

| Variable | Default | What it does |
|---|---|---|
| `DATA_PATH` | `./data` | **Host folder** that holds all data. Mounted into the container as `/data` (a normal folder, not a Docker-managed volume, so you can open and back it up). Use an **absolute path** on a server, e.g. `/srv/review-workbench/data`. |
| `HOST_PORT` | `8000` | Port on the host. |
| `BIND_ADDRESS` | `127.0.0.1` | Host address the port is bound to. `127.0.0.1` = only reachable from the machine itself. Set `0.0.0.0` to reach it from your network (see [Security](#security-and-privacy)). |
| `PUID` / `PGID` | `1000` / `1000` | User and group id the app runs as. Set them to your own host user (`id -u`, `id -g`) and every file in `DATA_PATH` belongs to you, so you can read, copy and delete backups without `sudo`. |
| `MAX_PDF_MB` | `100` | Largest PDF you can attach, in megabytes. |
| `CONTAINER_NAME` | `review-workbench` | Container name. |
| `IMAGE_NAME` | `review-workbench:latest` | Name of the image that gets built. |
| `RESTART_POLICY` | `unless-stopped` | Docker restart policy. |

**App settings (read by `server.py`, also usable without Docker)**

| Variable | Default | What it does |
|---|---|---|
| `DATA_DIR` | `./data` (`/data` in the image) | Where the app stores everything. |
| `HOST` | `127.0.0.1` (`0.0.0.0` in the image) | Address the server listens on. |
| `PORT` | `8000` | Port the server listens on (inside the container it is always 8000). |
| `MAX_PDF_MB` | `100` | As above. |
| `LEGACY_REVIEWS_DIR` | unset | Folder of old single-file `*.json` reviews to import at startup. Copies only, originals are never touched. Use once, then unset: while it is set, a migrated review you delete is imported again at the next start. |

Invalid values (for example `MAX_PDF_MB=abc`, `PUID=abc`) stop the container at startup with a clear message.

---

## Deploying with Dockhand (or any Git-based container manager)

Container managers that deploy from a Git repository usually do not let you edit `compose.yaml`, only set
environment variables. That is why every setting above is a variable with a sensible default.

The menu names differ between tools and versions; the idea is the same:

1. **Create a stack from a Git repository.**
   - Repository URL: `https://github.com/edos08/review-workbench`
   - Branch: `main`
   - Compose file: `compose.yaml`
2. **Add environment variables** in the stack's environment section. Typically:

   | Variable | Example | Why |
   |---|---|---|
   | `DATA_PATH` | `/srv/review-workbench/data` | Absolute folder on the server. Keep it **outside** the checkout so redeploys never touch it. Docker creates it if it does not exist. |
   | `BIND_ADDRESS` | `0.0.0.0` | Only if you want to open the app from another computer on your network. |
   | `HOST_PORT` | `8000` | Change if the port is taken. |
   | `PUID`, `PGID` | `1000`, `1000` | Your user on the server, so you can read the backup folder. Find them with `id`. |

3. **Deploy.** The image is built from the repository (`build: .` in `compose.yaml`). Open
   `http://<server>:<HOST_PORT>`.
4. **Update later** by pulling and redeploying the stack from the manager. Your data stays in `DATA_PATH`.

If your manager cannot build images from a Git context, build the image once on the server
(`docker build -t review-workbench:latest .`) and deploy the stack without a build step.

**Moving existing reviews to the server:** on the old machine open the home page and press **Backup (.zip)** on a
review. On the new server press **Restore from backup** and pick the file.

---

## Using the app

### Home page

- **Start review** creates a review from the default map.
- **Saved reviews:** open, download a **Backup (.zip)**, or delete (this removes the review's folder, including its
  map and PDF).
- **Restore from backup** imports a `.zip` produced by the backup button (or an old single-file `.json`) as a new
  review.

### Writing a review

Open a review. The page shows your form on the left and, once you attach a paper, the PDF on the right (drag the
divider to resize).

- Click **"What does this mean?"** under a field for the map author's explanation of that field and its scale.
- **Autosave:** changes are saved a moment after you stop typing; the top bar shows *Saved* / *Unsaved changes*.
  `Ctrl/Cmd+S` saves immediately (and stops the browser from saving the page).
- **Required fields** (marked `*`) feed the *Required: n/m* counter, so you can see at a glance what is still empty.
  It never blocks anything.
- **Markdown fields** have *Write* and *Preview* tabs. `$...$` is shown as a formula marker in the preview (full
  LaTeX is not rendered); paste the text as is into systems that render it.
- **Copy** next to a text field puts that field on the clipboard. **Export text** copies the whole review as
  Markdown (one `##` section per field) and also downloads it as a `.md` file.
- **Private fields** (checklists, reading notes) are saved with the review but left out of the exported text.
- A **Text field with the id `paper_title`** also names the review in the list until you rename it by hand.

### The PDF pane

1. Press **Select file (PDF)…** and choose the paper. It is uploaded to *your* server and stored in the review's
   folder; it opens again whenever you open the review, from any device.
2. **Annotate:** select text and a small bar appears (four highlight colours, underline, strikethrough, or
   highlight + comment). Or pick a tool in the toolbar (*Highlight*, *Underline*, *Strike*) and every selection is
   marked immediately; pick *Select* to go back.
3. **Click a mark** to change its type or colour, write a comment (shown as a speech bubble on the page), or delete it.
4. **☰ Annotations** lists them all; click one to jump there. *Copy all* puts them on the clipboard as a Markdown list,
   handy for your notes.
5. **Follow citations:** click a citation or any internal link in the PDF. A **↩ Back to p.N** button appears; click
   it (or press `Alt+←`) to return to the exact spot you left. Several jumps stack up. The 📍 button saves your
   current spot by hand, for PDFs whose citations are not clickable links.
6. **Undo/redo** annotations with `Ctrl/Cmd+Z` and `Ctrl/Cmd+Shift+Z` (inside a text field `Ctrl/Cmd+Z` keeps undoing
   text as usual).

Annotations, page and zoom are saved with the review. The viewer is [PDF.js](https://mozilla.github.io/pdf.js/)
bundled with the app: it works offline and nothing is sent to third parties.

### Map menu (per review)

Each review carries its **own copy of its map**, so a review always stays openable and a backup is complete.

- **Upload a map…** replaces this review's map. Your answers are kept: fields are matched by `id`, so answers to
  fields that no longer exist are simply not shown (they stay in the saved file).
- **Download this map** saves the map file.
- **Reset to default map** goes back to the default.

A map with a syntax error is rejected with an explanation (usually the line number) and the review keeps its previous map.

---

## Maps: defining a review form

A map is a plain text file. Here is a complete one:

    Sitemap label="My venue - review" venue="My venue" {

      Info label="Before you start" text="Read the paper once, then fill this in."

      Frame label="Written part" {
        Markdown id=summary label="Summary" required hint="What does the paper do?" help="""
          WHAT IS ASKED
          A neutral summary in your own words.

          - Problem, idea, evidence
          - No judgement yet
        """
      }

      Frame label="Scores" {
        Choice id=score label="Overall score" required {
          Option value=2 label="2 - Accept" help="Shown under the option"
          Option value=1 label="1 - Reject"
        }
        Checkbox id=award label="Nominate for a best-paper award"
      }

      Frame label="My notes" private {
        Textarea id=notes label="Reading notes" rows=8
      }
    }

### Syntax

- A map is **one `Sitemap { ... }`** containing widgets. Widgets are written as a **Type** (capitalised) followed
  by `key=value` attributes; containers (`Sitemap`, `Frame`, `Choice`) then have `{ ... }` with children.
- **Values:** `"text"` (use `\"` for a quote), `"""multi-line text"""` (the common indentation is removed), numbers,
  `true`/`false`, or a bare word. **Flags** are lowercase words without a value (`required`, `private`).
- **Comments** start with `#`. Whitespace and line breaks are free.
- **Ids** (`id=`) must be unique in the map, start with a letter or `_`, and use only letters, digits and `_`.
  They are the keys your answers are saved under, so keep them stable when you edit a map.

### Widgets

| Widget | Purpose | Attributes |
|---|---|---|
| `Sitemap` | The root. Exactly one per file. | `label` (title), `venue` (shown in the top bar) |
| `Frame` | A titled section; can be nested. | `label`, `private` |
| `Info` | A static box of text, e.g. instructions. | `label`, `text` |
| `Text` | One-line text. | `id`, `label`, `required`, `private`, `hint`, `help`, `placeholder` |
| `Textarea` | Multi-line plain text. | as `Text`, plus `rows` |
| `Markdown` | Multi-line text with a Write/Preview tab. | as `Textarea` |
| `Choice` | Pick one of several `Option`s. | `id`, `label`, `required`, `private`, `hint`, `help` |
| `Option` | One answer inside a `Choice`. | `value` (what is stored), `label` (what is shown), `help` (grey line below) |
| `Checkbox` | A yes/no box. | `id`, `label`, `required`, `private`, `hint`, `help` |

- `hint` is the short grey line under a label. `help` is the long expandable **"What does this mean?"** panel. In
  `help`, a line in ALL CAPS becomes a heading and lines starting with `- ` become bullets.
- `required` only feeds the progress counter. A required checkbox counts once you have toggled it.
- `private` on a `Frame` makes everything inside it private; on a single field it affects just that field. Private
  fields are saved but never exported, and are tagged "private" in the form.

### Writing help text for newcomers

The most useful maps explain each field to someone reviewing for the first time. A pattern that works well in
`help`:

    WHAT IS ASKED
    One or two sentences on what the venue wants here.

    WHAT TO CHECK
    - Concrete things to look for in the paper.

    HOW TO CHOOSE
    How to pick a score, and what separates neighbouring values.

    COMMON MISTAKES
    - What first-time reviewers get wrong.

### Setting the default map

New reviews copy `data/default.map`. It is created on first start from `defaults/default.map`, which reproduces the
OpenReview "Official Review" form of AAMAS 2027 with explanations for every field. To use another form as the
default, replace the file `data/default.map` in your data folder (copy any map over it); existing reviews keep the map
they already have. The explanations in the bundled map are general reviewing guidance: always check them against the
guidelines of the venue you are reviewing for.

---

## Example maps

The folder [`examples/`](examples) holds maps you can learn from or adapt. They are generic and are **not official
forms of any journal or conference**. Load one in a review via *Map → Upload a map…*, or copy it to
`data/default.map` to make it the default for new reviews.

| File | What it is |
|---|---|
| [`minimal.map`](examples/minimal.map) | Every widget type once, with comments on each line. Start here when writing your own. |
| [`conference-simple.map`](examples/conference-simple.map) | Compact conference review: summary, strengths, weaknesses, questions, a 1-6 score, confidence, award flag. |
| [`journal-article.map`](examples/journal-article.map) | Referee report: comments to authors, confidential note to the editor, four 1-4 ratings, accept / minor / major / reject. |
| [`workshop-short.map`](examples/workshop-short.map) | A very short review for workshops and extended abstracts. |

To make a map for a new venue: copy the closest example, rename the `label`, and change the fields and scales to
match the venue's own form (field names and options exactly as the venue words them, so pasting is easy). Add `help`
text where a newcomer would hesitate. Maps are welcome contributions.

---

## Data, backups and moving between machines

Everything is under one folder (`DATA_PATH` on the host, `/data` in the container):

    data/
      default.map                  map used by new reviews
      reviews/
        <id>/
          review.json              answers, annotations, PDF position
          map.map                  this review's own map
          paper.pdf                the paper (if attached)

- **Backup:** copy the whole `data/` folder (rsync, Time Machine, your server's backup job), or press
  **Backup (.zip)** on a single review.
- **Restore:** put the folder back, or use **Restore from backup** with a `.zip`.
- **Each review folder is self-contained.** You can copy one review to another instance by dropping its folder into
  that instance's `data/reviews/`, or through the zip.
- Files are written atomically and readable by the user set with `PUID`/`PGID`.

---

## Security and privacy

- **No authentication.** Anyone who can open the page can read, change and delete all reviews and papers.
- **Safe default:** the container publishes the port on `127.0.0.1` only. To open it from other machines set
  `BIND_ADDRESS=0.0.0.0`, and only on a network you trust.
- **Never expose it directly to the internet.** If you need remote access, put it behind a reverse proxy that
  authenticates (Authelia, Traefik/Caddy with basic auth or SSO, an authenticated tunnel) or reach it through a VPN,
  and use HTTPS.
- The PDF viewer and all scripts are served by the app itself. Papers go only to *your* server.
- Reviewing is confidential: keep the data folder on storage you control, and check the venue's rules about
  where papers may be stored and which tools may process them.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Container restarts, log says `Permission denied` on the data folder | Should not happen: the entrypoint fixes ownership of the data folder at start. Check that `PUID`/`PGID` are numbers and that the host filesystem allows `chown` (some network shares do not). |
| Page does not open from another computer | `BIND_ADDRESS` is `127.0.0.1` (the default). Set `0.0.0.0` and redeploy, and check the firewall. |
| `Could not upload the PDF: upload too large` | Raise `MAX_PDF_MB`. |
| `Could not upload the PDF: not a PDF file` | The file does not start with `%PDF-`. |
| `Map not replaced: map error: ...` | The uploaded map has a syntax error, usually reported with its line number. The review keeps its old map. |
| A review says its map cannot be loaded | Press **Reset to the default map** on that page. |
| PDF text looks soft | Use your browser's default page zoom (`Ctrl/Cmd+0`) and a zoom of 100% or more in the PDF toolbar. |
| Browser shows an old version after an update | Hard refresh (`Ctrl/Cmd+Shift+R`). |
| Reviews written with an older single-file version | Start once with `LEGACY_REVIEWS_DIR=/path/to/old/reviews`; each one is copied into the new layout. A PDF that the old version kept in your browser is uploaded to the server the first time you open that review in the same browser and address. |

---

## Development

    python3 -m unittest discover -s tests -v      # unit tests, standard library only

    server.py        HTTP routing, configuration (stdlib http.server)
    store.py         on-disk storage: reviews, maps, PDFs, zip backup, migration (no HTTP)
    mapparser.py     the map language parser and validator
    defaults/        the default map
    examples/        example maps (parsed by the tests)
    static/          the single-page frontend (vanilla JS) and vendored PDF.js
    tests/           unit tests for the store, server, map parser and bundled maps

The frontend has no build step. The HTTP API is implemented in `server.py`'s `_api` method: reviews are
`/api/reviews[/<id>]`, with `/map`, `/map.map`, `/pdf` and `/backup.zip` underneath, plus `/api/info` and `/healthz`.

---

## License

[MIT](LICENSE). The bundled PDF.js (in `static/vendor/pdfjs`) is distributed under the Apache License 2.0; its license
file is included in that folder.
