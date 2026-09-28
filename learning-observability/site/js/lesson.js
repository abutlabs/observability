/* Learning Observability: the lesson page.

   Loads one lesson's markdown (content/<track>/<lesson>.md, or exercises/<lab>.md) and
   renders it with marked and highlight.js. The markdown is written to read well on
   GitHub too, so its links are GitHub-style relative paths: here a link to another
   lesson's .md becomes that lesson's page, and a link to any other file in the repository
   goes to the file on GitHub. Adapted from learning-lasair's viewer (same owner, MIT). */

const COURSE_ROOT = 'learning-observability';   // this folder's path in the repository
const FAKE_ORIGIN = 'https://repository.invalid';

function lessonPathFromUrl() {
    return new URLSearchParams(window.location.search).get('lesson') || '00-welcome/01-why-observe';
}

function githubUrl(repoPath, isDir) {
    const d = Course.data;
    const repo = d.repo || 'https://github.com/abutlabs/observability';
    return repo + (isDir ? '/tree/' : '/blob/') + (d.branch || 'main') + '/' + repoPath;
}

/** Where a markdown link should go when the markdown is shown on this site. */
function resolveLink(href, file) {
    if (!href || href.startsWith('#') || /^[a-z][a-z0-9+.-]*:/i.test(href)) return { href, external: /^https?:/i.test(href) };
    const url = new URL(href, FAKE_ORIGIN + '/' + COURSE_ROOT + '/' + file);
    const repoPath = decodeURIComponent(url.pathname.replace(/^\//, ''));
    const prefix = COURSE_ROOT + '/';
    if (repoPath.startsWith(prefix)) {
        const lesson = Course.byFile(repoPath.slice(prefix.length));
        if (lesson) return { href: Course.lessonUrl(lesson.path) + url.hash, external: false };
    }
    return { href: githubUrl(repoPath, repoPath.endsWith('/') || repoPath === '') + url.hash, external: true };
}

function slugify(text, seen) {
    const base = text.toLowerCase().trim()
        .replace(/<[^>]+>/g, '')
        .replace(/&[a-z]+;|&#\d+;/g, '')
        .replace(/[^\p{L}\p{N}\s_-]/gu, '')
        .replace(/\s/g, '-');
    const n = seen.get(base) || 0;
    seen.set(base, n + 1);
    return n ? base + '-' + n : base;
}

function configureMarked(file) {
    const seen = new Map();
    const renderer = {
        code(code, infostring) {
            const lang = (infostring || '').trim().split(/\s+/)[0];
            const known = lang && window.hljs && hljs.getLanguage(lang);
            const body = known ? hljs.highlight(code, { language: lang, ignoreIllegals: true }).value : Course.escapeHtml(code);
            return '<div class="code-block"><div class="code-header"><span>' + Course.escapeHtml(lang || 'text')
                + '</span><button class="copy-button" type="button">Copy</button></div>'
                + '<pre><code class="hljs">' + body + '</code></pre></div>';
        },
        heading(text, level) {
            const id = slugify(text, seen);
            return '<h' + level + ' id="' + id + '">' + text + '</h' + level + '>';
        },
        link(href, title, text) {
            const r = resolveLink(href, file);
            return '<a href="' + Course.escapeHtml(r.href) + '"' + (title ? ' title="' + Course.escapeHtml(title) + '"' : '')
                + (r.external ? ' target="_blank" rel="noopener"' : '') + '>' + text + '</a>';
        },
        table(header, body) {
            return '<div class="table-wrap"><table><thead>' + header + '</thead><tbody>' + body + '</tbody></table></div>';
        },
    };
    marked.use({ gfm: true, breaks: false, renderer });
}

function renderSidebar(current) {
    const el = document.getElementById('sidebar');
    el.innerHTML = Course.data.tracks.map((track) => {
        const items = track.lessons.map((l) => {
            const path = track.id + '/' + l.id;
            const cls = [path === current ? 'active' : '', Course.isDone(path) ? 'done' : ''].filter(Boolean).join(' ');
            return '<li><a href="' + Course.lessonUrl(path) + '"' + (cls ? ' class="' + cls + '"' : '')
                + (path === current ? ' aria-current="page"' : '') + '>' + Course.escapeHtml(l.title) + '</a></li>';
        }).join('');
        const label = (track.number ? track.number + ' · ' : '') + track.title;
        return '<div class="sidebar-track"><div class="sidebar-track-title">' + Course.escapeHtml(label)
            + '</div><ul>' + items + '</ul></div>';
    }).join('');
    const active = el.querySelector('a.active');
    if (active) active.scrollIntoView({ block: 'center' });
}

function renderNav(current) {
    const all = Course.lessons();
    const i = all.findIndex((l) => l.path === current);
    const link = (l, cls, label) => '<a class="' + cls + '" href="' + Course.lessonUrl(l.path) + '"><span class="label">'
        + label + '</span><span class="title">' + Course.escapeHtml(l.lesson.title) + '</span></a>';
    document.getElementById('lesson-nav').innerHTML =
        (i > 0 ? link(all[i - 1], 'prev', '&larr; Previous') : '')
        + (i >= 0 && i < all.length - 1 ? link(all[i + 1], 'next', 'Next &rarr;') : '');
}

function renderActions(entry) {
    const el = document.getElementById('lesson-actions');
    const paint = (btn) => {
        const on = Course.isDone(entry.path);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        btn.textContent = on ? '✓ Done' : 'Mark as done';
    };
    el.innerHTML = '<button class="done-button" type="button"></button>'
        + '<a class="source-link" target="_blank" rel="noopener" href="' + githubUrl(COURSE_ROOT + '/' + entry.file)
        + '">This page on GitHub</a>';
    const btn = el.querySelector('button');
    paint(btn);
    btn.addEventListener('click', () => {
        Course.setDone(entry.path, !Course.isDone(entry.path));
        paint(btn);
        renderSidebar(entry.path);
    });
}

async function loadLesson() {
    const path = lessonPathFromUrl();
    const entry = Course.find(path);
    const body = document.getElementById('lesson-body');
    renderSidebar(path);
    if (!entry) {
        document.getElementById('lesson-title').textContent = 'Not found';
        body.innerHTML = '<p class="notice">There is no lesson "' + Course.escapeHtml(path)
            + '". <a href="index.html">Back to the course</a>.</p>';
        return;
    }
    const label = (entry.track.number ? entry.track.number + ' · ' : '') + entry.track.title;
    document.getElementById('lesson-track').textContent = label;
    document.getElementById('lesson-title').textContent = entry.lesson.title;
    document.getElementById('lesson-meta').textContent = entry.lesson.minutes ? entry.lesson.minutes + ' min read' : '';
    document.title = entry.lesson.title + ' · Learning Observability';
    renderNav(path);
    try {
        const r = await fetch(entry.file);
        if (!r.ok) throw new Error(entry.file + ': HTTP ' + r.status);
        configureMarked(entry.file);
        body.innerHTML = marked.parse(await r.text());
        const h1 = body.querySelector('h1');
        if (h1 && h1 === body.firstElementChild) h1.remove();   // the header shows the title
        renderActions(entry);
        if (window.location.hash) {
            const target = document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
            if (target) target.scrollIntoView();
        }
    } catch (err) {
        console.error(err);
        body.innerHTML = '<p class="notice">Could not load <code>' + Course.escapeHtml(entry.file)
            + '</code>. Serve the site over HTTP (see the course README) rather than opening the file directly.</p>';
    }
}

document.addEventListener('click', (e) => {
    const copy = e.target.closest('.copy-button');
    if (copy) {
        const code = copy.closest('.code-block').querySelector('code').textContent;
        navigator.clipboard.writeText(code).then(() => {
            copy.textContent = 'Copied';
            setTimeout(() => { copy.textContent = 'Copy'; }, 1500);
        }).catch(() => {});
        return;
    }
    const toggle = e.target.closest('#contents-toggle');
    if (toggle) {
        document.body.classList.toggle('sidebar-open');
        return;
    }
    if (document.body.classList.contains('sidebar-open') && !e.target.closest('#sidebar')) {
        document.body.classList.remove('sidebar-open');
    }
});

document.addEventListener('DOMContentLoaded', () => { Course.ready.then(loadLesson); });
