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
- **Accounts.** Register and sign in; every review belongs to one user and is invisible to the others.
- **Runs anywhere.** One Docker container, or plain Python. No database, no dependencies beyond the Python
  standard library.

> **Accounts:** people register with a username and password and see only their own reviews. Reviews are usually
> confidential, so by default the container only listens on `127.0.0.1`. See [Security](#security-and-privacy)
> before exposing it, and serve it over HTTPS if you do.

---

## Contents

1. [Quick start](#quick-start)
2. [Configuration](#configuration)
3. [Deploying with Dockhand (or any Git-based container manager)](#deploying-with-dockhand-or-any-git-based-container-manager)
4. [Using the app](#using-the-app)
5. [Maps: defining a review form](#maps-defining-a-review-form)
6. [Bundled review forms](#bundled-review-forms)
7. [Data, backups and moving between machines](#data-backups-and-moving-between-machines)
8. [Security and privacy](#security-and-privacy)
9. [Troubleshooting](#troubleshooting)
10. [Development](#development)
11. [License](#license)

---

## Quick start

The image is published at `ghcr.io/edos08/review-workbench` (Linux `amd64` and `arm64`, so it also runs on a
Raspberry Pi or an ARM NAS). You do not need to clone or build anything.

### With Docker Compose (recommended)

Save this as `compose.yaml` in an empty folder (it is also the `compose.yaml` of this repository):

    # Review Workbench. Every setting is an environment variable with a default, so it can be
    # configured without editing this file (e.g. in the environment-variables panel of Dockhand,
    # Portainer, or a local .env file). See .env.example and the README for what each one does.
    services:
      review-workbench:
        image: ghcr.io/edos08/review-workbench:latest
        restart: unless-stopped
        ports:
          # Default 127.0.0.1: reachable only from the server itself.
          # Set BIND_ADDRESS=0.0.0.0 to reach it from your network (and use HTTPS, see the README).
          - "${BIND_ADDRESS:-127.0.0.1}:${HOST_PORT:-8000}:8000"
        environment:
          PUID: ${PUID:-1000}
          PGID: ${PGID:-1000}
          MAX_PDF_MB: ${MAX_PDF_MB:-100}
          ALLOW_REGISTRATION: ${ALLOW_REGISTRATION:-true}
        volumes:
          # A normal folder on the host (a bind mount, not a Docker-managed volume), so you can open,
          # copy and back it up directly. On a server use an ABSOLUTE path, e.g. /srv/review-workbench/data.
          - ${DATA_PATH:-./data}:/data

Then start it:

    docker compose up -d

Open <http://localhost:8000>. Your data appears in `./data/` next to the file.

Every setting is an environment variable with a default. Put overrides in a `.env` file next to `compose.yaml`
(start from [`.env.example`](.env.example)) or pass them inline:

    HOST_PORT=9000 DATA_PATH=/srv/reviews PUID=$(id -u) PGID=$(id -g) docker compose up -d

See [Configuration](#configuration) for all of them.

### With a single `docker run`

    docker run -d --name review-workbench --restart unless-stopped \
      -p 127.0.0.1:8000:8000 \
      -e PUID=$(id -u) -e PGID=$(id -g) \
      -v "$HOME/review-workbench-data:/data" \
      ghcr.io/edos08/review-workbench:latest

### Build from source instead

    git clone https://github.com/edos08/review-workbench.git
    cd review-workbench
    docker compose -f compose.yaml -f compose.build.yaml up -d --build

### Without Docker

Needs Python 3.11 or newer. Nothing to install.

    python3 server.py                # http://127.0.0.1:8000, data in ./data
    PORT=9000 DATA_DIR=~/reviews python3 server.py

**Pin a version** for a server you rely on: change the tag in the `image:` line of `compose.yaml` from `latest` to a release
(for example `ghcr.io/edos08/review-workbench:1.0.0`). Release tags are published as `X.Y.Z` and `X.Y`; `latest` follows the
`main` branch.

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
| `ALLOW_REGISTRATION` | `true` | Whether anyone who reaches the page can create an account. The very first account can always be created. Set `false` once everyone who needs one has signed up. |

**App settings (read by `server.py`, also usable without Docker)**

| Variable | Default | What it does |
|---|---|---|
| `DATA_DIR` | `./data` (`/data` in the image) | Where the app stores everything. |
| `HOST` | `127.0.0.1` (`0.0.0.0` in the image) | Address the server listens on. |
| `PORT` | `8000` | Port the server listens on (inside the container it is always 8000). |
| `MAX_PDF_MB` | `100` | As above. |
| `ALLOW_REGISTRATION` | `true` | As above. |
| `LEGACY_REVIEWS_DIR` | unset | Folder of old single-file `*.json` reviews to import at startup. Copies only, originals are never touched. Use once, then unset: while it is set, a migrated review you delete is imported again at the next start. |

Invalid values (for example `MAX_PDF_MB=abc`, `ALLOW_REGISTRATION=maybe`, `PUID=abc`) stop the container at startup with a clear message.

---

## Deploying with Dockhand (or any container manager)

Because the image is published, a manager only needs the compose file and a few environment variables; nothing is
built on your server. The steps below follow the [Dockhand manual](https://dockhand.pro/manual/); menu and button
names can differ between versions.

**Before you start:** the image package must be **public** on GitHub Container Registry, otherwise the pull fails
with an authorization error (for your own fork: after the first publish, open the package on GitHub, *Package
settings*, and set the visibility to Public). A public image needs no login.

### Option A: paste the compose file as a normal stack (simplest)

1. In Dockhand open **Stacks** and click **Create stack**; the YAML editor opens.
2. Name the stack and paste the contents of [`compose.yaml`](compose.yaml).
3. In the **environment variables** panel on the right add your settings (see the table below).
4. Click **Create & start** (or **Create**, then deploy).
5. Open `http://<server>:<HOST_PORT>`.

### Option B: a Git stack that tracks this repository

1. Create a stack from a Git repository with
   - **Repository:** `https://github.com/edos08/review-workbench` (public, no credentials needed)
   - **Branch:** `main`
   - **Compose file path:** `compose.yaml` (Dockhand's default is `docker-compose.yml`, so change it)
   - **Build images on deploy:** leave off, the image is pulled, not built
2. Add the environment variables in the stack's variables panel. The manual says values set there are available to
   `${VAR}` substitution in the compose file, which is how this project reads them.
3. Click **Deploy**. Dockhand clones the repository on the first deploy; until then it may say the compose file is
   not available.
4. Use **Dockhand 1.0.40 or newer**: older versions did not pass the panel variables when starting, stopping or
   removing a Git stack ([bug report](https://github.com/Finsys/dockhand/issues/1313), fixed in 1.0.40).

### Variables to set

| Variable | Example | Why |
|---|---|---|
| `DATA_PATH` | `/srv/review-workbench/data` | **Absolute folder on the Docker host** that holds all data. Do not leave the default `./data`: in a Git stack a relative path points into Dockhand's own clone of the repository, and with a remote Docker host it points nowhere useful. Docker creates the folder if it is missing. |
| `BIND_ADDRESS` | `0.0.0.0` | Only if you want to open the app from another computer. The default `127.0.0.1` is reachable from the server only. |
| `HOST_PORT` | `8000` | Change if the port is taken. |
| `PUID`, `PGID` | `1000`, `1000` | Your user on the server (`id -u`, `id -g`), so you can read and back up the data folder. |
| `ALLOW_REGISTRATION` | `true` | Set to `false` after the people who need accounts have registered, so strangers on your network cannot create one. |

### Updating

Redeploy the stack with the **Pull images** option so Docker fetches the new image. The data folder is outside
the container and is never touched.

**Moving existing reviews to the server:** on the old machine press **Backup (.zip)** on a review, then on the
server press **Restore from backup** and pick the file.

---

## Using the app

### Accounts

- The first time you open the app it asks you to **create the first account**. Any reviews that already exist on
  the server (made before accounts were added) become that account's.
- After that, people **Create an account** (username 3-32 characters: letters, digits, `.`, `-`, `_`; password 8
  or more characters) or **Sign in**. Sessions last 30 days. The top right of every page has **Password** (change
  it; this signs out your other devices) and **Sign out**.
- Everyone sees only their own reviews. Someone else's review behaves exactly like one that does not exist.
- There is no password reset by email. An administrator resets a password from a terminal on the server (this
  also signs that user out everywhere; `list` shows the usernames):

      docker exec -it <container> python3 auth.py list
      docker exec -it <container> python3 auth.py reset alice

  Without Docker run `python3 auth.py reset alice` in the project folder (with the same `DATA_DIR`).
- **My maps** (your own form templates) are shared by everyone on the server.

### Home page

- **Start a new review:** open the **Review form** dropdown (type to search, grouped into conferences, journals,
  general and *My maps*), check the preview on the right (how many fields, how closely it follows the venue's own
  form, a link to the venue's guidelines), optionally type the paper's title, and press **Start review**. The form
  you used last is remembered.
- **Add my own map…** adds a map file of yours to the dropdown (see [Maps](#maps-defining-a-review-form)).
- **Your reviews:** one card per review with its form, last update, whether a PDF is attached, and a progress bar of
  required fields. Filter by *All / In progress / Complete*, search by title, **Open**, download a **Backup**, or
  **Delete** (this removes the review's folder, including its map and PDF).
- **Restore from backup** imports a `.zip` made by the backup button (or an old single-file `.json`) as a new review.

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

1. **Open the paper beside the form.** A blue tab on the right edge of the screen says **Open the PDF here**; click it
   and a side panel opens with a **Select PDF…** button (the **Open PDF…** button in the top bar does the same).
   You can also **drag a PDF file anywhere onto the page**. The PDF is uploaded to *your* server and stored in the
   review's folder, so it opens again whenever you open the review, from any device. A review that already has a PDF
   opens with the panel open.
   - **Hide** collapses the panel (the tab on the edge turns into **Show the PDF**) without detaching anything;
     **Change** picks another file; **Remove** detaches the PDF from the review.
2. **Leave a note:** pick the **📝 Note** tool and click anywhere on the page (a citation too) to drop a sticky note, then type
   your text; hover it to read it, click it to edit or delete it. An empty note is not kept.
3. **Annotate text:** select text and a small bar appears (five highlight colours: yellow, orange, red, green, blue; underline; strikethrough; or
   highlight + comment). Or pick a tool in the toolbar (*Highlight*, *Underline*, *Strike*) and every selection is
   marked immediately; pick *Select* to go back.
4. **Click a mark** to change its type or colour, write a comment (shown as a speech bubble on the page), or delete it.
5. **☰ Annotations** lists them all; click one to jump there. *Copy all* puts them on the clipboard as a Markdown list,
   handy for your notes.
6. **Follow citations:** click a citation or any internal link in the PDF. A **↩ Back to p.N** button appears; click
   it (or press `Alt+←`) to return to the exact spot you left. Several jumps stack up. The 📍 button saves your
   current spot by hand, for PDFs whose citations are not clickable links.
7. **Undo/redo** annotations with `Ctrl/Cmd+Z` and `Ctrl/Cmd+Shift+Z` (inside a text field `Ctrl/Cmd+Z` keeps undoing
   text as usual).

Annotations, page and zoom are saved with the review. The viewer is [PDF.js](https://mozilla.github.io/pdf.js/)
bundled with the app: it works offline and nothing is sent to third parties.

### Form menu (per review)

Each review carries its **own copy of its map**, so a review always stays openable and a backup is complete. The
**Form** menu in the top bar lets you change it:

- **Switch to another form** applies another template to this review. Your answers are kept wherever the field ids
  match (the bundled forms share `summary`, `strengths`, `weaknesses`, `questions`, `confidence` and the paper title),
  so moving a review between venues keeps most of your text.
- **Upload a map file…** replaces this review's map with a file from your computer.
- **Download this map** saves the map file.
- **Reset to "<form>"** goes back to the template the review is based on.

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

### Your own maps (the "My maps" group)

A map you add on the home page (**Add my own map…**) is stored in `data/maps/<name>.map`, listed under **My maps** in
the dropdown, and can be removed again from its preview. You can also drop `.map` files straight into that folder.
A file with the same name as a bundled form replaces it in the list until you remove it. Reviews already created from
a map keep their own copy.

Besides the usual widgets, a map can describe itself for the dropdown with attributes on its `Sitemap`:

| Attribute | Meaning |
|---|---|
| `label` | Name shown in the list |
| `venue` | Venue or journal, shown under the name |
| `kind` | `conference`, `journal` or `general` (decides the group) |
| `year` | Year the form is modelled on |
| `fidelity` | `form` (follows the published form), `guidelines` (built from public guidance, verify wording) or `generic` |
| `source` | URL of the venue's reviewer guidelines (the "Venue guidelines" link) |
| `description` | One or two sentences shown in the preview |

---

## Bundled review forms

The app ships with 21 forms in [`maps/`](maps). Each one explains every field in plain language ("What does
this mean?") and ends with a private reviewer workbench (checklist and notes that are never exported).

**How accurate are they?** Venues change their review forms every year and rarely publish them as a file, so each
form carries an accuracy badge:

- **follows the published form:** modelled on the form the venue publishes (field names, scales and their labels).
- **from public guidelines:** built from the venue's public reviewer guidance. The official form could not be
  verified word for word, so field names or scales may differ. Treat it as a structured checklist.
- **generic:** not tied to a venue.

Whatever the badge, **follow the form you were actually given** when you copy your answers into the venue's system,
and adapt the map (Form menu, upload your own version) if it differs. Corrections and new venues are welcome as pull
requests. The explanations are general reviewing guidance, not the venue's official instructions.

### Conferences

| Form | File | Accuracy | Source |
|---|---|---|---|
| AAAI 2026 - Review | `maps/aaai-2026.map` | from public guidelines | [aaai.org](https://aaai.org/conference/aaai/aaai-26/review-process/) |
| AAMAS 2027 - Official Review | `maps/aamas-2027.map` | follows the published form | [warwick.ac.uk](https://warwick.ac.uk/fac/sci/dcs/aamas2027/guidelines-and-policies/reviewer-guidelines/) |
| ACL Rolling Review (ACL / EMNLP / NAACL) - Review | `maps/acl-rolling-review.map` | from public guidelines | [aclrollingreview.org](https://aclrollingreview.org/reviewerguidelines) |
| ACM CHI - Review (2024) | `maps/chi-2024.map` | from public guidelines | [chi2025.acm.org](https://chi2025.acm.org/guide-to-a-successful-submission/) |
| CVPR / ICCV / ECCV - Review | `maps/cvf-conference.map` | from public guidelines | [cvpr.thecvf.com](https://cvpr.thecvf.com/Conferences/2025/ReviewerGuidelines) |
| ICLR 2025 - Review | `maps/iclr-2025.map` | follows the published form | [www.iclr.cc](https://www.iclr.cc/Conferences/2025/ReviewerGuide) |
| ICML 2025 - Review | `maps/icml-2025.map` | follows the published form | [icml.cc](https://icml.cc/Conferences/2025/ReviewerInstructions) |
| ICRA / IROS (IEEE RAS) - Review | `maps/icra-iros.map` | from public guidelines | [ieee-ras.org](https://ieee-ras.org/conferences-workshops/fully-sponsored/icra/information-for-reviewers) |
| IJCAI - Review (2022 form) | `maps/ijcai-2022.map` | from public guidelines | [www.ijcai-22.org](https://www.ijcai-22.org/wp-content/uploads/2022/01/Review-form-IJCAI-ECAI-2022-2.pdf) |
| KDD 2025 - Research track review | `maps/kdd-2025.map` | from public guidelines | [kdd.org](https://kdd.org/) |
| NeurIPS 2025 - Review | `maps/neurips-2025.map` | follows the published form | [neurips.cc](https://neurips.cc/Conferences/2025/ReviewerGuidelines) |

### Journals

| Form | File | Accuracy | Source |
|---|---|---|---|
| IEEE Transactions - Referee report | `maps/ieee-transactions.map` | from public guidelines | [ieeeaccess.ieee.org](https://ieeeaccess.ieee.org/reviewers/reviewer-guidelines/) |
| JMLR - Referee report | `maps/jmlr.map` | from public guidelines | [www.jmlr.org](https://www.jmlr.org/reviewer-guide.html) |
| Nature Portfolio - Referee report | `maps/nature-referee-report.map` | from public guidelines | [www.nature.com](https://www.nature.com/ncomms/for-reviewers/reviewer-faqs) |
| PLOS ONE - Reviewer form | `maps/plos-one.map` | from public guidelines | [journals.plos.org](https://journals.plos.org/plosone/s/reviewer-guidelines) |
| TMLR - Review (Transactions on Machine Learning Research) | `maps/tmlr.map` | follows the published form | [jmlr.org](https://jmlr.org/tmlr/reviewer-guide.html) |

### General

| Form | File | Accuracy | Source |
|---|---|---|---|
| Blank review (free text) | `maps/blank.map` | generic | - |
| Generic conference paper review | `maps/generic-conference.map` | generic | - |
| Generic journal referee report | `maps/generic-journal.map` | generic | - |
| Map tutorial (every widget once) | `maps/map-tutorial.map` | generic | - |
| Short review (workshop / extended abstract) | `maps/workshop-short.map` | generic | - |

`map-tutorial.map` is the one to read when you want to write your own: it uses every widget once, with comments.
To make a form for a new venue, copy the closest bundled map, change the `label`, `venue` and fields to match the
venue's own form (field names and options exactly as the venue words them, so pasting is easy), and add `help` text
where a newcomer would hesitate.

---

## Data, backups and moving between machines

Everything is under one folder (`DATA_PATH` on the host, `/data` in the container):

    data/
      users.json                   accounts: usernames and password hashes (never the passwords)
      .secret                      key that signs session cookies; delete it to sign everyone out
      maps/                        your own map templates ("My maps")
      reviews/
        <id>/
          review.json              owner, answers, annotations, PDF position
          map.map                  this review's own map
          paper.pdf                the paper (if attached)

- **Backup:** copy the whole `data/` folder (rsync, Time Machine, your server's backup job), or press
  **Backup (.zip)** on a single review.
- **Restore:** put the folder back, or use **Restore from backup** with a `.zip`.
- **Each review folder is self-contained**, and `review.json` names its `owner` (a user id from `users.json`).
  To move a review to another instance, use **Backup (.zip)** and **Restore from backup** (the restoring user
  becomes the owner). Dropping a bare folder into `data/reviews/` only works if its `owner` matches a user there.
- Files are written atomically and readable by the user set with `PUID`/`PGID`.

---

## Security and privacy

- **Accounts, not a full identity system.** Passwords are stored as salted scrypt hashes; sessions are signed
  `HttpOnly`, `SameSite=Strict` cookies (also `Secure` when a reverse proxy sends `X-Forwarded-Proto: https`);
  repeated wrong passwords for one username are throttled. Each user can reach only their own reviews. There is
  no email, no password reset and no two-factor sign-in. Administrators with access to the data folder can read
  everything in it.
- **Use HTTPS** whenever the app is reachable beyond your own machine: without it, passwords and papers travel in
  clear text.
- **Close registration** (`ALLOW_REGISTRATION=false`) once everyone has an account.
- **Safe default:** the container publishes the port on `127.0.0.1` only. To open it from other machines set
  `BIND_ADDRESS=0.0.0.0`, and only on a network you trust.
- **Prefer not to expose it directly to the internet.** If you need remote access, put it behind a reverse proxy
  that terminates HTTPS (Caddy, Traefik, nginx) or reach it through a VPN.
- The PDF viewer and all scripts are served by the app itself. Papers go only to *your* server.
- Reviewing is confidential: keep the data folder on storage you control, and check the venue's rules about
  where papers may be stored and which tools may process them.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Container restarts, log says `Permission denied` on the data folder | Should not happen: the entrypoint fixes ownership of the data folder at start. Check that `PUID`/`PGID` are numbers and that the host filesystem allows `chown` (some network shares do not). |
| Page does not open from another computer | `BIND_ADDRESS` is `127.0.0.1` (the default). Set `0.0.0.0` and redeploy, and check the firewall. |
| `pull access denied` / `unauthorized` when pulling the image | The package is private, or the `image:` name in `compose.yaml` has a typo. Set the package to Public on GitHub (Packages, *Package settings*), or log in with `docker login ghcr.io`. |
| `Could not upload the PDF: upload too large` | Raise `MAX_PDF_MB`. |
| `Could not upload the PDF: not a PDF file` | The file does not start with `%PDF-`. |
| `Map not replaced: map error: ...` | The uploaded map has a syntax error, usually reported with its line number. The review keeps its old map. |
| A review says its map cannot be loaded | Press **Reset to its template** on that page. |
| PDF text looks soft | Use your browser's default page zoom (`Ctrl/Cmd+0`) and a zoom of 100% or more in the PDF toolbar. |
| Browser shows an old version after an update | Hard refresh (`Ctrl/Cmd+Shift+R`). |
| Reviews written with an older single-file version | Start once with `LEGACY_REVIEWS_DIR=/path/to/old/reviews`; each one is copied into the new layout. A PDF that the old version kept in your browser is uploaded to the server the first time you open that review in the same browser and address. |

---

## Development

    python3 -m unittest discover -s tests -v      # unit tests, standard library only
    docker compose -f compose.yaml -f compose.build.yaml up -d --build   # run your local changes in a container

    server.py        HTTP routing, configuration (stdlib http.server)
    auth.py          accounts, password hashing, session cookies, password-reset command
    store.py         on-disk storage: reviews, maps, PDFs, zip backup, migration (no HTTP)
    mapparser.py     the map language parser and validator
    maps/            the bundled review forms (parsed and checked by the tests)
    static/          the single-page frontend (vanilla JS) and vendored PDF.js
    tests/           unit tests for the store, server, map parser and bundled maps
    .github/         workflow that tests, builds and publishes the image to ghcr.io

The frontend has no build step. The HTTP API is implemented in `server.py`'s `_api` method: reviews are
`/api/reviews[/<id>]`, with `/map`, `/map.map`, `/pdf` and `/backup.zip` underneath, plus `/api/templates[/<id>]` for the form library and `/healthz`.

---

## License

[MIT](LICENSE). The bundled PDF.js (in `static/vendor/pdfjs`) is distributed under the Apache License 2.0; its license
file is included in that folder.
