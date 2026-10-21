# -*- coding: utf-8 -*-
"""Inject the shared Quest lens-scroll (damped wheel -> lerped scrollTop, library-free
Lenis-style) into every page (idempotent). Mirrors index.html's own engine: adaptive
k (8 long flicks / 6.2 short glides), deltaMode normalization, external-scroll resync,
keyboard on the same damped target, touch + html.rm bypass, nested-scroll passthrough."""
import io, os, sys

LENS_SCRIPT = """
<script>
/* ============ shared Quest lens-scroll: damped wheel, expo-lerped glide ============ */
(function(){
  'use strict';
  if (window.__lens) return;
  var rm = document.documentElement.classList.contains('rm');
  var REDUCED = rm && new URLSearchParams(location.search).get('motion') !== 'full';
  if (REDUCED) return;                       /* reduced motion: native scrolling only */
  if (window.matchMedia && matchMedia('(pointer:coarse)').matches) return; /* touch: native momentum */

  function clamp(v, a, b){ return v < a ? a : v > b ? b : v; }
  function maxScroll(el){ return Math.max(0, el.scrollHeight - el.clientHeight); }

  /* a nested scrollable between the wheel target and our container that can
     absorb the wheel keeps native behavior (sheet body, side lists, textareas) */
  function nestedScrollable(node, cont, dir){
    var n = node;
    while (n && n !== cont){
      if (n.nodeType === 1){
        var oy = getComputedStyle(n).overflowY;
        if ((oy === 'auto' || oy === 'scroll') && n.scrollHeight > n.clientHeight + 4){
          if (dir < 0 ? n.scrollTop > 1 : n.scrollTop < n.scrollHeight - n.clientHeight - 1) return true;
        }
      }
      n = n.parentElement;
    }
    return false;
  }

  function lens(el){
    if (!el || el.__lens) return;
    el.__lens = true;
    var target = el.scrollTop, current = target, lastWritten = target;
    var lastNow = 0, raf = 0;

    function frame(now){
      raf = 0;
      var dt = clamp((now - lastNow) / 1000, 0.0001, 0.05);
      lastNow = now;
      var ms = maxScroll(el);
      if (target > ms) target = ms;
      var gap = Math.abs(target - current);
      var k = gap > (el.clientHeight || 400) * 0.25 ? 8 : 6.2;   /* adaptive response */
      current += (target - current) * (1 - Math.exp(-dt * k));   /* frame-rate independent */
      if (Math.abs(target - current) < 0.35) current = target;
      if (Math.abs(current - lastWritten) > 0.05){
        el.scrollTop = current;                                  /* instant per spec */
        lastWritten = current;
      }
      if (current !== target) raf = requestAnimationFrame(frame);
    }
    function kick(){ if (!raf){ lastNow = performance.now(); raf = requestAnimationFrame(frame); } }

    el.addEventListener('wheel', function(e){
      if (e.ctrlKey || e.deltaY === 0) return;                    /* pinch-zoom / horizontal: native */
      if (el.scrollHeight <= el.clientHeight + 4) return;
      if (nestedScrollable(e.target, el, e.deltaY)) return;
      e.preventDefault();
      var d = e.deltaY;
      if (e.deltaMode === 1) d *= 16;
      else if (e.deltaMode === 2) d *= el.clientHeight || 400;
      target = clamp(target + d, 0, maxScroll(el));
      kick();
    }, { passive: false });

    /* keyboard feeds the same damped target — nothing snaps */
    el.addEventListener('keydown', function(e){
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      var t = e.target;
      if (t && t !== el && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      var d = 0;
      if (e.key === 'ArrowDown') d = 90;
      else if (e.key === 'ArrowUp') d = -90;
      else if (e.key === 'PageDown' || e.key === ' ') d = el.clientHeight * 0.85;
      else if (e.key === 'PageUp') d = -el.clientHeight * 0.85;
      else if (e.key === 'Home'){ target = 0; e.preventDefault(); kick(); return; }
      else if (e.key === 'End'){ target = maxScroll(el); e.preventDefault(); kick(); return; }
      if (!d) return;
      e.preventDefault();
      target = clamp(target + d, 0, maxScroll(el));
      kick();
    });

    /* scrollbar drags, autoscroll, find-in-page, anchor restore -> resync */
    el.addEventListener('scroll', function(){
      var y = el.scrollTop;
      if (Math.abs(y - lastWritten) > 1.5) current = target = lastWritten = y;
    }, { passive: true });
  }

  window.__lens = lens;
  var TARGETS = __LENS_TARGETS__;
  TARGETS.forEach(function(sel){
    var el = sel === null ? (document.scrollingElement || document.documentElement) : document.querySelector(sel);
    if (el) lens(el);
  });
})();
</script>
"""

# index.html keeps its own full engine (FX-coupled); login only scrolls as the document.
TARGETS = {
    "new.html":       "['#sideScroll', '#runview', '#thread']",
    "report.html":    "['.doc-scroll', '.src-list', '.studio-scroll']",
    "login.html":     "[null]",
    "history.html":   "['#scroll']",
    "dashboard.html": "['#scroll']",
}

END_ANCHOR = "</body>"

def inject(path, targets):
    with io.open(path, "r", encoding="utf-8") as f:
        s = f.read()
    if "__lens" in s:
        print("  already patched", path)
        return
    head, sep, tail = s.rpartition(END_ANCHOR)
    if not sep:
        print("  !! no </body> in", path); return
    s = head + LENS_SCRIPT.replace("__LENS_TARGETS__", targets) + sep + tail
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(s)
    print("  patched", path)

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ok = True
    for name, targets in TARGETS.items():
        fp = os.path.join(here, name)
        if not os.path.exists(fp):
            print("  !! missing", name); ok = False; continue
        inject(fp, targets)
    print("  skipped index.html (has its own engine)")
    sys.exit(0 if ok else 1)

main()
