# Learning Observability

**Read it online: https://abutlabs.github.io/observability/**

A course on using this repository's observability stack to monitor and diagnose JAM
networks, including networks that mix clients. It starts from zero (what a metric is, how
to read a dashboard) and ends with a real investigation: a DEX soak test on six lasair
validators that failed after 25 minutes, followed from its first symptom to its cause, the
fix, and the run that proved it.

Every command in it comes from this repository's CLI and scripts, every metric name from
the code that exports it, and every number from a real run.

## What is in it

| Track | Lessons |
|---|---|
| **0 Welcome** | Why a JAM network needs observability · The words you need · Reading your first dashboard · How to use this course |
| **1 The stack** | The eight services · How the pieces fit together · Running the stack · Runs, annotations and links |
| **2 Telemetry from JAM nodes** | The `jam_*` metrics · Three paths, one label model · JIP-3 and JIP-2 · lasair as an example · Plugging in a new client |
| **3 Reading the dashboards** | How to read a dashboard · Network overview and Chain health · Logs and obs self-health · The lasair dashboards · The DEX and Soak runs dashboards · When something looks wrong: the drill-down path · PromQL basics · LogQL basics |
| **4 Soak tests** | What a soak proves · Running a soak · Reading the results |
| **5 Case study: a failing soak** | The symptom · The DEX backlog · Refine time · The logs · Where the window goes · The fix and the proof |
| **6 Mixed networks** | lasair and PolkaJam on one chain · What cross-client telemetry reveals · Adding another client |
| **Labs** | Start the stack · JIP-3 without Docker · Register a process · Replay the case study · PromQL exercises · LogQL exercises |

Each lesson takes 5 to 10 minutes to read. Start with
[track 0](content/00-welcome/01-why-observe.md).

## Three ways to read it

1. **Online**, at https://abutlabs.github.io/observability/: a sidebar, progress marks,
   light and dark themes.
2. **On GitHub**: the lessons are plain markdown in [`content/`](content/) and the labs
   in [`exercises/`](exercises/README.md). Links between lessons work there too.
3. **Locally**, exactly as Pages will serve it. From the repository's top folder:

   ```sh
   learning-observability/tools/build.sh
   cd learning-observability && python3 -m http.server 8000 --bind 127.0.0.1
   ```

   Then open http://127.0.0.1:8000/_site/. The site sits under a sub-path there, as it
   does on Pages (`/observability/`), so a link that works locally works online.

   While editing, you can skip the build: `cd learning-observability/site && python3 -m
   http.server 8000 --bind 127.0.0.1` serves the viewer straight from the source, and
   `site/content` and `site/exercises` are links to the markdown, so a saved edit shows on
   the next reload.

The pages load their markdown with `fetch`, which browsers refuse on `file://` URLs:
always serve over HTTP rather than opening the HTML file directly.

## How it is built

| Path | What |
|---|---|
| `content/<track>/<lesson>.md` | the lessons |
| `exercises/` | the labs and exercises (and `fake_node.py`, used by lab 3) |
| `site/` | the viewer: `index.html`, `lesson.html`, `css/`, `js/`, and `data/course.json`, the list of tracks and lessons both pages render from |
| `tools/check.py` | checks that `course.json` and the markdown agree, that every relative link and anchor resolves, and that the site uses relative URLs only |
| `tools/build.sh` | runs the check, copies the viewer and the markdown into `_site/` (ignored by git), and stamps asset URLs with a content hash (`tools/stamp_assets.py`) |

The viewer is plain HTML, CSS and JavaScript with no build step and no framework; it
renders markdown with [marked](https://marked.js.org) and highlights code with
[highlight.js](https://highlightjs.org), both from cdnjs. It is adapted from the viewer of
lasair's course, *learning-lasair* (same owner, MIT).

### Adding or changing a lesson

1. Write or edit the markdown under `content/<track>/`. Start it with a `# Title` line.
   Link to other lessons and to repository files with ordinary relative links; the viewer
   turns lesson links into its own pages and other files into GitHub links.
2. List a new lesson in `site/data/course.json` (its `id` is the file name without `.md`;
   `minutes` is the reading time).
3. Run `python3 learning-observability/tools/check.py`.

## Deployment

GitHub Pages, from [`.github/workflows/pages.yml`](../.github/workflows/pages.yml). Every
push to `main` that touches `learning-observability/` (and a manual run from the Actions
tab) builds the site with `tools/build.sh` and deploys `_site/` with
`actions/upload-pages-artifact` and `actions/deploy-pages`. The repository's Pages source
must be set to **GitHub Actions** (Settings → Pages) once. The site is then at
https://abutlabs.github.io/observability/.

## License

MIT, like the rest of the repository ([LICENSE](../LICENSE)).
