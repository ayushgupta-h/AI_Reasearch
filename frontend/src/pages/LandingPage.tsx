/**
 * Research AI Quest — Landing Page (React + TypeScript)
 * Built with the dark parchment & bronze scholar theme from sample-ui.html.
 */

import React, { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../providers/Auth';
import { useToast } from '../providers/ui';

const STAGE_INFO: Record<string, { k: string; t: string; b: string }> = {
  source: {
    k: 'Stage 01 — Source',
    t: 'Where knowledge begins',
    b: 'Every source is captured with its origin, publication context and retrieval path — so no finding ever floats without provenance.'
  },
  claim: {
    k: 'Stage 02 — Claim',
    t: 'What a source asserts',
    b: 'Statements are extracted and normalised so they can be tested against evidence, rather than taken on trust.'
  },
  evidence: {
    k: 'Stage 03 — Evidence',
    t: 'What supports — or contradicts',
    b: 'Passages, figures and datasets are linked to each claim with exact quotations and locations — including dissenting material.'
  },
  analysis: {
    k: 'Stage 04 — Analysis',
    t: 'How it all connects',
    b: 'The platform maps agreements, contradictions and gaps across evidence to form findings with explicit confidence levels.'
  },
  report: {
    k: 'Stage 05 — Report',
    t: 'What you can act on',
    b: 'Findings become a structured, fully cited report — every statement one click away from its source.'
  }
};

const CONSTELLATION_NODES = [
  { id: 's1', x: 235, y: 225, o: 0.8, t: 'paper', label: 'Census of Antarctic marine life', stage: 'source' },
  { id: 's2', x: 430, y: 118, o: 0.5, t: 'photo', label: 'Expedition photographs, 1912', stage: 'source' },
  { id: 's3', x: 1185, y: 145, o: 0.6, t: 'map', label: 'Ocean current charts', stage: 'source' },
  { id: 's4', x: 1405, y: 305, o: 0.45, t: 'note', label: 'Field notebook excerpts', stage: 'source' },
  { id: 's5', x: 172, y: 520, o: 0.5, t: 'chart', label: 'Satellite temperature series', stage: 'source' },
  { id: 's6', x: 1348, y: 648, o: 0.7, t: 'paper', label: 'Peer-reviewed study, 2021', stage: 'source' },
  { id: 'c1', x: 520, y: 365, o: 0.95, t: 'note', label: '“Species ranges shifted poleward”', stage: 'claim' },
  { id: 'c2', x: 1078, y: 330, o: 0.9, t: 'note', label: '“Currents strengthened ~12%”', stage: 'claim' },
  { id: 'e1', x: 610, y: 628, o: 0.9, t: 'paper', label: 'Supporting dataset, Table 3', stage: 'evidence' },
  { id: 'e2', x: 988, y: 565, o: 0.75, t: 'photo', label: 'Contradictory survey, 1987', stage: 'evidence' },
  { id: 'e3', x: 792, y: 732, o: 0.7, t: 'chart', label: 'Meta-analysis of 14 studies', stage: 'evidence' },
  { id: 'core', x: 800, y: 430, o: 1, t: 'core', label: 'Analysis', stage: 'analysis' },
  { id: 'rep', x: 800, y: 130, o: 0.95, t: 'report', label: 'Final report', stage: 'report' }
];

const CONSTELLATION_EDGES = [
  ['s1', 'c1'], ['s2', 'c1'], ['s4', 'c1'], ['s3', 'c2'], ['s5', 'c2'], ['s6', 'c2'],
  ['c1', 'e1'], ['c1', 'e3'], ['c2', 'e2'], ['c2', 'e3'],
  ['e1', 'core'], ['e2', 'core'], ['e3', 'core'], ['c1', 'core'], ['c2', 'core'], ['core', 'rep']
];

const AGENTS_DATA = [
  { id: 'supervisor', name: 'Supervisor', role: 'Orchestrator', d: 'Coordinates the investigation and delegates to specialists.' },
  { id: 'planner', name: 'Planner', role: 'Strategy', d: 'Breaks the question into structured research objectives.' },
  { id: 'search', name: 'Search', role: 'Discovery', d: 'Retrieves candidates across the open web and internal archives.' },
  { id: 'extraction', name: 'Extraction', role: 'Comprehension', d: 'Pulls claims, figures and passages from raw sources.' },
  { id: 'verification', name: 'Verification', role: 'Integrity', d: 'Tests claims against supporting and contradicting evidence.' },
  { id: 'analysis', name: 'Analysis', role: 'Insight', d: 'Finds relationships, trends and gaps across findings.' },
  { id: 'writer', name: 'Writer', role: 'Synthesis', d: 'Composes structured, cited research documents.' },
  { id: 'critic', name: 'Critic', role: 'Quality', d: 'Challenges weak reasoning and flags unsupported statements.' },
  { id: 'memory', name: 'Memory', role: 'Continuity', d: 'Persists knowledge for future investigations.' }
];

export function LandingPage() {
  const navigate = useNavigate();
  const { user, signInWithGoogle, signOut } = useAuth();
  const toast = useToast();

  const [scrolled, setScrolled] = useState(false);
  const [progress, setProgress] = useState(0);
  const [showToTop, setShowToTop] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [activeSection, setActiveSection] = useState('hero');
  const [activeTab, setActiveTab] = useState<'overview' | 'execution' | 'evidence' | 'report'>('overview');
  const [hoveredAgent, setHoveredAgent] = useState<string | null>(null);
  const [activeNode, setActiveNode] = useState<(typeof CONSTELLATION_NODES)[0] | null>(null);
  const [selectedNode, setSelectedNode] = useState<(typeof CONSTELLATION_NODES)[0] | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [tipPos, setTipPos] = useState({ left: 0, top: 0, show: false, label: '', stage: '' });
  const [popData, setPopData] = useState({ show: false, title: '', desc: '', left: 0, top: 0 });

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const heroRef = useRef<HTMLElement>(null);
  const archiveStageRef = useRef<HTMLDivElement>(null);
  const lensRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<HTMLDivElement>(null);
  const artRef = useRef<SVGSVGElement>(null);

  // Scroll listener
  useEffect(() => {
    const handleScroll = () => {
      const y = window.scrollY;
      const d = document.documentElement;
      setScrolled(y > 40);
      setShowToTop(y > 900);
      const maxScroll = Math.max(1, d.scrollHeight - window.innerHeight);
      setProgress((y / maxScroll) * 100);
    };
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  // Section observer
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) setActiveSection(entry.target.id);
        });
      },
      { rootMargin: '-42% 0px -52% 0px' }
    );
    ['hero', 'archive', 'constellation', 'tree', 'agents', 'process', 'preview', 'begin'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  // Dust canvas animation
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext('2d');
    if (!ctx) return;
    const mobile = window.innerWidth <= 820;
    const N = mobile ? 24 : 50;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let W = c.clientWidth, H = c.clientHeight;

    const rs = () => {
      W = c.clientWidth;
      H = c.clientHeight;
      c.width = W * dpr;
      c.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    rs();
    window.addEventListener('resize', rs);

    const P = Array.from({ length: N }, () => ({
      x: Math.random(),
      y: Math.random(),
      r: 0.6 + Math.random() * 1.5,
      s: 0.05 + Math.random() * 0.2,
      p: Math.random() * 6.28,
      a: 0.1 + Math.random() * 0.3,
    }));

    let animId: number;
    const frame = () => {
      animId = requestAnimationFrame(frame);
      ctx.clearRect(0, 0, W, H);
      for (const p of P) {
        p.p += 0.005;
        p.y -= p.s / H;
        if (p.y < -0.02) {
          p.y = 1.02;
          p.x = Math.random();
        }
        ctx.beginPath();
        ctx.arc((p.x + Math.sin(p.p) * 0.012) * W, p.y * H, p.r, 0, 6.28);
        ctx.fillStyle = `rgba(214,166,93,${p.a})`;
        ctx.fill();
      }
    };
    frame();

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', rs);
    };
  }, []);

  // Magnifying lens on archive
  useEffect(() => {
    const stage = archiveStageRef.current;
    const lens = lensRef.current;
    const view = viewRef.current;
    const art = artRef.current;
    if (!stage || !lens || !view || !art || window.innerWidth <= 820) return;

    view.innerHTML = '';
    const clone = art.cloneNode(true) as SVGSVGElement;
    clone.removeAttribute('id');
    view.appendChild(clone);

    const R = 70;
    const Z = 1.9;
    let cx = 0, cy = 0, sx = 0, sy = 0, on = false;

    const onMove = (e: PointerEvent) => {
      const r = stage.getBoundingClientRect();
      sx = e.clientX - r.left;
      sy = e.clientY - r.top;
      if (!on) {
        on = true;
        cx = sx;
        cy = sy;
        lens.classList.add('on');
      }
    };
    const onLeave = () => {
      on = false;
      lens.classList.remove('on');
    };

    stage.addEventListener('pointermove', onMove);
    stage.addEventListener('pointerleave', onLeave);

    let animId: number;
    const loop = () => {
      animId = requestAnimationFrame(loop);
      if (!on) return;
      cx += (sx - cx) * 0.16;
      cy += (sy - cy) * 0.16;
      lens.style.transform = `translate(${cx - R}px,${cy - R}px)`;
      view.style.transform = `translate(${R - cx * Z}px,${R - cy * Z}px) scale(${Z})`;
    };
    loop();

    return () => {
      cancelAnimationFrame(animId);
      stage.removeEventListener('pointermove', onMove);
      stage.removeEventListener('pointerleave', onLeave);
    };
  }, []);

  const nodeMap = useMemo(() => {
    const map: Record<string, (typeof CONSTELLATION_NODES)[0]> = {};
    CONSTELLATION_NODES.forEach((n) => { map[n.id] = n; });
    return map;
  }, []);

  return (
    <div className="landing-root bg-[#080806] text-[#F4EBDD] font-sans antialiased min-h-screen">
      <div className="grain" aria-hidden="true"></div>
      <div className="progress" style={{ width: `${progress}%` }} aria-hidden="true"></div>

      {/* Navigation */}
      <header className={`nav ${scrolled ? 'scrolled' : ''}`} id="nav">
        <a className="brand" href="#top">
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M12 3.5 5 19M12 3.5 19 19M5 19h14" stroke="#C5A36A" strokeWidth="1.3" />
            <circle cx="12" cy="3.5" r="2.1" fill="#D6A65D" />
            <circle cx="5" cy="19" r="2.1" fill="#C5A36A" />
            <circle cx="19" cy="19" r="2.1" fill="#C5A36A" />
          </svg>
          <span>Quest</span>
        </a>

        <nav className="nav-links" aria-label="Primary">
          <a href="#archive">Discover</a>
          <a href="#constellation">Intelligence</a>
          <a href="#process">How it works</a>
          <a href="#preview">Platform</a>
          <a href="#begin">Research</a>
        </nav>

        <div className="nav-actions">
          {user ? (
            <div className="relative group flex items-center">
              <button
                onClick={() => navigate('/app/new')}
                title={`${user.email || 'User'} — Open Workspace`}
                className="grid h-8 w-8 place-items-center rounded-full bg-[#1B1510] border border-[#C5A36A]/50 font-serif text-xs font-bold text-[#E9D6B6] hover:border-[#C5A36A] hover:bg-[#302319] transition-all cursor-pointer"
                aria-label="Account menu"
              >
                {(user.email || 'U')[0].toUpperCase()}
              </button>
              <div className="absolute right-0 top-full mt-2 hidden group-hover:flex flex-col rounded-xl border border-[#453322] bg-[#10100E] p-2 text-xs shadow-2xl min-w-[150px] z-50">
                <p className="truncate px-2 py-1 text-[11px] font-mono text-[#817463] border-b border-[#302319] mb-1">{user.email}</p>
                <button onClick={() => navigate('/app/new')} className="rounded px-2 py-1.5 text-left text-[#F4EBDD] hover:bg-[#1B1510] hover:text-[#C5A36A] transition-colors">Workspace</button>
                <button onClick={() => navigate('/app/history')} className="rounded px-2 py-1.5 text-left text-[#F4EBDD] hover:bg-[#1B1510] hover:text-[#C5A36A] transition-colors">History</button>
                <button onClick={() => signOut()} className="rounded px-2 py-1.5 text-left text-red-400 hover:bg-[#1B1510] hover:text-red-300 transition-colors">Sign out</button>
              </div>
            </div>
          ) : (
            <button onClick={() => signInWithGoogle()} className="signin">
              Sign in with Google
            </button>
          )}

          <button onClick={() => navigate('/app/new')} className="btn primary small">
            {user ? 'New Research' : 'Begin Research'}
          </button>

          <button
            className={`burger ${menuOpen ? 'open' : ''}`}
            onClick={() => setMenuOpen(!menuOpen)}
            aria-label="Toggle menu"
          >
            <span></span>
            <span></span>
          </button>
        </div>
      </header>

      {/* Mobile Drawer */}
      <div className={`mobile-menu ${menuOpen ? 'open' : ''}`}>
        <a href="#archive" onClick={() => setMenuOpen(false)}>Discover</a>
        <a href="#constellation" onClick={() => setMenuOpen(false)}>Intelligence</a>
        <a href="#process" onClick={() => setMenuOpen(false)}>How it works</a>
        <a href="#preview" onClick={() => setMenuOpen(false)}>Platform</a>
        <button
          onClick={() => { setMenuOpen(false); navigate(user ? '/app/new' : '/auth/login'); }}
          className="btn primary w-full"
        >
          {user ? 'Open Workspace' : 'Get Started'}
        </button>
      </div>

      {/* Side Rail */}
      <aside className="rail">
        {['hero', 'archive', 'constellation', 'tree', 'agents', 'process', 'preview', 'begin'].map((id) => (
          <a key={id} href={`#${id}`} className={activeSection === id ? 'active' : ''}>
            <i></i>
          </a>
        ))}
      </aside>

      {/* Main Content */}
      <main>
        {/* HERO */}
        <header id="hero" ref={heroRef} aria-label="Introduction">
          <div className="hero-scene" aria-hidden="true">
            <div className="layer l-back">
              <div className="lin">
                <div className="shelf l"></div>
                <div className="shelf r"></div>
              </div>
            </div>
            <div className="layer l-wall">
              <div className="lin">
                <div className="doc d-note" style={{ left: '10%', top: '15%', transform: 'rotate(-4deg)' }}></div>
                <div className="doc d-photo" style={{ left: '15%', top: '31%', transform: 'rotate(3deg)' }}></div>
                <div className="doc d-map" style={{ left: '7%', top: '47%' }}></div>
                <div className="doc d-note" style={{ left: '84%', top: '16%', transform: 'rotate(5deg)' }}></div>
                <div className="doc d-photo" style={{ left: '79%', top: '33%', transform: 'rotate(-3deg)' }}></div>
                <div className="doc d-note" style={{ left: '89%', top: '47%', transform: 'rotate(-6deg)' }}></div>
              </div>
            </div>
            <div className="glow"></div>
            <div className="layer l-desk">
              <div className="lin">
                <div className="desk">
                  <div className="desk-top"></div>
                  <div className="globe"></div>
                  <div className="stack"><i></i><i></i><i></i></div>
                  <div className="pp a"></div>
                  <div className="pp b"></div>
                  <div className="lamp"><i className="base"></i><i className="stem"></i><i className="shade"></i></div>
                </div>
              </div>
            </div>
            <canvas id="dust" ref={canvasRef}></canvas>
            <div className="layer l-fig">
              <div className="lin">
                <div className="fig-wrap">
                  <svg viewBox="0 0 420 460" aria-hidden="true">
                    <path
                      d="M96 460 C100 330 128 224 176 200 L180 188 C160 176 148 152 148 126 C148 92 176 66 210 66 C244 66 272 92 272 126 C272 152 260 176 240 188 L244 200 C292 224 320 330 324 460 Z"
                      fill="#070503"
                    />
                    <path
                      d="M240 188 L244 200 C292 224 320 330 324 460 L302 460 C298 342 274 236 232 206 Z"
                      fill="rgba(214,166,93,.06)"
                    />
                  </svg>
                </div>
              </div>
            </div>
            <div className="vig"></div>
          </div>

          <div className="hero-content">
            <p className="hero-eyebrow">An intelligent space for deep research</p>
            <h1 className="hero-title">
              <span className="w">Every</span> <span className="w">great</span> <span className="w"><em>discovery</em></span>{' '}
              <span className="w">begins</span> <span className="w">with</span> <span className="w">a</span>{' '}
              <span className="w"><em>question.</em></span>
            </h1>
            <p className="hero-sub">
              An autonomous AI research workspace that investigates complex questions, connects knowledge across sources
              and transforms information into evidence-backed intelligence.
            </p>
            <div className="hero-cta">
              <button onClick={() => navigate('/app/new')} className="btn primary">
                Begin Your Research <span className="arr">→</span>
              </button>
              <a className="btn ghost" href="#archive">
                Explore the Experience
              </a>
            </div>
          </div>

          <div className="scroll-cue" aria-hidden="true">
            <span>Scroll to explore</span>
            <i></i>
          </div>
        </header>

        {/* ARCHIVE */}
        <section id="archive" className="chapter">
          <div className="archive-bg"></div>
          <div className="warm-pool"></div>
          <div className="wrap">
            <div className="chapter-head center reveal">
              <p className="eyebrow">Enter the archive</p>
              <h2 className="sec-title">Knowledge has <em>no boundaries.</em></h2>
              <p className="sec-desc">Every discovery begins with information. But understanding requires more than simply collecting it.</p>
            </div>
          </div>
          <div className="archive-stage" ref={archiveStageRef}>
            <svg id="archiveArt" ref={artRef} viewBox="0 0 1200 640" role="img" aria-label="Antique research manuscript">
              <rect width="1200" height="640" fill="#0d0a07" />
              <rect y="380" width="1200" height="260" fill="#20130a" />
              <g transform="translate(600 280)">
                <path d="M-240 -110 C-160 -130 -60 -126 0 -110 L0 120 C-60 104 -160 100 -240 116 Z" fill="#efe0c0" />
                <path d="M0 -110 C80 -130 180 -126 240 -110 L240 120 C180 104 80 100 0 116 Z" fill="#e6d4ae" />
                <text x="-180" y="-40" fontFamily="Cormorant Garamond,serif" fontSize="42" fill="#6e3a1f">Quaestio</text>
                <text x="-180" y="0" fontFamily="Inter,sans-serif" fontSize="11" fill="rgba(90,70,48,0.7)">On the Autonomous Architecture of Multi-Agent Research</text>
                <circle cx="120" cy="-20" r="30" fill="none" stroke="#8a6a3c" strokeWidth="1.5" />
                <path d="M120 -40 L120 0 M100 -20 L140 -20" stroke="#8a6a3c" strokeWidth="1.2" />
              </g>
            </svg>
            <div className="lens" ref={lensRef} aria-hidden="true">
              <div className="lens-view" ref={viewRef}></div>
            </div>
          </div>
          <p className="microcopy">Millions of documents · Thousands of perspectives · One connected research process</p>
        </section>

        {/* CONSTELLATION */}
        <section id="constellation" className="chapter">
          <div className="wrap">
            <div className="chapter-head center reveal">
              <p className="eyebrow">Beyond information</p>
              <h2 className="sec-title">Find the connections <em>others miss.</em></h2>
              <p className="sec-desc">
                Research is about understanding how individual pieces of evidence connect. Hover a document to trace its path.
              </p>
            </div>
          </div>
          <div className="constellation-stage" id="netStage">
            <svg id="netSvg" viewBox="0 0 1600 900" className={activeNode ? 'dim' : ''}>
              {CONSTELLATION_EDGES.map(([a, b], idx) => {
                const A = nodeMap[a];
                const B = nodeMap[b];
                if (!A || !B) return null;
                const isLit = activeNode && (activeNode.id === a || activeNode.id === b);
                return (
                  <path
                    key={idx}
                    className={`edge ${isLit ? 'lit' : ''}`}
                    d={`M${A.x} ${A.y} Q${(A.x + B.x) / 2} ${(A.y + B.y) / 2 - 30} ${B.x} ${B.y}`}
                  />
                );
              })}
              {CONSTELLATION_NODES.map((n) => (
                <g key={n.id} transform={`translate(${n.x} ${n.y})`}>
                  <circle
                    r={n.t === 'core' ? 24 : 16}
                    fill={n.t === 'core' ? '#D6A65D' : '#302319'}
                    stroke="#C5A36A"
                    strokeWidth="1.5"
                    className="cursor-pointer transition-all hover:scale-125"
                    onMouseEnter={(e) => {
                      setActiveNode(n);
                      const r = e.currentTarget.getBoundingClientRect();
                      setTipPos({ left: r.left + 20, top: r.top - 20, show: true, label: n.label, stage: STAGE_INFO[n.stage]?.k || '' });
                    }}
                    onMouseLeave={() => { setActiveNode(null); setTipPos((p) => ({ ...p, show: false })); }}
                    onClick={() => { setSelectedNode(n); setPanelOpen(true); }}
                  />
                  <text y={32} textAnchor="middle" fill="#C0B19C" fontSize="11" fontFamily="Geist Mono,monospace">
                    {n.label.slice(0, 16)}…
                  </text>
                </g>
              ))}
            </svg>
            <div className={`net-tip ${tipPos.show ? 'show' : ''}`} style={{ left: tipPos.left, top: tipPos.top }}>
              <strong>{tipPos.label}</strong>
              <span>{tipPos.stage}</span>
            </div>
            <aside className={`net-panel ${panelOpen ? 'open' : ''}`}>
              <button className="np-x" onClick={() => setPanelOpen(false)}>✕</button>
              <p className="np-k">{selectedNode ? STAGE_INFO[selectedNode.stage]?.k : ''}</p>
              <h3 className="np-t">{selectedNode ? STAGE_INFO[selectedNode.stage]?.t : ''}</h3>
              <p className="np-b">{selectedNode ? STAGE_INFO[selectedNode.stage]?.b : ''}</p>
            </aside>
          </div>
        </section>

        {/* AGENTS */}
        <section id="agents" className="chapter">
          <div className="wrap">
            <div className="chapter-head reveal">
              <p className="eyebrow">The intelligence</p>
              <h2 className="sec-title">Behind every discovery, a <em>system</em> of intelligence.</h2>
              <p className="sec-desc">Specialized AI agents working collaboratively to plan, search, verify and synthesize.</p>
            </div>
            <div className="agents-grid">
              <div className="agents-stage in">
                <svg id="agentsSvg" viewBox="0 0 900 640">
                  <circle cx="450" cy="300" r="48" fill="#302319" stroke="#D6A65D" strokeWidth="2" />
                  <text x="450" y="305" textAnchor="middle" fill="#F3E9D7" fontSize="12" fontWeight="bold">SUPERVISOR</text>
                  {AGENTS_DATA.slice(1).map((a, i) => {
                    const rad = ((i * 45 - 90) * Math.PI) / 180;
                    const x = 450 + 200 * Math.cos(rad);
                    const y = 300 + 200 * Math.sin(rad);
                    const isActive = hoveredAgent === a.id;
                    return (
                      <g key={a.id} onMouseEnter={() => setHoveredAgent(a.id)} onMouseLeave={() => setHoveredAgent(null)}>
                        <line x1="450" y1="300" x2={x} y2={y} stroke="#C5A36A" strokeOpacity={isActive ? 1 : 0.25} strokeWidth={isActive ? 2 : 1} />
                        <circle cx={x} cy={y} r="28" fill={isActive ? '#5B4230' : '#1B1510'} stroke="#C5A36A" strokeWidth="1.5" className="cursor-pointer" />
                        <text x={x} y={y + 4} textAnchor="middle" fill="#F4EBDD" fontSize="10">{a.name}</text>
                      </g>
                    );
                  })}
                </svg>
              </div>
              <ul className="agent-list">
                {AGENTS_DATA.map((a) => (
                  <li key={a.id} className={hoveredAgent === a.id ? 'active' : ''} onMouseEnter={() => setHoveredAgent(a.id)} onMouseLeave={() => setHoveredAgent(null)}>
                    <i className="st"></i>
                    <div>
                      <strong>{a.name} Agent</strong>
                      <span className="role">{a.role}</span>
                      <p>{a.d}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        {/* PLATFORM PREVIEW */}
        <section id="preview" className="chapter">
          <div className="wrap">
            <div className="chapter-head reveal">
              <p className="eyebrow">The platform</p>
              <h2 className="sec-title">An entire research operation. <em>One intelligent workspace.</em></h2>
            </div>
            <div className="ptabs">
              {(['overview', 'execution', 'evidence', 'report'] as const).map((t) => (
                <button key={t} className={`ptab ${activeTab === t ? 'active' : ''}`} onClick={() => setActiveTab(t)}>
                  {t[0].toUpperCase() + t.slice(1)}
                </button>
              ))}
            </div>
            <div className="window reveal">
              <div className="win-bar">
                <span className="win-dot"></span><span className="win-dot"></span><span className="win-dot"></span>
                <span className="win-title">quest / workspace preview</span>
              </div>
              <div className="win-body">
                <div className="win-side">
                  <button className="side-item active" onClick={() => setActiveTab('overview')}>Overview</button>
                  <button className="side-item" onClick={() => navigate('/app/history')}>Sessions</button>
                  <button className="side-item" onClick={() => navigate('/app/documents')}>Documents</button>
                  <button className="side-item" onClick={() => navigate('/app/projects')}>Projects</button>
                  <button className="side-new" onClick={() => navigate('/app/new')}>+ New Research</button>
                </div>
                <div className="win-main p-6">
                  {activeTab === 'overview' && (
                    <div>
                      <h4 className="text-lg font-semibold mb-2">The Economics of Remote Work — Analysis</h4>
                      <div className="stats my-4">
                        <div className="stat"><b>214</b><span>Sources</span></div>
                        <div className="stat"><b>96</b><span>Claims</span></div>
                        <div className="stat"><b>71</b><span>Verified</span></div>
                        <div className="stat"><b>0.87</b><span>Confidence</span></div>
                      </div>
                      <div className="arow"><span className="adot ok"></span>Planner Agent — research structure completed<em>done</em></div>
                      <div className="arow"><span className="adot ok"></span>Search Agent — academic sources retrieved<em>done</em></div>
                      <div className="arow"><span className="adot run"></span>Verification Agent — validating claims 82/96<em>running</em></div>
                    </div>
                  )}
                  {activeTab === 'execution' && (
                    <div className="space-y-2 text-sm font-mono">
                      <div className="text-emerald-400">[14:32:07] Verification agent verified claim #41</div>
                      <div className="text-amber-400">[14:31:52] Extraction agent pulled 3 claims from Nature</div>
                      <div className="text-zinc-400">[14:31:20] Search agent retrieved 6 new academic sources</div>
                    </div>
                  )}
                  {activeTab === 'evidence' && (
                    <div className="space-y-2 text-sm">
                      <div className="p-3 bg-[#1B1510] border border-[#453322] rounded flex justify-between">
                        <span>Ctrip field experiment productivity gain +13%</span>
                        <span className="text-emerald-400 font-mono text-xs">SUPPORTED [1]</span>
                      </div>
                      <div className="p-3 bg-[#1B1510] border border-[#453322] rounded flex justify-between">
                        <span>High-collaboration role friction -4%</span>
                        <span className="text-amber-400 font-mono text-xs">CONTRADICTION [2]</span>
                      </div>
                    </div>
                  )}
                  {activeTab === 'report' && (
                    <div className="space-y-3">
                      <h5 className="font-serif text-lg font-bold text-[#E9D6B6]">Executive Synthesis Report</h5>
                      <p className="text-sm leading-relaxed text-[#C0B19C]">
                        Across 214 peer-reviewed sources and empirical surveys, the evidence demonstrates a net positive
                        productivity signal for individually owned knowledge work tasks, with key contradiction points
                        isolated in high-collaboration workflows.
                      </p>
                      <button onClick={() => navigate('/app/new')} className="btn primary small">
                        Launch in Full Editor →
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* FINAL CTA */}
        <section id="begin" className="py-24 text-center">
          <div className="wrap max-w-2xl mx-auto">
            <p className="eyebrow justify-center mb-3">The invitation</p>
            <h2 className="sec-title mb-4">Your next great <em>discovery</em> starts here.</h2>
            <p className="sec-desc mb-8">Bring your most complex questions. Let intelligent agents investigate the answers.</p>
            <button onClick={() => navigate('/app/new')} className="btn primary mx-auto">
              Start Your Research <span className="arr">→</span>
            </button>
          </div>
        </section>
      </main>

      {/* FOOTER */}
      <footer className="foot border-t border-[#453322] py-12 text-sm text-[#817463]">
        <div className="wrap flex flex-col md:flex-row justify-between items-center gap-6">
          <div className="flex items-center gap-2">
            <span className="font-serif text-[#C5A36A] font-bold">Quest</span>
            <span>— autonomous evidence-backed discovery.</span>
          </div>
          <div className="flex gap-4">
            <button onClick={() => navigate('/app/history')} className="hover:text-[#F4EBDD]">History</button>
            <button onClick={() => navigate('/app/projects')} className="hover:text-[#F4EBDD]">Projects</button>
            <button onClick={() => navigate('/app/documents')} className="hover:text-[#F4EBDD]">Documents</button>
            <button onClick={() => navigate('/app/system')} className="hover:text-[#F4EBDD]">System</button>
          </div>
        </div>
      </footer>
    </div>
  );
}

