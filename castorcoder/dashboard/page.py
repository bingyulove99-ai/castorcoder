"""Static HTML/CSS/JS for the CastorCoder local cockpit.

The page is intentionally dependency-free: one HTML document, a small CSS design
system, and vanilla JavaScript calling the JSON endpoints in ``server.py``.  The
interface is Chinese-first because the cockpit is a local developer tool for
this project's primary audience; identifiers, commands and raw event payloads
stay in their original form.
"""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CastorCoder Cockpit · 本地驾驶舱</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #070a12;
    --panel: rgba(17, 24, 40, .78);
    --panel-2: rgba(11, 16, 29, .66);
    --line: rgba(124, 140, 255, .14);
    --line-2: rgba(124, 140, 255, .30);
    --text: #e9edfa;
    --text-2: #b9c3dd;
    --muted: #8c97b5;
    --faint: #5f6a88;
    --accent: #7c8cff;
    --accent-2: #38e0c8;
    --llm: #3987e5;
    --tool: #199e70;
    --fail: #e66767;
    --mem: #9085e9;
    --ctx: #c98500;
    --good: #0ca30c;
    --warn: #fab219;
    --crit: #d03b3b;
    --radius: 16px;
    --mono: "JetBrains Mono", "Cascadia Code", Consolas, "Courier New", monospace;
    --sans: "Inter", "HarmonyOS Sans SC", "PingFang SC", "Microsoft YaHei",
      system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
  }
  body::before {
    content: "";
    position: fixed;
    inset: 0;
    z-index: 0;
    pointer-events: none;
    background:
      radial-gradient(900px 620px at 8% -12%, rgba(124, 140, 255, .20), transparent 62%),
      radial-gradient(760px 520px at 102% -4%, rgba(56, 224, 200, .10), transparent 58%),
      radial-gradient(800px 620px at 50% 118%, rgba(124, 140, 255, .08), transparent 60%);
  }
  .app { position: relative; z-index: 1; display: flex; min-height: 100vh; }
  .ico {
    width: 18px; height: 18px; flex: none;
    fill: none; stroke: currentColor; stroke-width: 1.8;
    stroke-linecap: round; stroke-linejoin: round;
  }
  .sprite { position: absolute; width: 0; height: 0; overflow: hidden; }

  /* ---------- sidebar ---------- */
  .sidebar {
    width: 238px; flex: 0 0 238px;
    position: sticky; top: 0; height: 100vh;
    display: flex; flex-direction: column; gap: 26px;
    padding: 22px 16px 18px;
    border-right: 1px solid var(--line);
    background: linear-gradient(180deg, rgba(14, 20, 35, .94), rgba(7, 10, 18, .88));
    backdrop-filter: blur(16px);
  }
  .brand { display: flex; align-items: center; gap: 12px; padding: 2px 6px; }
  .brand-mark {
    width: 38px; height: 38px; border-radius: 12px;
    display: grid; place-items: center;
    font-weight: 800; font-size: 1.05rem; color: #06080e;
    background: linear-gradient(135deg, #8b9bff, #38e0c8);
    box-shadow: 0 10px 26px -10px rgba(124, 140, 255, .9);
  }
  .brand-text strong { display: block; font-size: .98rem; letter-spacing: .01em; }
  .brand-text span {
    display: block; color: var(--muted); font-size: .68rem;
    letter-spacing: .16em; margin-top: 3px;
  }
  .nav { display: flex; flex-direction: column; gap: 4px; }
  .nav-item {
    display: flex; align-items: center; gap: 11px;
    padding: 10px 12px; border-radius: 10px;
    border: 1px solid transparent; background: transparent;
    color: var(--muted); font: inherit; font-size: .9rem;
    text-align: left; cursor: pointer; transition: .18s ease;
  }
  .nav-item:hover { color: var(--text); background: rgba(124, 140, 255, .08); }
  .nav-item.active {
    color: #fff;
    background: linear-gradient(90deg, rgba(124, 140, 255, .24), rgba(56, 224, 200, .07));
    border-color: var(--line-2);
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .02);
  }
  .nav-item.active .ico { color: var(--accent-2); }
  .sidebar-foot {
    margin-top: auto; display: flex; align-items: center; gap: 8px;
    padding: 10px 12px; border-radius: 10px;
    border: 1px solid var(--line); background: rgba(255, 255, 255, .02);
    color: var(--muted); font-size: .74rem;
  }
  .live-dot {
    width: 7px; height: 7px; border-radius: 50%; background: var(--good);
    box-shadow: 0 0 0 4px rgba(12, 163, 12, .12);
    animation: pulse 2.4s ease-in-out infinite;
  }
  @keyframes pulse { 50% { opacity: .45; } }
  .ver { margin-left: auto; font-family: var(--mono); color: var(--faint); }

  /* ---------- top bar ---------- */
  .content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .topbar {
    position: sticky; top: 0; z-index: 5;
    display: flex; align-items: center; justify-content: space-between;
    gap: 18px; padding: 20px 30px 16px;
    border-bottom: 1px solid rgba(255, 255, 255, .045);
    background: linear-gradient(180deg, rgba(7, 10, 18, .95), rgba(7, 10, 18, .72));
    backdrop-filter: blur(12px);
  }
  .topbar h1 { margin: 0; font-size: 1.24rem; font-weight: 700; letter-spacing: .01em; }
  .topbar p { margin: 4px 0 0; color: var(--muted); font-size: .8rem; }
  .top-actions { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .ws-chip {
    max-width: 34vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-family: var(--mono); font-size: .7rem; color: var(--text-2);
    background: rgba(255, 255, 255, .04); border: 1px solid var(--line);
    padding: 7px 11px; border-radius: 999px;
  }
  .btn {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 8px 13px; border-radius: 10px; border: 1px solid var(--line-2);
    background: rgba(124, 140, 255, .10); color: var(--text);
    font: inherit; font-size: .8rem; cursor: pointer; transition: .18s ease;
    white-space: nowrap;
  }
  .btn:hover { background: rgba(124, 140, 255, .20); transform: translateY(-1px); }
  .btn.ghost { background: rgba(255, 255, 255, .03); }
  .btn.small { padding: 5px 10px; font-size: .76rem; border-radius: 8px; }

  /* ---------- main ---------- */
  main { padding: 24px 30px 56px; }
  .tab-content { display: none; }
  .tab-content.active { display: block; animation: rise .28s ease; }
  @keyframes rise {
    from { opacity: 0; transform: translateY(7px); }
    to { opacity: 1; transform: none; }
  }
  .card {
    background: linear-gradient(180deg, var(--panel), var(--panel-2));
    border: 1px solid var(--line); border-radius: var(--radius);
    padding: 20px 22px; margin-bottom: 18px;
    box-shadow: 0 26px 60px -46px #000;
  }
  .card-head {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 14px; margin-bottom: 2px;
  }
  .card h3 { margin: 0; font-size: 1rem; font-weight: 700; letter-spacing: .01em; }
  .hint {
    color: var(--muted); font-size: .8rem; line-height: 1.65;
    margin: 6px 0 14px;
  }
  .hint code { margin: 0 2px; }

  /* ---------- stat tiles ---------- */
  .grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(188px, 1fr));
    gap: 14px; margin-bottom: 18px;
  }
  .grid.stats { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  @media (max-width: 1180px) { .grid.stats { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  @media (max-width: 640px) { .grid.stats { grid-template-columns: 1fr; } }
  .stat {
    position: relative; overflow: hidden;
    background: linear-gradient(180deg, rgba(20, 28, 48, .92), rgba(10, 15, 26, .86));
    border: 1px solid var(--line); border-radius: 14px;
    padding: 16px 16px 15px; transition: .2s ease;
  }
  .stat:hover { transform: translateY(-2px); border-color: var(--line-2); }
  .stat::after {
    content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 2px;
    background: linear-gradient(90deg, var(--tint, var(--accent)), transparent 72%);
    opacity: .85;
  }
  .stat-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .stat-label { color: var(--muted); font-size: .76rem; letter-spacing: .05em; }
  .stat-ico {
    width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center;
    color: var(--tint, var(--accent)); background: rgba(255, 255, 255, .055);
  }
  .stat-ico .ico { width: 16px; height: 16px; }
  .stat-value {
    margin-top: 11px; font-size: 1.42rem; font-weight: 700;
    letter-spacing: -.01em; font-variant-numeric: tabular-nums;
    overflow-wrap: anywhere; line-height: 1.25;
  }
  .stat-value.small {
    font-size: .84rem; font-weight: 600; font-family: var(--mono);
    line-height: 1.5; word-break: break-all;
  }
  .stat-sub { margin-top: 7px; color: var(--faint); font-size: .72rem; }

  /* ---------- tables ---------- */
  .table-wrap {
    overflow-x: auto; border: 1px solid var(--line); border-radius: 12px;
  }
  table { width: 100%; border-collapse: collapse; font-size: .85rem; }
  thead th {
    text-align: left; padding: 11px 14px;
    color: var(--muted); font-size: .72rem; font-weight: 600;
    letter-spacing: .07em; white-space: nowrap;
    background: rgba(255, 255, 255, .025);
    border-bottom: 1px solid var(--line);
  }
  tbody td {
    padding: 11px 14px; border-bottom: 1px solid rgba(255, 255, 255, .04);
    color: var(--text-2); vertical-align: middle;
  }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr:hover td { background: rgba(124, 140, 255, .055); color: var(--text); }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  td .num-inline { font-variant-numeric: tabular-nums; }
  code, .mono {
    font-family: var(--mono); font-size: .76rem; color: var(--text);
    background: rgba(255, 255, 255, .055); padding: 2px 6px; border-radius: 6px;
  }
  .muted { color: var(--muted); }
  .faint { color: var(--faint); }
  .empty {
    display: flex; flex-direction: column; align-items: center; gap: 9px;
    padding: 40px 16px; color: var(--faint); font-size: .84rem; text-align: center;
  }
  .empty .ico { width: 26px; height: 26px; color: var(--faint); }
  .error-text { color: var(--fail); font-size: .84rem; padding: 18px 4px; }

  /* ---------- chips ---------- */
  .chip {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 2px 9px; border-radius: 999px; border: 1px solid transparent;
    font-size: .71rem; font-weight: 600; line-height: 1.65; white-space: nowrap;
  }
  .chip.ok { color: #8ff0c4; background: rgba(12, 163, 12, .16); border-color: rgba(12, 163, 12, .34); }
  .chip.warn { color: #ffd88a; background: rgba(250, 178, 25, .14); border-color: rgba(250, 178, 25, .32); }
  .chip.crit { color: #ffb3b3; background: rgba(208, 59, 59, .18); border-color: rgba(208, 59, 59, .38); }
  .chip.neutral { color: var(--text-2); background: rgba(255, 255, 255, .045); border-color: rgba(255, 255, 255, .08); }
  .chip.llm { color: #a8d1ff; background: rgba(57, 135, 229, .16); border-color: rgba(57, 135, 229, .36); }
  .chip.tool { color: #94e8c8; background: rgba(25, 158, 112, .16); border-color: rgba(25, 158, 112, .36); }
  .chip.fail { color: #ffb3b3; background: rgba(230, 103, 103, .16); border-color: rgba(230, 103, 103, .34); }
  .chip.mem { color: #cdc6ff; background: rgba(144, 133, 233, .16); border-color: rgba(144, 133, 233, .34); }
  .chip.ctx { color: #ffd88a; background: rgba(201, 133, 0, .16); border-color: rgba(201, 133, 0, .34); }

  /* ---------- overview ---------- */
  .sys-list { display: flex; flex-direction: column; }
  .sys-row {
    display: grid; grid-template-columns: 150px 92px 1fr; gap: 14px;
    align-items: center; padding: 12px 4px;
    border-bottom: 1px solid rgba(255, 255, 255, .045);
    font-size: .85rem;
  }
  .sys-row:last-child { border-bottom: none; }
  .sys-name { color: var(--text); font-weight: 600; }
  .sys-note { color: var(--muted); font-size: .79rem; }
  .quick { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
  .quick-item {
    border: 1px solid var(--line); border-radius: 12px; padding: 13px 14px;
    background: rgba(255, 255, 255, .025);
  }
  .quick-item code { display: inline-block; margin-bottom: 7px; }
  .quick-item span { display: block; color: var(--muted); font-size: .78rem; }

  /* ---------- trace waterfall ---------- */
  .legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 12px 0 6px; color: var(--muted); font-size: .75rem; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); flex: none; }
  .dot.llm { background: var(--llm); }
  .dot.tool { background: var(--tool); }
  .dot.fail { background: var(--fail); }
  .dot.mem { background: var(--mem); }
  .dot.ctx { background: var(--ctx); }
  .dot.meta { background: var(--muted); }
  #trace-detail-card { scroll-margin-top: 96px; }
  .wf { margin-top: 6px; }
  .wf-row {
    display: grid; grid-template-columns: 78px 1fr; gap: 14px;
    padding: 10px 0; position: relative;
  }
  .wf-row + .wf-row { border-top: 1px dashed rgba(255, 255, 255, .05); }
  .wf-time {
    font-family: var(--mono); font-size: .73rem; color: var(--faint);
    text-align: right; padding-top: 3px; white-space: nowrap;
  }
  .wf-body { min-width: 0; }
  .wf-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .wf-label { font-size: .87rem; font-weight: 600; color: var(--text); }
  .wf-round { color: var(--faint); font-size: .73rem; font-family: var(--mono); }
  .wf-summary { margin-top: 4px; color: var(--muted); font-size: .79rem; line-height: 1.55; overflow-wrap: anywhere; }
  .wf-track {
    position: relative; height: 6px; margin-top: 8px;
    border-radius: 999px; background: rgba(255, 255, 255, .05);
  }
  .wf-track i {
    position: absolute; top: 0; bottom: 0; border-radius: 999px;
    background: var(--accent); min-width: 3px;
  }
  .wf-row.is-llm .wf-track i { background: var(--llm); }
  .wf-row.is-tool .wf-track i { background: var(--tool); }
  .wf-row.is-fail .wf-track i { background: var(--fail); }
  .wf-row.is-mem .wf-track i { background: var(--mem); }
  .wf-row.is-ctx .wf-track i { background: var(--ctx); }
  .wf-row.is-meta .wf-track i { background: var(--muted); }
  details.raw { margin-top: 8px; }
  details.raw summary { cursor: pointer; color: var(--faint); font-size: .74rem; }
  details.raw summary:hover { color: var(--muted); }
  details.raw pre {
    margin: 8px 0 0; padding: 13px; max-height: 320px; overflow: auto;
    background: #070b14; border: 1px solid rgba(255, 255, 255, .06);
    border-radius: 10px; color: var(--text-2);
    font-family: var(--mono); font-size: .73rem; line-height: 1.6;
  }

  /* ---------- memory ---------- */
  .filter-row { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
  .search {
    flex: 1; min-width: 220px; display: flex; align-items: center; gap: 8px;
    border: 1px solid var(--line); border-radius: 10px;
    background: rgba(255, 255, 255, .03); padding: 0 12px;
  }
  .search .ico { width: 15px; height: 15px; color: var(--faint); }
  .search input {
    flex: 1; background: transparent; border: none; outline: none;
    color: var(--text); font: inherit; font-size: .84rem; padding: 9px 0;
  }
  .search input::placeholder { color: var(--faint); }
  .conf { display: inline-flex; align-items: center; gap: 7px; }
  .conf-track { width: 54px; height: 5px; border-radius: 999px; background: rgba(255, 255, 255, .08); }
  .conf-track i { display: block; height: 100%; border-radius: 999px; background: var(--accent); }
  .conf-val { font-size: .73rem; color: var(--muted); font-variant-numeric: tabular-nums; }

  /* ---------- usage ---------- */
  .mini-bars {
    display: flex; align-items: flex-end; gap: 5px;
    height: 78px; padding: 10px 2px 0; margin: 6px 0 14px;
  }
  .mini-bars i {
    flex: 1; min-width: 4px; border-radius: 4px 4px 2px 2px;
    background: linear-gradient(180deg, #5598e7, #256abf);
    opacity: .88; transition: .15s ease;
  }
  .mini-bars i:hover { opacity: 1; transform: scaleY(1.05); transform-origin: bottom; }
  .bars-caption { color: var(--faint); font-size: .72rem; margin-bottom: 8px; }

  /* ---------- gate ---------- */
  .verdict {
    display: flex; align-items: center; gap: 14px;
    border: 1px solid var(--line); border-radius: 14px;
    padding: 18px 20px; margin: 14px 0 18px;
    background: rgba(255, 255, 255, .025);
  }
  .verdict.ok { border-color: rgba(12, 163, 12, .40); background: rgba(12, 163, 12, .07); }
  .verdict.crit { border-color: rgba(208, 59, 59, .40); background: rgba(208, 59, 59, .07); }
  .verdict-ico {
    width: 42px; height: 42px; border-radius: 12px; display: grid; place-items: center;
    font-size: 1.2rem; font-weight: 700; flex: none;
  }
  .verdict.ok .verdict-ico { color: #8ff0c4; background: rgba(12, 163, 12, .18); }
  .verdict.crit .verdict-ico { color: #ffb3b3; background: rgba(208, 59, 59, .18); }
  .verdict strong { display: block; font-size: 1rem; }
  .verdict span { display: block; color: var(--muted); font-size: .78rem; margin-top: 4px; }
  .tier-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }
  .tier {
    border: 1px solid var(--line); border-radius: 12px; padding: 15px 16px;
    background: rgba(255, 255, 255, .02);
  }
  .tier-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .tier-name { font-size: .86rem; font-weight: 600; }
  .tier p { margin: 9px 0 0; color: var(--muted); font-size: .77rem; line-height: 1.6; }

  @media (max-width: 900px) {
    .app { flex-direction: column; }
    .sidebar {
      position: relative; width: auto; flex: none; height: auto;
      flex-direction: row; align-items: center; gap: 14px;
      padding: 12px 16px; overflow-x: auto;
      border-right: none; border-bottom: 1px solid var(--line);
    }
    .brand-text, .sidebar-foot { display: none; }
    .brand { padding: 0; }
    .nav { flex-direction: row; }
    .nav-item span { display: none; }
    .nav-item { padding: 9px; }
    main { padding: 18px 16px 44px; }
    .topbar { padding: 14px 16px; }
    .ws-chip { display: none; }
    .sys-row { grid-template-columns: 1fr 90px; }
    .sys-note { grid-column: 1 / -1; }
    .wf-row { grid-template-columns: 62px 1fr; }
  }
</style>
</head>
<body>
<svg class="sprite" aria-hidden="true">
  <symbol id="i-home" viewBox="0 0 24 24"><path d="M3 10.5 12 3l9 7.5"/><path d="M5.5 9.5V21h13V9.5"/><path d="M10 21v-6h4v6"/></symbol>
  <symbol id="i-activity" viewBox="0 0 24 24"><path d="M3 12h4l2.5-6 4 12L16 12h5"/></symbol>
  <symbol id="i-brain" viewBox="0 0 24 24"><path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0 0 6v2a3 3 0 0 0 3 3h.5V4H9Z"/><path d="M15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 0 6v2a3 3 0 0 1-3 3h-.5V4H15Z"/><path d="M12 4v15"/></symbol>
  <symbol id="i-chat" viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-8 8H4l2-3a8 8 0 1 1 15-5Z"/><path d="M8.5 11h.01M12 11h.01M15.5 11h.01"/></symbol>
  <symbol id="i-chart" viewBox="0 0 24 24"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></symbol>
  <symbol id="i-shield" viewBox="0 0 24 24"><path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6l-7-3Z"/><path d="m9 12 2 2 4-4"/></symbol>
  <symbol id="i-refresh" viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 6.3"/><path d="M20 4v7h-7"/></symbol>
  <symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></symbol>
  <symbol id="i-cpu" viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/></symbol>
  <symbol id="i-folder" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/></symbol>
  <symbol id="i-coin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v10M9.5 9.5h4a1.8 1.8 0 0 1 0 3.6h-3a1.8 1.8 0 0 0 0 3.6h4"/></symbol>
  <symbol id="i-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></symbol>
  <symbol id="i-inbox" viewBox="0 0 24 24"><path d="M4 13h4l2 3h4l2-3h4"/><path d="M5 5h14l1 8v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-5l1-8Z"/></symbol>
</svg>

<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-mark">C</div>
      <div class="brand-text">
        <strong>CastorCoder</strong>
        <span>驾驶舱 · COCKPIT</span>
      </div>
    </div>
    <nav class="nav" aria-label="主导航">
      <button class="nav-item active" data-tab="tab-overview" title="总览" onclick="showTab('tab-overview')">
        <svg class="ico"><use href="#i-home"/></svg><span>总览</span>
      </button>
      <button class="nav-item" data-tab="tab-traces" title="运行轨迹" onclick="showTab('tab-traces')">
        <svg class="ico"><use href="#i-activity"/></svg><span>运行轨迹</span>
      </button>
      <button class="nav-item" data-tab="tab-memory" title="长期记忆" onclick="showTab('tab-memory')">
        <svg class="ico"><use href="#i-brain"/></svg><span>长期记忆</span>
      </button>
      <button class="nav-item" data-tab="tab-sessions" title="会话" onclick="showTab('tab-sessions')">
        <svg class="ico"><use href="#i-chat"/></svg><span>会话</span>
      </button>
      <button class="nav-item" data-tab="tab-usage" title="用量" onclick="showTab('tab-usage')">
        <svg class="ico"><use href="#i-chart"/></svg><span>用量</span>
      </button>
      <button class="nav-item" data-tab="tab-gate" title="发布门禁" onclick="showTab('tab-gate')">
        <svg class="ico"><use href="#i-shield"/></svg><span>发布门禁</span>
      </button>
    </nav>
    <div class="sidebar-foot">
      <span class="live-dot"></span><span>本地服务运行中</span>
      <span class="ver" id="version-badge">v-</span>
    </div>
  </aside>

  <div class="content">
    <header class="topbar">
      <div>
        <h1 id="page-title">总览</h1>
        <p id="page-sub">工作区运行状态、记忆与用量一览</p>
      </div>
      <div class="top-actions">
        <span class="ws-chip" id="top-workspace" title="当前工作区">读取中…</span>
        <button class="btn ghost" onclick="refreshCurrent()">
          <svg class="ico"><use href="#i-refresh"/></svg>刷新
        </button>
      </div>
    </header>

    <main>
      <!-- 总览 -->
      <section id="tab-overview" class="tab-content active">
        <div class="grid stats">
          <article class="stat" style="--tint:#7c8cff">
            <div class="stat-head"><span class="stat-label">工作区</span>
              <span class="stat-ico"><svg class="ico"><use href="#i-folder"/></svg></span></div>
            <div class="stat-value small" id="stat-workspace">-</div>
            <div class="stat-sub">所有路径解析的根目录</div>
          </article>
          <article class="stat" style="--tint:#3987e5">
            <div class="stat-head"><span class="stat-label">当前模型</span>
              <span class="stat-ico"><svg class="ico"><use href="#i-cpu"/></svg></span></div>
            <div class="stat-value small" id="stat-model">-</div>
            <div class="stat-sub" id="stat-model-sub">最近一次运行的模型</div>
          </article>
          <article class="stat" style="--tint:#9085e9">
            <div class="stat-head"><span class="stat-label">长期记忆</span>
              <span class="stat-ico"><svg class="ico"><use href="#i-brain"/></svg></span></div>
            <div class="stat-value" id="stat-memories">-</div>
            <div class="stat-sub">未失效的记忆条目</div>
          </article>
          <article class="stat" style="--tint:#38e0c8">
            <div class="stat-head"><span class="stat-label">运行轨迹</span>
              <span class="stat-ico"><svg class="ico"><use href="#i-activity"/></svg></span></div>
            <div class="stat-value" id="stat-traces">-</div>
            <div class="stat-sub">.castorcoder/traces/</div>
          </article>
          <article class="stat" style="--tint:#fab219">
            <div class="stat-head"><span class="stat-label">已保存会话</span>
              <span class="stat-ico"><svg class="ico"><use href="#i-chat"/></svg></span></div>
            <div class="stat-value" id="stat-sessions">-</div>
            <div class="stat-sub">castorcoder -c 可恢复</div>
          </article>
          <article class="stat" style="--tint:#0ca30c">
            <div class="stat-head"><span class="stat-label">累计花费</span>
              <span class="stat-ico"><svg class="ico"><use href="#i-coin"/></svg></span></div>
            <div class="stat-value" id="stat-spend">-</div>
            <div class="stat-sub">usage.jsonl 估算值</div>
          </article>
        </div>

        <div class="card">
          <div class="card-head">
            <h3>运行时子系统</h3>
            <span class="chip ok">全部就绪</span>
          </div>
          <p class="hint">主线循环与安全边界均由显式分层承担，不依赖隐式全局状态。</p>
          <div class="sys-list">
            <div class="sys-row"><span class="sys-name">Agent 主循环</span><span><span class="chip ok">运行中</span></span><span class="sys-note">ReAct 循环 · 波次图执行 · 轮次上限保护</span></div>
            <div class="sys-row"><span class="sys-name">安全边界</span><span><span class="chip ok">运行中</span></span><span class="sys-note">工作区路径收敛 · 权限模式 · 敏感信息脱敏</span></div>
            <div class="sys-row"><span class="sys-name">工具执行器</span><span><span class="chip ok">运行中</span></span><span class="sys-note">依赖感知并发 · 原子写入 · 超时与回滚</span></div>
            <div class="sys-row"><span class="sys-name">混合检索</span><span><span class="chip ok">运行中</span></span><span class="sys-note">SQLite FTS5 + 离线哈希向量 · RRF 融合</span></div>
            <div class="sys-row"><span class="sys-name">长期记忆</span><span><span class="chip ok">运行中</span></span><span class="sys-note">召回门禁 · 槽位上限 · 去重与过期</span></div>
          </div>
        </div>

        <div class="card">
          <div class="card-head"><h3>快捷操作</h3></div>
          <p class="hint">在终端里继续工作；驾驶舱只读取本地状态，不会修改工作区。</p>
          <div class="quick">
            <div class="quick-item"><code>castorcoder -c</code><span>恢复当前工作区最近的会话</span></div>
            <div class="quick-item"><code>/dashboard 7777</code><span>在对话中启动本驾驶舱</span></div>
            <div class="quick-item"><code>castorcoder gate</code><span>运行分层发布门禁并生成报告</span></div>
          </div>
        </div>
      </section>

      <!-- 运行轨迹 -->
      <section id="tab-traces" class="tab-content">
        <div class="card">
          <div class="card-head">
            <div>
              <h3>运行轨迹</h3>
              <p class="hint">每次启动默认写入 <code>.castorcoder/traces/</code>；点击任意运行查看逐轮瀑布、Token 与停止原因。</p>
            </div>
            <button class="btn small ghost" onclick="loadTraces()">
              <svg class="ico"><use href="#i-refresh"/></svg>刷新
            </button>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr>
                <th>运行</th><th>开始时间</th><th class="num">耗时</th><th class="num">事件</th>
                <th class="num">Token 入 / 出</th><th class="num">工具</th><th>停止原因</th><th></th>
              </tr></thead>
              <tbody id="traces-list"><tr><td colspan="8"><div class="empty">正在读取轨迹…</div></td></tr></tbody>
            </table>
          </div>
        </div>

        <div class="card" id="trace-detail-card" style="display:none">
          <div class="card-head">
            <h3 id="trace-detail-title">轨迹详情</h3>
            <button class="btn small ghost" onclick="document.getElementById('trace-detail-card').style.display='none'">收起</button>
          </div>
          <div id="trace-meta"></div>
          <div class="legend">
            <span><i class="dot llm"></i>模型调用</span>
            <span><i class="dot tool"></i>工具成功</span>
            <span><i class="dot fail"></i>工具失败</span>
            <span><i class="dot mem"></i>记忆</span>
            <span><i class="dot ctx"></i>上下文</span>
            <span><i class="dot meta"></i>生命周期</span>
          </div>
          <div id="waterfall-container" class="wf"></div>
        </div>
      </section>

      <!-- 长期记忆 -->
      <section id="tab-memory" class="tab-content">
        <div class="card">
          <div class="card-head">
            <div>
              <h3>长期记忆</h3>
              <p class="hint">工作区隔离的知识条目：架构约定、关键决策、排错经验与用户偏好。</p>
            </div>
            <button class="btn small ghost" onclick="loadMemories()">
              <svg class="ico"><use href="#i-refresh"/></svg>刷新
            </button>
          </div>
          <div class="filter-row">
            <label class="search">
              <svg class="ico"><use href="#i-search"/></svg>
              <input id="memory-filter" type="search" placeholder="按内容、类型或来源筛选…" oninput="renderMemories()">
            </label>
            <span class="chip neutral" id="memory-count">0 条</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>类型</th><th>内容</th><th>置信度</th><th>来源</th></tr></thead>
              <tbody id="memory-list"><tr><td colspan="4"><div class="empty">正在读取记忆…</div></td></tr></tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- 会话 -->
      <section id="tab-sessions" class="tab-content">
        <div class="card">
          <div class="card-head">
            <div>
              <h3>已保存会话</h3>
              <p class="hint">终端中运行 <code>castorcoder -c</code> 恢复最近一次，或用 <code>castorcoder -r</code> 选择历史对话。</p>
            </div>
            <button class="btn small ghost" onclick="loadSessions()">
              <svg class="ico"><use href="#i-refresh"/></svg>刷新
            </button>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>会话</th><th>模型</th><th class="num">轮次</th><th>保存时间</th><th>摘要</th></tr></thead>
              <tbody id="sessions-list"><tr><td colspan="5"><div class="empty">正在读取会话…</div></td></tr></tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- 用量 -->
      <section id="tab-usage" class="tab-content">
        <div class="grid">
          <article class="stat" style="--tint:#3987e5">
            <div class="stat-head"><span class="stat-label">运行次数</span></div>
            <div class="stat-value" id="usage-runs">-</div>
            <div class="stat-sub">最近 500 条记录</div>
          </article>
          <article class="stat" style="--tint:#38e0c8">
            <div class="stat-head"><span class="stat-label">输入 Token</span></div>
            <div class="stat-value" id="usage-in">-</div>
            <div class="stat-sub">输入 Token 数</div>
          </article>
          <article class="stat" style="--tint:#9085e9">
            <div class="stat-head"><span class="stat-label">输出 Token</span></div>
            <div class="stat-value" id="usage-out">-</div>
            <div class="stat-sub">输出 Token 数</div>
          </article>
          <article class="stat" style="--tint:#0ca30c">
            <div class="stat-head"><span class="stat-label">估算花费</span></div>
            <div class="stat-value" id="usage-cost">-</div>
            <div class="stat-sub">优先使用服务商返回值</div>
          </article>
          <article class="stat" style="--tint:#e66767">
            <div class="stat-head"><span class="stat-label">工具调用 / 失败</span></div>
            <div class="stat-value" id="usage-tools">-</div>
            <div class="stat-sub">明细见运行轨迹</div>
          </article>
        </div>

        <div class="card">
          <div class="card-head"><h3>最近运行</h3></div>
          <p class="hint">每次完成的运行在 <code>~/.castorcoder/usage.jsonl</code> 追加一行；账本不会被自动清空。</p>
          <div class="bars-caption">Token 用量（输入 + 输出），从旧到新</div>
          <div class="mini-bars" id="usage-bars"></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>时间</th><th>模型</th><th class="num">输入</th><th class="num">输出</th><th class="num">轮次</th><th class="num">工具</th><th>停止原因</th><th class="num">花费</th></tr></thead>
              <tbody id="usage-recent"><tr><td colspan="8"><div class="empty">正在读取账本…</div></td></tr></tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- 发布门禁 -->
      <section id="tab-gate" class="tab-content">
        <div class="card">
          <div class="card-head">
            <div>
              <h3>发布门禁</h3>
              <p class="hint">第一层单元测试与第二层离线回归全部通过，才允许发布。</p>
            </div>
            <button class="btn small ghost" onclick="loadGate()">
              <svg class="ico"><use href="#i-refresh"/></svg>刷新
            </button>
          </div>
          <div id="gate-summary"><div class="empty">正在读取门禁报告…</div></div>
        </div>
      </section>
    </main>
  </div>
</div>

<script>
  const TAB_META = {
    'tab-overview': ['总览', '工作区运行状态、记忆与用量一览'],
    'tab-traces': ['运行轨迹', '逐轮模型调用与工具执行的瀑布视图'],
    'tab-memory': ['长期记忆', '工作区隔离的约定、决策与经验'],
    'tab-sessions': ['会话', '已保存的对话，可用 castorcoder -c 恢复'],
    'tab-usage': ['用量', '全局运行账本与最近消耗'],
    'tab-gate': ['发布门禁', '单元测试与离线回归的分层校验'],
  };
  const STOP_LABELS = {
    completed: '正常完成', final: '正常完成', success: '正常完成',
    max_rounds: '达到轮次上限', loop_guard: '循环保护', cost_budget: '达到成本预算',
    cancelled: '已取消', tool_errors: '工具连续失败', error: '异常终止',
  };
  const STOP_TONES = {
    completed: 'ok', final: 'ok', success: 'ok',
    max_rounds: 'warn', loop_guard: 'warn', cost_budget: 'warn', cancelled: 'warn',
    tool_errors: 'crit', error: 'crit',
  };
  const KIND_LABELS = {
    episode: '历史片段', decision: '决策', convention: '约定',
    preference: '偏好', error: '错误', fact: '事实', gotcha: '注意事项',
  };
  const EVENT_LABELS = {
    'run.started': '运行开始', 'run.completed': '运行结束',
    'llm.started': '模型请求', 'llm.completed': '模型响应',
    'tool.started': '工具调用', 'tool.completed': '工具返回',
    'context.compressed': '上下文压缩', 'context.trimmed': '上下文裁剪',
    'memory.gate': '记忆门禁', 'memory.retrieved': '记忆召回', 'memory.written': '记忆写入',
    'autorun.completed': '自动验证',
  };
  const state = { traces: [], memories: [], currentTab: 'tab-overview' };

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
  }
  function fmtNum(value) {
    const n = Number(value || 0);
    return isFinite(n) ? n.toLocaleString('zh-CN') : '0';
  }
  function fmtMs(ms) {
    if (ms === null || ms === undefined || ms === '') return '—';
    const value = Number(ms);
    if (!isFinite(value)) return '—';
    if (value >= 60000) return (value / 60000).toFixed(1) + ' 分钟';
    if (value >= 1000) return (value / 1000).toFixed(2) + ' 秒';
    return Math.round(value) + ' 毫秒';
  }
  function fmtTime(epoch) {
    if (!epoch) return '—';
    const d = new Date(Number(epoch) * 1000);
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleString('zh-CN', { hour12: false });
  }
  function fmtCost(value) {
    const n = Number(value);
    return isFinite(n) ? '$' + n.toFixed(4) : '—';
  }
  function shortId(id) {
    const text = String(id || '');
    return text.replace(/^trace_/, '');
  }
  function stopLabel(stop) { return STOP_LABELS[stop] || stop || '—'; }
  function stopChip(stop) {
    if (!stop) return '<span class="chip neutral">—</span>';
    const tone = STOP_TONES[stop] || 'neutral';
    return '<span class="chip ' + tone + '">' + esc(stopLabel(stop)) + '</span>';
  }
  function kindLabel(kind) { return KIND_LABELS[kind] || kind || '—'; }
  function eventLabel(type) { return EVENT_LABELS[type] || type; }

  async function getJSON(url) {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }
  function errorRow(colspan, err) {
    return '<tr><td colspan="' + colspan + '"><div class="error-text">读取失败：' + esc(err.message || err) + '</div></td></tr>';
  }
  function emptyRow(colspan, text) {
    return '<tr><td colspan="' + colspan + '"><div class="empty">' +
      '<svg class="ico"><use href="#i-inbox"/></svg><span>' + esc(text) + '</span></div></td></tr>';
  }

  function showTab(tabId) {
    state.currentTab = tabId;
    document.querySelectorAll('.tab-content').forEach(el => el.classList.toggle('active', el.id === tabId));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.tab === tabId));
    const meta = TAB_META[tabId] || ['', ''];
    document.getElementById('page-title').textContent = meta[0];
    document.getElementById('page-sub').textContent = meta[1];
    const slug = tabId.replace(/^tab-/, '');
    if (location.hash.slice(1) !== slug) history.replaceState(null, '', '#' + slug);
    refreshTab(tabId);
  }
  function refreshTab(tabId) {
    if (tabId === 'tab-overview') loadStatus();
    if (tabId === 'tab-traces') loadTraces();
    if (tabId === 'tab-memory') loadMemories();
    if (tabId === 'tab-sessions') loadSessions();
    if (tabId === 'tab-usage') loadUsage();
    if (tabId === 'tab-gate') loadGate();
  }
  function refreshCurrent() { refreshTab(state.currentTab); }

  async function loadStatus() {
    try {
      const data = await getJSON('/api/status');
      document.getElementById('stat-workspace').textContent = data.workspace || '默认工作区';
      document.getElementById('stat-workspace').title = data.workspace || '';
      document.getElementById('top-workspace').textContent = data.workspace || '默认工作区';
      document.getElementById('top-workspace').title = data.workspace || '';
      const model = data.model || '未配置';
      document.getElementById('stat-model').textContent = model;
      document.getElementById('stat-model').title = model;
      document.getElementById('stat-model-sub').textContent = data.model
        ? '最近一次运行的模型' : '可在 .castorcoder/config.toml 中设置';
      document.getElementById('stat-memories').textContent = fmtNum(data.memory_count);
      document.getElementById('stat-traces').textContent = fmtNum(data.trace_count);
      document.getElementById('stat-sessions').textContent = fmtNum(data.session_count);
      document.getElementById('stat-spend').textContent = fmtCost(data.spend_usd);
      document.getElementById('version-badge').textContent = 'v' + (data.version || '-');
    } catch (err) {
      document.getElementById('stat-model').textContent = '读取失败';
    }
  }

  async function loadTraces() {
    const tbody = document.getElementById('traces-list');
    try {
      const list = await getJSON('/api/traces');
      state.traces = list || [];
      if (!state.traces.length) {
        tbody.innerHTML = emptyRow(8, '还没有轨迹。运行一次任务后，这里会出现逐轮瀑布。');
        return;
      }
      tbody.innerHTML = state.traces.map(t => `
        <tr>
          <td><code title="${esc(t.file || t.run_id)}">${esc(shortId(t.run_id))}</code></td>
          <td class="muted">${esc(t.started_at ? fmtTime(t.started_at) : t.timestamp)}</td>
          <td class="num">${fmtMs(t.duration_ms)}</td>
          <td class="num">${fmtNum(t.events)}</td>
          <td class="num"><span class="num-inline">${fmtNum(t.tokens_in)} / ${fmtNum(t.tokens_out)}</span></td>
          <td class="num">${fmtNum(t.tool_calls)}${Number(t.failures) ? ' <span class="chip fail">' + fmtNum(t.failures) + ' 失败</span>' : ''}</td>
          <td>${stopChip(t.stop_reason)}</td>
          <td><button class="btn small" onclick="viewTrace('${esc(t.run_id)}')">查看瀑布</button></td>
        </tr>`).join('');
      const deepTrace = new URLSearchParams(location.search).get('trace');
      if (deepTrace && !state.deepTraceOpened) {
        state.deepTraceOpened = true;
        viewTrace(deepTrace, false);
      }
    } catch (err) {
      tbody.innerHTML = errorRow(8, err);
    }
  }

  function eventKind(e) {
    const d = e.data || {};
    if (String(e.type).startsWith('llm.')) return 'llm';
    if (e.type === 'tool.completed') return d.success === false ? 'fail' : 'tool';
    if (String(e.type).startsWith('tool.')) return 'tool';
    if (String(e.type).startsWith('memory.')) return 'mem';
    if (String(e.type).startsWith('context.')) return 'ctx';
    return 'meta';
  }
  function kindChip(kind) {
    const names = { llm: '模型', tool: '工具', fail: '失败', mem: '记忆', ctx: '上下文', meta: '系统' };
    const cls = kind === 'meta' ? 'neutral' : kind;
    return '<span class="chip ' + cls + '">' + names[kind] + '</span>';
  }
  function eventSummary(e) {
    const d = e.data || {};
    if (e.type === 'run.started') {
      const effort = d.reasoning_effort && d.reasoning_effort !== 'auto' ? ' · 思考强度 ' + d.reasoning_effort : '';
      return d.model ? '模型 ' + d.model + effort : '开始一次运行';
    }
    if (e.type === 'run.completed') {
      const stats = d.stats || {};
      return '停止原因：' + stopLabel(d.stop_reason || stats.stop_reason);
    }
    if (e.type === 'llm.started') return '第 ' + (d.round || '?') + ' 轮推理';
    if (e.type === 'llm.completed') {
      const bits = [];
      if (d.prompt_tokens != null) bits.push(fmtNum(d.prompt_tokens) + ' 输入');
      if (d.completion_tokens != null) bits.push(fmtNum(d.completion_tokens) + ' 输出');
      if (d.tool_calls) bits.push(String(d.tool_calls) + ' 个工具调用');
      if (d.duration_ms != null) bits.push(fmtMs(d.duration_ms));
      return bits.join(' · ');
    }
    if (e.type === 'tool.started') {
      const args = JSON.stringify(d.arguments || {});
      return (d.name || '工具') + ' ' + (args.length > 110 ? args.slice(0, 110) + '…' : args);
    }
    if (e.type === 'tool.completed') {
      const bits = [(d.name || '工具'), d.success === false ? '失败' : '成功'];
      if (d.duration_ms != null) bits.push(fmtMs(d.duration_ms));
      if (d.output_preview) bits.push(String(d.output_preview).slice(0, 90));
      return bits.join(' · ');
    }
    if (e.type === 'context.trimmed') return '裁剪 ' + fmtNum(d.count) + ' 条旧工具结果';
    if (e.type === 'context.compressed') return '压缩到约 ' + fmtNum(d.tokens_estimate) + ' tokens';
    if (e.type === 'memory.retrieved') return '召回 ' + fmtNum(d.count) + ' 条记忆';
    if (e.type === 'memory.written') return '写入 ' + fmtNum(d.count) + ' 条记忆';
    if (e.type === 'memory.gate') return d.retrieve === false ? '本轮跳过记忆召回' : '本轮允许记忆召回';
    if (e.type === 'autorun.completed') return (d.passed ? '验证通过' : '验证失败') + (d.command ? ' · ' + d.command : '');
    return '';
  }

  async function viewTrace(runId, scroll = true) {
    const card = document.getElementById('trace-detail-card');
    const container = document.getElementById('waterfall-container');
    const meta = document.getElementById('trace-meta');
    card.style.display = 'block';
    document.getElementById('trace-detail-title').textContent = '轨迹详情 · ' + shortId(runId);
    meta.innerHTML = '<div class="empty">正在读取事件…</div>';
    container.innerHTML = '';
    try {
      const events = await getJSON('/api/traces/' + encodeURIComponent(runId));
      if (!events || !events.length) {
        meta.innerHTML = '<div class="empty">这个轨迹文件里没有事件。</div>';
        return;
      }
      const base = Number(events[0].timestamp || 0);
      const end = Number(events[events.length - 1].timestamp || base);
      const total = Math.max((end - base) * 1000, 1);
      let tokensIn = 0, tokensOut = 0, toolCalls = 0, failures = 0;
      events.forEach(e => {
        const d = e.data || {};
        if (e.type === 'llm.completed') {
          tokensIn += Number(d.prompt_tokens || 0);
          tokensOut += Number(d.completion_tokens || 0);
        }
        if (e.type === 'tool.completed') {
          toolCalls += 1;
          if (d.success === false) failures += 1;
        }
      });
      const done = [...events].reverse().find(e => e.type === 'run.completed');
      const stop = done ? ((done.data || {}).stop_reason || (((done.data || {}).stats || {}).stop_reason)) : null;
      const modelEvent = events.find(e => e.type === 'run.started');
      const model = modelEvent ? (modelEvent.data || {}).model : null;
      meta.innerHTML = `
        <div class="grid">
          <article class="stat" style="--tint:#38e0c8"><div class="stat-head"><span class="stat-label">事件数</span></div>
            <div class="stat-value">${fmtNum(events.length)}</div><div class="stat-sub">${model ? '模型 ' + esc(model) : '未知模型'}</div></article>
          <article class="stat" style="--tint:#7c8cff"><div class="stat-head"><span class="stat-label">总耗时</span></div>
            <div class="stat-value">${fmtMs(total)}</div><div class="stat-sub">首尾事件间隔</div></article>
          <article class="stat" style="--tint:#3987e5"><div class="stat-head"><span class="stat-label">Token 入 / 出</span></div>
            <div class="stat-value">${fmtNum(tokensIn)} / ${fmtNum(tokensOut)}</div><div class="stat-sub">所有模型轮次合计</div></article>
          <article class="stat" style="--tint:#199e70"><div class="stat-head"><span class="stat-label">工具调用</span></div>
            <div class="stat-value">${fmtNum(toolCalls)}${failures ? ' <span class="chip fail">' + fmtNum(failures) + ' 失败</span>' : ''}</div>
            <div class="stat-sub">按完成事件统计</div></article>
          <article class="stat" style="--tint:${failures || STOP_TONES[stop] === 'crit' ? '#e66767' : '#0ca30c'}"><div class="stat-head"><span class="stat-label">停止原因</span></div>
            <div class="stat-value small">${esc(stopLabel(stop))}</div><div class="stat-sub">来自运行结束事件</div></article>
        </div>`;
      container.innerHTML = events.map(e => {
        const d = e.data || {};
        const kind = eventKind(e);
        const offset = Math.max(0, (Number(e.timestamp || base) - base) * 1000);
        const duration = Number(d.duration_ms || 0);
        const left = Math.min(99.4, offset / total * 100);
        const width = duration > 0 ? Math.max(duration / total * 100, 1.2) : 0.6;
        const summary = eventSummary(e);
        const round = d.round ? '<span class="wf-round">第 ' + esc(d.round) + ' 轮</span>' : '';
        return `
          <div class="wf-row is-${kind}">
            <div class="wf-time">+${fmtMs(offset)}</div>
            <div class="wf-body">
              <div class="wf-title">${kindChip(kind)}<span class="wf-label">${esc(eventLabel(e.type))}</span>${round}${duration ? '<span class="wf-round">' + fmtMs(duration) + '</span>' : ''}</div>
              ${summary ? '<div class="wf-summary">' + esc(summary) + '</div>' : ''}
              <div class="wf-track"><i style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"></i></div>
              <details class="raw"><summary>原始事件</summary><pre>${esc(JSON.stringify(d, null, 2))}</pre></details>
            </div>
          </div>`;
      }).join('');
      if (scroll) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
      meta.innerHTML = '<div class="error-text">读取失败：' + esc(err.message || err) + '</div>';
    }
  }

  async function loadMemories() {
    const tbody = document.getElementById('memory-list');
    try {
      state.memories = await getJSON('/api/memories') || [];
      renderMemories();
    } catch (err) {
      tbody.innerHTML = errorRow(4, err);
    }
  }
  function renderMemories() {
    const tbody = document.getElementById('memory-list');
    const query = (document.getElementById('memory-filter').value || '').trim().toLowerCase();
    const rows = state.memories.filter(m => !query || [
      m.content, m.kind, kindLabel(m.kind), m.source, (m.tags || []).join(' ')
    ].join(' ').toLowerCase().includes(query));
    document.getElementById('memory-count').textContent = rows.length + ' 条';
    if (!rows.length) {
      tbody.innerHTML = emptyRow(4, state.memories.length ? '没有匹配的记忆。' : '还没有记忆。完成一次任务后会自动沉淀。');
      return;
    }
    tbody.innerHTML = rows.map(m => {
      const conf = Math.max(0, Math.min(1, Number(m.confidence) || 0));
      return `
        <tr>
          <td><span class="chip neutral">${esc(kindLabel(m.kind))}</span></td>
          <td>${esc(m.content)}</td>
          <td><span class="conf"><span class="conf-track"><i style="width:${(conf * 100).toFixed(0)}%"></i></span><span class="conf-val">${(conf * 100).toFixed(0)}%</span></span></td>
          <td class="muted" title="${esc(m.source || '')}"><span class="mono">${esc((m.source || 'agent').slice(0, 28))}</span></td>
        </tr>`;
    }).join('');
  }

  async function loadSessions() {
    const tbody = document.getElementById('sessions-list');
    try {
      const list = await getJSON('/api/sessions');
      if (!list || !list.length) {
        tbody.innerHTML = emptyRow(5, '还没有保存的会话。');
        return;
      }
      tbody.innerHTML = list.map(s => `
        <tr>
          <td><code>${esc(s.id)}</code></td>
          <td>${esc(s.model || '—')}</td>
          <td class="num">${fmtNum(s.turns)}</td>
          <td class="muted">${esc(s.saved_at || '—')}</td>
          <td class="muted" title="${esc(s.preview || '')}">${esc((s.preview || '').slice(0, 80))}</td>
        </tr>`).join('');
    } catch (err) {
      tbody.innerHTML = errorRow(5, err);
    }
  }

  async function loadUsage() {
    const tbody = document.getElementById('usage-recent');
    const bars = document.getElementById('usage-bars');
    try {
      const u = await getJSON('/api/usage');
      document.getElementById('usage-runs').textContent = fmtNum(u.runs);
      document.getElementById('usage-in').textContent = fmtNum(u.prompt_tokens);
      document.getElementById('usage-out').textContent = fmtNum(u.completion_tokens);
      document.getElementById('usage-cost').textContent = fmtCost(u.cost_usd);
      document.getElementById('usage-tools').textContent = fmtNum(u.tool_calls) + ' / ' + fmtNum(u.failures);
      const recent = (u.recent || []).slice().reverse();
      if (!recent.length) {
        bars.innerHTML = '';
        tbody.innerHTML = emptyRow(8, '账本还没有记录。完成一次运行后会出现在这里。');
        return;
      }
      const totals = recent.map(r => Number(r.prompt_tokens || 0) + Number(r.completion_tokens || 0));
      const max = Math.max(1, ...totals);
      bars.innerHTML = recent.map((r, i) => {
        const total = totals[i];
        const height = Math.max(4, Math.round(total / max * 100));
        const title = (r.ts || '') + ' · ' + fmtNum(total) + ' tokens · ' + fmtCost(r.cost_usd);
        return '<i style="height:' + height + '%" title="' + esc(title) + '"></i>';
      }).join('');
      tbody.innerHTML = recent.slice().reverse().map(r => `
        <tr>
          <td class="muted">${esc((r.ts || '—').replace('T', ' '))}</td>
          <td><span class="mono">${esc(r.model || '—')}</span></td>
          <td class="num">${fmtNum(r.prompt_tokens)}</td>
          <td class="num">${fmtNum(r.completion_tokens)}</td>
          <td class="num">${fmtNum(r.rounds)}</td>
          <td class="num">${fmtNum(r.tool_calls)}${Number(r.tool_failures) ? ' <span class="chip fail">' + fmtNum(r.tool_failures) + ' 失败</span>' : ''}</td>
          <td>${stopChip(r.stop_reason)}</td>
          <td class="num">${fmtCost(r.cost_usd)}</td>
        </tr>`).join('');
    } catch (err) {
      bars.innerHTML = '';
      tbody.innerHTML = errorRow(8, err);
    }
  }

  async function loadGate() {
    const cont = document.getElementById('gate-summary');
    try {
      const gate = await getJSON('/api/gate');
      if (!gate || (!gate.timestamp && gate.passed === undefined && gate.verdict === undefined)) {
        cont.innerHTML = '<div class="empty">还没有门禁报告。运行 <code>castorcoder gate</code> 生成。</div>';
        return;
      }
      const passed = gate.passed === true || gate.verdict === 'SHIP';
      const t1 = gate.tier1_unit_tests === true || gate.tier1_unit_tests === 'PASS';
      const t2 = gate.tier2_regression_suite === true || gate.tier2_regression_suite === 'PASS';
      const d1 = gate.tier1_details || {};
      const d2 = gate.tier2_details || {};
      const detail = d => {
        const bits = [];
        if (d.passed != null) bits.push('通过 ' + fmtNum(d.passed));
        if (d.failed != null) bits.push('失败 ' + fmtNum(d.failed));
        if (d.total != null) bits.push('共 ' + fmtNum(d.total));
        if (d.exit_code != null) bits.push('退出码 ' + d.exit_code);
        if (d.error) bits.push(d.error);
        return bits.join(' · ') || '无详细计数';
      };
      const reasons = {
        'All release gates passed.': '所有发布门禁均通过。',
        'One or more gate tiers failed.': '至少一个门禁层级未通过。',
      };
      const reason = reasons[gate.reason] || gate.reason
        || (passed ? '所有发布门禁均通过。' : '至少一个门禁层级未通过。');
      cont.innerHTML = `
        <div class="verdict ${passed ? 'ok' : 'crit'}">
          <div class="verdict-ico">${passed ? '✓' : '!'}</div>
          <div><strong>${passed ? '门禁通过，可以发布' : '门禁未通过，暂缓发布'}</strong>
            <span>评估时间：${esc((gate.timestamp || '—').replace('T', ' '))}</span></div>
        </div>
        <div class="tier-grid">
          <div class="tier">
            <div class="tier-head"><span class="tier-name">第一层 · 单元测试</span>
              <span class="chip ${t1 ? 'ok' : 'crit'}">${t1 ? '通过' : '失败'}</span></div>
            <p>${esc(detail(d1))}</p>
          </div>
          <div class="tier">
            <div class="tier-head"><span class="tier-name">第二层 · 离线回归</span>
              <span class="chip ${t2 ? 'ok' : 'crit'}">${t2 ? '通过' : '失败'}</span></div>
            <p>${esc(detail(d2))}</p>
          </div>
        </div>
        <p class="hint" style="margin-top:14px">${esc(reason)}</p>`;
    } catch (err) {
      cont.innerHTML = '<div class="error-text">读取失败：' + esc(err.message || err) + '</div>';
    }
  }

  function bootFromLocation() {
    loadStatus();
    const wanted = 'tab-' + (location.hash.slice(1) || 'overview');
    if (TAB_META[wanted] && wanted !== 'tab-overview') showTab(wanted);
  }
  window.addEventListener('hashchange', () => {
    const wanted = 'tab-' + location.hash.slice(1);
    if (TAB_META[wanted] && wanted !== state.currentTab) showTab(wanted);
  });
  bootFromLocation();
</script>
</body>
</html>
"""
