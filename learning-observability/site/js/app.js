/* Learning Observability: what every page shares.

   The course manifest (data/course.json) is the single source of the tracks and lessons;
   the landing page and the lesson sidebar both render from it. Every URL here is
   relative, so the site works at any path (GitHub Pages serves it under /observability/).
   Adapted from learning-lasair's viewer (same owner, MIT). No framework, no build step. */

const Course = (() => {
    let data = { tracks: [] };

    const ready = fetch('data/course.json')
        .then((r) => {
            if (!r.ok) throw new Error('data/course.json: HTTP ' + r.status);
            return r.json();
        })
        .then((d) => { data = d; return d; })
        .catch((err) => { console.error('course manifest:', err); return data; });

    // ---- storage (per reader, never required) -------------------------------------
    function load(key, fallback) {
        try {
            const v = localStorage.getItem(key);
            return v === null ? fallback : JSON.parse(v);
        } catch (e) {
            return fallback;
        }
    }
    function save(key, value) {
        try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ }
    }

    // ---- lessons ---------------------------------------------------------------------
    /** Every lesson in course order: {path, track, lesson, file}. */
    function lessons() {
        const out = [];
        for (const track of data.tracks) {
            for (const lesson of track.lessons) {
                const dir = track.dir || ('content/' + track.id);
                out.push({ path: track.id + '/' + lesson.id, track, lesson, file: dir + '/' + lesson.id + '.md' });
            }
        }
        return out;
    }
    function find(path) { return lessons().find((l) => l.path === path) || null; }
    /** The lesson whose markdown is at `file` (relative to the course root), if any. */
    function byFile(file) { return lessons().find((l) => l.file === file) || null; }
    function lessonUrl(path) { return 'lesson.html?lesson=' + encodeURI(path); }

    // ---- progress ------------------------------------------------------------------------
    function done() { return new Set(load('lo-done', [])); }
    function isDone(path) { return done().has(path); }
    function setDone(path, on) {
        const d = done();
        if (on) d.add(path); else d.delete(path);
        save('lo-done', Array.from(d));
    }

    // ---- theme ---------------------------------------------------------------------------
    function applyTheme(theme) {
        if (theme === 'light') document.documentElement.setAttribute('data-theme', 'light');
        else document.documentElement.removeAttribute('data-theme');
    }
    function initTheme() {
        const saved = load('lo-theme', null);
        const light = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
        applyTheme(saved || (light ? 'light' : 'dark'));
        const btn = document.getElementById('theme-toggle');
        if (btn) btn.addEventListener('click', () => {
            const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
            applyTheme(next);
            save('lo-theme', next);
        });
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    return {
        ready, lessons, find, byFile, lessonUrl, isDone, setDone, initTheme, escapeHtml,
        get data() { return data; },
    };
})();

// ---- landing page: the tracks ------------------------------------------------------------
function renderTracks() {
    const el = document.getElementById('tracks');
    if (!el) return;
    el.innerHTML = Course.data.tracks.map((track) => {
        const minutes = track.lessons.reduce((n, l) => n + (l.minutes || 0), 0);
        const doneCount = track.lessons.filter((l) => Course.isDone(track.id + '/' + l.id)).length;
        const status = track.lessons.length + (track.lessons.length === 1 ? ' page' : ' pages')
            + (minutes ? ' · about ' + minutes + ' min' : '')
            + (doneCount ? ' · ' + doneCount + ' done' : '');
        const links = track.lessons.map((l) => {
            const path = track.id + '/' + l.id;
            return '<a href="' + Course.lessonUrl(path) + '"' + (Course.isDone(path) ? ' class="done"' : '') + '>'
                + Course.escapeHtml(l.title) + '</a>';
        }).join('');
        return '<div class="track">'
            + '<div class="track-head"><span class="track-number">' + Course.escapeHtml(track.number || '') + '</span>'
            + '<h3>' + Course.escapeHtml(track.title) + '</h3><span class="track-status">' + status + '</span></div>'
            + '<p>' + Course.escapeHtml(track.description || '') + '</p>'
            + '<div class="track-lessons">' + links + '</div></div>';
    }).join('');
}

document.addEventListener('DOMContentLoaded', () => {
    Course.initTheme();
    Course.ready.then(renderTracks);
});
