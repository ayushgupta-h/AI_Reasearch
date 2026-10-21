# -*- coding: utf-8 -*-
"""Inject the shared Quest page-veil transition into all six pages (idempotent)."""
import io, os, sys

VEIL_CSS = """
/* ============ page-to-page veil (shared Quest transition) ============ */
.veil{
  position:fixed;inset:0;z-index:200;pointer-events:none;
  background:#050505;
  opacity:0;visibility:hidden;
  transition:opacity .38s cubic-bezier(.22,1,.36,1),visibility 0s linear .38s;
}
.veil::after{ /* thin amber leading edge, resn-style */
  content:"";position:absolute;left:0;right:0;top:0;height:2px;
  background:linear-gradient(90deg,transparent,rgba(233,201,155,.9),transparent);
  transform:translateY(-2px);
  transition:transform .38s cubic-bezier(.22,1,.36,1);
}
.veil.on{opacity:1;visibility:visible;transition:opacity .32s cubic-bezier(.22,1,.36,1),visibility 0s}
.veil.on::after{transform:translateY(0)}
html.rm .veil{transition:none}
"""

VEIL_BODY = """
<div class="veil" id="veil" aria-hidden="true"></div>
"""

VEIL_SCRIPT = """
<script>
/* ============ shared Quest page-veil: smooth page-to-page hand-off ============ */
(function(){
  'use strict';
  var veil = document.getElementById('veil');
  if (!veil) return;
  var rm = document.documentElement.classList.contains('rm');
  var REDUCED = rm && new URLSearchParams(location.search).get('motion') !== 'full';

  window.__nav = function(url){
    if (!url || url.charAt(0) === '#') return;
    if (REDUCED) { location.href = url; return; }
    veil.classList.add('on');
    setTimeout(function(){ location.href = url; }, 330);
  };

  /* intercept in-page <a> clicks to same-origin .html targets */
  document.addEventListener('click', function(e){
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest('a[href]');
    if (!a || a.target === '_blank' || a.hasAttribute('download')) return;
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) === '#') return;
    if (!/\\.html(\\?|$|#)/.test(href) && href.indexOf('/') !== -1) return;
    if (!/\\.html/.test(href)) return;
    if (a.origin !== location.origin) return;
    if (a.pathname === location.pathname && a.search === location.search) return; /* same page: real anchor jump */
    e.preventDefault();
    window.__nav(href);
  }, false);

  /* arriving: start covered, unveil — gives every page a choreographed entrance */
  veil.classList.add('on');
  var unveil = function(){
    veil.classList.remove('on');
    if (window.__scrollOnLoad) window.__scrollOnLoad();
  };
  if (document.readyState === 'complete') setTimeout(unveil, 260);
  else window.addEventListener('load', function(){ setTimeout(unveil, 260); });
})();
</script>
"""

ANCHOR_CSS = "</style>"
ANCHOR_BODY = "<body>"
END_ANCHORS = ("</body>", "</html>")

PAGES = ["index.html", "new.html", "report.html", "login.html", "history.html", "dashboard.html"]

def inject(path):
    with io.open(path, "r", encoding="utf-8") as f:
        s = f.read()
    changed = False
    if ".veil{" not in s:
        s = s.replace(ANCHOR_CSS, VEIL_CSS + ANCHOR_CSS, 1)
        changed = True
    if 'id="veil"' not in s:
        s = s.replace(ANCHOR_BODY, ANCHOR_BODY + "\n" + VEIL_BODY, 1)
        changed = True
    if "__nav" not in s:
        head, sep, tail = s.rpartition(END_ANCHORS[0])
        if not sep:
            print("  !! no </body> in", path); return
        s = head + VEIL_SCRIPT + sep + tail
        changed = True
    if changed:
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(s)
        print("  patched", path)
    else:
        print("  already patched", path)

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ok = True
    for p in PAGES:
        fp = os.path.join(here, p)
        if not os.path.exists(fp):
            print("  !! missing", p); ok = False; continue
        inject(fp)
    sys.exit(0 if ok else 1)

main()
