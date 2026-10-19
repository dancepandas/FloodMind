"""生成单文件、离线可用的知识图谱交互式可视化。

输出的 HTML 不依赖任何 CDN 或外部资源：图数据内嵌为 JSON，
布局、渲染与交互全部由内联脚本完成。视图包括文件级 import 图、
分层聚合图、子系统语义图，以及按文件下钻的符号调用图。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_viz_payload(
    graph_payload: dict[str, Any],
    metrics: dict[str, Any],
    subsystems: dict[str, Any],
) -> dict[str, Any]:
    """裁剪 graph.json，只保留可视化需要的字段。

    ``defines`` 边不进入载荷：符号节点自带 ``path``，下钻时按路径归组即可，
    去掉后能把内嵌数据减少约三分之一。
    """

    nodes = [
        {
            "id": node["id"],
            "kind": node["kind"],
            "name": node["name"],
            "path": node["path"],
            "layer": node["layer"],
            "line": node.get("line", 0),
            "loc": node.get("loc", 0),
        }
        for node in graph_payload.get("nodes", [])
    ]
    edges = [
        edge
        for edge in graph_payload.get("edges", [])
        if edge.get("kind") != "defines"
    ]
    return {
        "schema_version": "1.0",
        "generated_at": graph_payload.get("generated_at", ""),
        "root": graph_payload.get("root", ""),
        "stats": graph_payload.get("stats", {}),
        "nodes": nodes,
        "edges": edges,
        "metrics": {
            "summary": metrics.get("summary", {}),
            "layers": metrics.get("layers", []),
            "layer_edges": metrics.get("layer_edges", []),
            "god_files": metrics.get("god_files", [])[:15],
            "cycles": metrics.get("cycles", []),
        },
        "subsystems": {
            "nodes": subsystems.get("nodes", []),
            "edges": subsystems.get("edges", []),
        },
    }


def render_html(viz: dict[str, Any]) -> str:
    """把载荷嵌入模板，返回完整的 HTML 文本。"""

    data = json.dumps(viz, ensure_ascii=False, separators=(",", ":"))
    # 防止数据中的 "</script>" 提前闭合内嵌 JSON 块；"\/" 是合法的 JSON 转义。
    data = data.replace("</", "<\\/")
    return _TEMPLATE.replace("__GRAPH_DATA__", data)


def write_visualization(
    *,
    graph_payload: dict[str, Any],
    metrics: dict[str, Any],
    subsystems: dict[str, Any],
    out_path: Path,
) -> Path:
    """写出可视化 HTML，返回输出路径。"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    viz = build_viz_payload(graph_payload, metrics, subsystems)
    out_path.write_text(render_html(viz), encoding="utf-8")
    return out_path


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FloodMind 知识图谱</title>
<style>
  :root {
    --bg: #0b0f17;
    --panel: #121826;
    --panel-2: #182132;
    --border: #263349;
    --text: #e6edf7;
    --muted: #8fa1ba;
    --accent: #f0b45a;
    --good: #4fc3a1;
    --danger: #d46a6a;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font: 13px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei",
          "PingFang SC", "Noto Sans CJK SC", sans-serif;
    overflow: hidden;
  }
  header {
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 10px 18px; border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, #131a29, #0e1420);
  }
  .brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .logo {
    width: 34px; height: 34px; border-radius: 9px; flex: none;
    display: grid; place-items: center; font-weight: 700; letter-spacing: .5px;
    color: #101623; background: linear-gradient(135deg, #f0b45a, #d88b3f);
  }
  h1 { font-size: 16px; margin: 0; font-weight: 600; }
  .sub { color: var(--muted); font-size: 11px; white-space: nowrap; overflow: hidden;
         text-overflow: ellipsis; }
  .stats { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
  .chip {
    border: 1px solid var(--border); background: var(--panel);
    border-radius: 999px; padding: 3px 10px; font-size: 11px; color: var(--muted);
    white-space: nowrap;
  }
  .chip b { color: var(--text); font-weight: 600; }
  main { display: flex; height: calc(100vh - 57px); }
  aside {
    width: 322px; flex: none; overflow-y: auto; border-right: 1px solid var(--border);
    background: var(--panel); padding: 12px;
  }
  aside::-webkit-scrollbar, #detailsBody::-webkit-scrollbar { width: 8px; }
  aside::-webkit-scrollbar-thumb, #detailsBody::-webkit-scrollbar-thumb {
    background: #2b3a54; border-radius: 4px;
  }
  .panel {
    border: 1px solid var(--border); border-radius: 10px;
    background: var(--panel-2); padding: 10px 11px; margin-bottom: 10px;
  }
  .panel h2 {
    font-size: 12px; margin: 0 0 8px; color: var(--muted);
    font-weight: 600; letter-spacing: .04em; text-transform: uppercase;
    display: flex; justify-content: space-between; align-items: baseline; gap: 8px;
  }
  .hint { font-size: 10px; font-weight: 400; text-transform: none; letter-spacing: 0;
          color: #6d7f99; }
  .tabs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
  .tabs button, .toolbtn {
    appearance: none; border: 1px solid var(--border); background: #101623;
    color: var(--muted); border-radius: 7px; padding: 7px 6px; cursor: pointer;
    font: inherit; font-size: 12px;
  }
  .tabs button:hover, .toolbtn:hover { border-color: #3b5072; color: var(--text); }
  .tabs button.active {
    color: #101623; background: var(--accent); border-color: var(--accent);
    font-weight: 600;
  }
  .symbolbar {
    margin-top: 8px; border-top: 1px dashed var(--border); padding-top: 8px;
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  }
  .symbolbar code {
    color: var(--accent); font-size: 11px; word-break: break-all;
  }
  .hidden { display: none !important; }
  #search {
    width: 100%; border: 1px solid var(--border); border-radius: 7px;
    background: #0d1320; color: var(--text); padding: 7px 9px; font: inherit;
    outline: none;
  }
  #search:focus { border-color: #3b5072; }
  .results { margin-top: 6px; display: flex; flex-direction: column; gap: 2px; }
  .result {
    appearance: none; border: 0; background: transparent; color: var(--text);
    text-align: left; padding: 5px 6px; border-radius: 6px; cursor: pointer;
    font: inherit; font-size: 12px; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap;
  }
  .result:hover, .result.active { background: #1e2b40; }
  .result .tag {
    display: inline-block; font-size: 10px; color: var(--muted);
    border: 1px solid var(--border); border-radius: 4px; padding: 0 4px;
    margin-right: 6px;
  }
  .legend { display: flex; flex-direction: column; gap: 2px; }
  .legend-row {
    display: flex; align-items: center; gap: 7px; padding: 4px 6px;
    border-radius: 6px; cursor: pointer; user-select: none;
  }
  .legend-row:hover { background: #1e2b40; }
  .legend-row.off { opacity: .38; }
  .swatch { width: 9px; height: 9px; border-radius: 3px; flex: none; }
  .legend-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
                 white-space: nowrap; }
  .legend-count { color: var(--muted); font-size: 10px; white-space: nowrap; }
  #stage { position: relative; flex: 1; min-width: 0; background:
    radial-gradient(circle at 30% 20%, #101827 0%, #0b0f17 60%); }
  canvas { display: block; width: 100%; height: 100%; cursor: grab; }
  canvas.dragging { cursor: grabbing; }
  #hint {
    position: absolute; left: 14px; bottom: 12px; color: #667a96; font-size: 11px;
    pointer-events: none; background: rgba(11,15,23,.72); padding: 4px 8px;
    border-radius: 6px;
  }
  #loading {
    position: absolute; inset: 0; display: grid; place-items: center;
    background: rgba(11,15,23,.65); color: var(--muted); font-size: 13px;
    pointer-events: none; transition: opacity .2s; z-index: 5;
  }
  #tooltip {
    position: absolute; z-index: 6; pointer-events: none; max-width: 340px;
    background: #0d1320ee; border: 1px solid #33455f; border-radius: 8px;
    padding: 6px 9px; box-shadow: 0 8px 24px #0008; font-size: 12px;
  }
  #tooltip .tt-title { font-weight: 600; margin-bottom: 2px; }
  #tooltip .tt-meta { color: var(--muted); font-size: 11px; word-break: break-all; }
  #detailsBody { max-height: 42vh; overflow-y: auto; }
  #detailsBody p { margin: 0 0 6px; }
  .muted { color: var(--muted); }
  .kv { display: grid; grid-template-columns: 74px 1fr; gap: 3px 8px; margin: 6px 0; }
  .kv .k { color: var(--muted); font-size: 11px; }
  .kv .v { word-break: break-all; }
  .section-title {
    color: var(--muted); font-size: 11px; margin: 10px 0 4px;
    letter-spacing: .03em;
  }
  .linklist { display: flex; flex-direction: column; gap: 2px; }
  .link {
    appearance: none; text-align: left; font: inherit; font-size: 12px;
    color: #9cc2f0; background: transparent; border: 0; padding: 2px 4px;
    border-radius: 4px; cursor: pointer; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap;
  }
  .link:hover { background: #1e2b40; color: #cfe3ff; }
  .badge {
    display: inline-block; font-size: 10px; border-radius: 4px; padding: 1px 6px;
    border: 1px solid #3b5072; color: #9cc2f0; margin-left: 6px;
    vertical-align: 1px;
  }
  .empty { color: #6d7f99; font-size: 12px; padding: 4px 0; }
  @media (max-width: 860px) {
    aside { width: 258px; }
    .stats { display: none; }
  }
</style>
</head>
<body>
<header>
  <div class="brand">
    <div class="logo">FG</div>
    <div style="min-width:0">
      <h1>FloodMind 知识图谱</h1>
      <div class="sub" id="generated"></div>
    </div>
  </div>
  <div class="stats" id="stats"></div>
</header>
<main>
  <aside>
    <section class="panel">
      <h2>视图</h2>
      <div class="tabs" id="tabs">
        <button data-mode="file" class="active">文件</button>
        <button data-mode="layer">分层</button>
        <button data-mode="subsystem">子系统</button>
      </div>
      <div class="symbolbar hidden" id="symbolBar">
        <button class="toolbtn" id="backBtn">返回文件视图</button>
        <code id="focusPath"></code>
      </div>
    </section>
    <section class="panel">
      <h2>搜索</h2>
      <input id="search" autocomplete="off" spellcheck="false"
             placeholder="文件 / 符号 / 子系统，回车定位">
      <div class="results" id="searchResults"></div>
    </section>
    <section class="panel">
      <h2>分层 <span class="hint">点击隐藏/显示</span></h2>
      <div class="legend" id="legend"></div>
    </section>
    <section class="panel">
      <h2>详情</h2>
      <div id="detailsBody"><p class="muted">单击图中的节点查看详情。</p></div>
    </section>
  </aside>
  <div id="stage">
    <canvas id="canvas"></canvas>
    <div id="hint">滚轮缩放 · 拖拽平移 · 单击选中 · 双击文件展开符号</div>
    <div id="tooltip" class="hidden"></div>
    <div id="loading">正在布局…</div>
  </div>
</main>
<script type="application/json" id="graph-data">__GRAPH_DATA__</script>
<script>
(function () {
  'use strict';

  var DATA = JSON.parse(document.getElementById('graph-data').textContent);
  var $ = function (id) { return document.getElementById(id); };
  var canvas = $('canvas');
  var ctx = canvas.getContext('2d');
  var tooltip = $('tooltip');
  var loading = $('loading');

  var PALETTE = [
    '#5b8def', '#4fc3a1', '#e0b34c', '#d46a6a', '#9b7be8',
    '#4bb3d3', '#8cbf5a', '#e08a4a', '#c76db1', '#7d8fa8',
    '#5fa8a0', '#b98b5e', '#6f9fd8', '#a3b25f', '#d2648c'
  ];

  var layerMeta = new Map(DATA.metrics.layers.map(function (l) { return [l.layer, l]; }));
  var layerOrder = DATA.metrics.layers.map(function (l) { return l.layer; });
  var layerColor = new Map();
  layerOrder.forEach(function (layer, i) {
    layerColor.set(layer, PALETTE[i % PALETTE.length]);
  });

  function colorFor(layer) {
    if (layerColor.has(layer)) return layerColor.get(layer);
    var h = 0;
    for (var i = 0; i < layer.length; i++) h = (h * 31 + layer.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }
  function titleFor(layer) {
    var meta = layerMeta.get(layer);
    return meta && meta.title ? meta.title : (layer || '未分层');
  }

  var importEdges = DATA.edges.filter(function (e) { return e.kind === 'imports'; });
  var callEdges = DATA.edges.filter(function (e) { return e.kind === 'calls'; });
  var inheritEdges = DATA.edges.filter(function (e) { return e.kind === 'inherits'; });
  var nodeById = new Map(DATA.nodes.map(function (n) { return [n.id, n]; }));
  var importIn = new Map(), importOut = new Map();
  var callIn = new Map(), callOut = new Map();
  importEdges.forEach(function (e) {
    importIn.set(e.dst, (importIn.get(e.dst) || 0) + 1);
    importOut.set(e.src, (importOut.get(e.src) || 0) + 1);
  });
  callEdges.forEach(function (e) {
    callIn.set(e.dst, (callIn.get(e.dst) || 0) + 1);
    callOut.set(e.src, (callOut.get(e.src) || 0) + 1);
  });
  var symbolsByPath = new Map();
  DATA.nodes.forEach(function (n) {
    if (n.kind === 'file') return;
    if (!symbolsByPath.has(n.path)) symbolsByPath.set(n.path, []);
    symbolsByPath.get(n.path).push(n);
  });
  var callersOf = new Map(), calleesOf = new Map();
  callEdges.forEach(function (e) {
    if (!callersOf.has(e.dst)) callersOf.set(e.dst, []);
    callersOf.get(e.dst).push(e.src);
    if (!calleesOf.has(e.src)) calleesOf.set(e.src, []);
    calleesOf.get(e.src).push(e.dst);
  });

  var state = {
    mode: 'file',
    focusPath: '',
    hidden: new Set(),
    selectedId: null,
    hoverId: null,
    scale: 1,
    tx: 0,
    ty: 0
  };

  var sim = {
    nodes: [], edges: [], byId: new Map(), adj: new Map(),
    alpha: 0, running: false
  };

  function mulberry32(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* ---------------- 视图构建 ---------------- */

  function buildNodesAndEdges() {
    var nodes = [], edges = [];
    if (state.mode === 'file') {
      var visible = DATA.nodes.filter(function (n) {
        return n.kind === 'file' && !state.hidden.has(n.layer);
      });
      var ids = new Set(visible.map(function (n) { return n.id; }));
      visible.forEach(function (n) {
        var deg = (importIn.get(n.id) || 0) + (importOut.get(n.id) || 0);
        nodes.push({
          id: n.id, label: n.name, kind: 'file', layer: n.layer, data: n, deg: deg,
          r: Math.min(15, 3.2 + Math.sqrt(deg) * 1.35)
        });
      });
      importEdges.forEach(function (e) {
        if (ids.has(e.src) && ids.has(e.dst)) {
          edges.push({ src: e.src, dst: e.dst, kind: 'imports' });
        }
      });
    } else if (state.mode === 'layer') {
      var layers = DATA.metrics.layers.filter(function (l) {
        return !state.hidden.has(l.layer);
      });
      var lids = new Set(layers.map(function (l) { return 'layer:' + l.layer; }));
      layers.forEach(function (l) {
        nodes.push({
          id: 'layer:' + l.layer, label: l.title || l.layer, kind: 'layer',
          layer: l.layer, data: l, deg: 0,
          r: 13 + Math.sqrt(l.files + l.symbols) * 0.75
        });
      });
      DATA.metrics.layer_edges.forEach(function (e) {
        if (lids.has('layer:' + e.src) && lids.has('layer:' + e.dst)) {
          edges.push({
            src: 'layer:' + e.src, dst: 'layer:' + e.dst,
            kind: 'imports', count: e.count
          });
        }
      });
    } else if (state.mode === 'symbol') {
      var syms = (symbolsByPath.get(state.focusPath) || []).slice();
      var sids = new Set(syms.map(function (n) { return n.id; }));
      syms.forEach(function (n) {
        var deg = (callIn.get(n.id) || 0) + (callOut.get(n.id) || 0);
        nodes.push({
          id: n.id, label: n.name, kind: n.kind, layer: n.layer, data: n, deg: deg,
          r: Math.min(13, 3.6 + Math.sqrt(deg) * 1.3)
        });
      });
      callEdges.concat(inheritEdges).forEach(function (e) {
        if (sids.has(e.src) && sids.has(e.dst)) {
          edges.push({ src: e.src, dst: e.dst, kind: e.kind });
        }
      });
    } else {
      var subs = DATA.subsystems;
      subs.nodes.forEach(function (n) {
        nodes.push({
          id: 'sub:' + n.id, label: n.title, kind: 'subsystem',
          layer: (n.layers && n.layers[0]) || '', data: n, deg: 0, r: 13
        });
      });
      subs.edges.forEach(function (e) {
        if (nodeMapHas(nodes, 'sub:' + e.src) && nodeMapHas(nodes, 'sub:' + e.dst)) {
          edges.push({
            src: 'sub:' + e.src, dst: 'sub:' + e.dst, kind: e.kind, note: e.note
          });
        }
      });
    }
    return { nodes: nodes, edges: edges };
  }

  function nodeMapHas(nodes, id) {
    for (var i = 0; i < nodes.length; i++) if (nodes[i].id === id) return true;
    return false;
  }

  function buildView() {
    var built = buildNodesAndEdges();
    sim.nodes = built.nodes;
    sim.edges = built.edges;
    sim.byId = new Map(sim.nodes.map(function (n) { return [n.id, n]; }));
    sim.adj = new Map();
    sim.nodes.forEach(function (n) { sim.adj.set(n.id, new Set()); });
    sim.edges.forEach(function (e) {
      if (sim.adj.has(e.src)) sim.adj.get(e.src).add(e.dst);
      if (sim.adj.has(e.dst)) sim.adj.get(e.dst).add(e.src);
    });
    if (state.selectedId && !sim.byId.has(state.selectedId)) state.selectedId = null;
    initPositions();
    sim.alpha = 1;
    // 先同步迭代一小段，避免首帧散乱；剩余交给动画。
    for (var i = 0; i < 90; i++) tick();
    fitToView();
    updateChrome();
    renderDetails();
    draw();
    startAnimation();
  }

  function initPositions() {
    var rnd = mulberry32(20261019);
    var tau = Math.PI * 2;
    if (state.mode === 'file' || state.mode === 'layer') {
      var groups = new Map();
      sim.nodes.forEach(function (n) {
        if (!groups.has(n.layer)) groups.set(n.layer, []);
        groups.get(n.layer).push(n);
      });
      var keys = Array.from(groups.keys());
      var R = keys.length > 1 ? 240 + Math.sqrt(sim.nodes.length) * 20 : 0;
      keys.forEach(function (key, i) {
        var ang = -Math.PI / 2 + (i * tau) / keys.length;
        var ax = Math.cos(ang) * R, ay = Math.sin(ang) * R;
        var arr = groups.get(key);
        var spread = Math.max(26, Math.sqrt(arr.length) * 24);
        arr.forEach(function (n) {
          var a = rnd() * tau, rad = Math.sqrt(rnd()) * spread;
          n.ax = ax; n.ay = ay;
          n.x = ax + Math.cos(a) * rad;
          n.y = ay + Math.sin(a) * rad;
          n.vx = 0; n.vy = 0;
        });
      });
    } else {
      var R2 = 130 + Math.sqrt(sim.nodes.length) * 34;
      sim.nodes.forEach(function (n, i) {
        var a = (i * tau) / Math.max(1, sim.nodes.length);
        n.ax = Math.cos(a) * R2;
        n.ay = Math.sin(a) * R2;
        n.x = n.ax + (rnd() - 0.5) * 40;
        n.y = n.ay + (rnd() - 0.5) * 40;
        n.vx = 0; n.vy = 0;
      });
    }
  }

  function tick() {
    var ns = sim.nodes, es = sim.edges;
    var mode = state.mode;
    var rep = mode === 'layer' ? 7000 : mode === 'subsystem' ? 4200 :
              mode === 'symbol' ? 1500 : 1900;
    var rest = mode === 'layer' ? 270 : mode === 'file' ? 92 : 104;
    var spring = mode === 'layer' ? 0.02 : 0.012;
    var i, j, a, b, dx, dy, d2, d, f, fx, fy;

    for (i = 0; i < ns.length; i++) {
      a = ns[i];
      for (j = i + 1; j < ns.length; j++) {
        b = ns[j];
        dx = b.x - a.x; dy = b.y - a.y;
        d2 = dx * dx + dy * dy + 0.01;
        d = Math.sqrt(d2);
        f = rep / d2;
        if (f > 3) f = 3;
        fx = (dx / d) * f; fy = (dy / d) * f;
        a.vx -= fx; a.vy -= fy;
        b.vx += fx; b.vy += fy;
      }
    }
    for (i = 0; i < es.length; i++) {
      a = sim.byId.get(es[i].src); b = sim.byId.get(es[i].dst);
      if (!a || !b) continue;
      dx = b.x - a.x; dy = b.y - a.y;
      d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      f = (d - rest) * spring;
      fx = (dx / d) * f; fy = (dy / d) * f;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }
    for (i = 0; i < ns.length; i++) {
      a = ns[i];
      a.vx += (a.ax - a.x) * 0.004;
      a.vy += (a.ay - a.y) * 0.004;
      a.vx *= 0.86; a.vy *= 0.86;
      var speed = Math.sqrt(a.vx * a.vx + a.vy * a.vy);
      if (speed > 5) { a.vx = (a.vx / speed) * 5; a.vy = (a.vy / speed) * 5; }
      a.x += a.vx; a.y += a.vy;
    }
    sim.alpha *= 0.988;
  }

  function startAnimation() {
    if (sim.running) return;
    sim.running = true;
    function frame() {
      if (sim.alpha > 0.03) {
        tick(); tick();
        draw();
        requestAnimationFrame(frame);
      } else {
        sim.running = false;
        draw();
      }
    }
    requestAnimationFrame(frame);
  }

  /* ---------------- 绘制 ---------------- */

  function resize() {
    var dpr = window.devicePixelRatio || 1;
    var rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }

  function fitToView() {
    if (!sim.nodes.length) return;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    sim.nodes.forEach(function (n) {
      minX = Math.min(minX, n.x - n.r); minY = Math.min(minY, n.y - n.r);
      maxX = Math.max(maxX, n.x + n.r); maxY = Math.max(maxY, n.y + n.r);
    });
    var rect = canvas.getBoundingClientRect();
    var w = rect.width || 900, h = rect.height || 600;
    var bw = Math.max(1, maxX - minX), bh = Math.max(1, maxY - minY);
    state.scale = Math.min(w / bw, h / bh) * 0.88;
    state.scale = Math.max(0.04, Math.min(2.5, state.scale));
    state.tx = w / 2 - ((minX + maxX) / 2) * state.scale;
    state.ty = h / 2 - ((minY + maxY) / 2) * state.scale;
  }

  function draw() {
    var rect = canvas.getBoundingClientRect();
    var w = rect.width, h = rect.height;
    var dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(state.tx, state.ty);
    ctx.scale(state.scale, state.scale);

    var selected = state.selectedId;
    var neighbours = selected && sim.adj.has(selected) ? sim.adj.get(selected) : null;
    var dimOthers = !!(selected && neighbours);
    var lineScale = 1 / state.scale;

    sim.edges.forEach(function (e) {
      var a = sim.byId.get(e.src), b = sim.byId.get(e.dst);
      if (!a || !b) return;
      var hot = selected && (e.src === selected || e.dst === selected);
      var faded = dimOthers && !hot;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      if (state.mode === 'layer') {
        ctx.lineWidth = Math.min(6, 0.8 + Math.log2((e.count || 1) + 1)) * lineScale;
        ctx.strokeStyle = hot ? 'rgba(240,180,90,.85)' : 'rgba(120,150,190,.34)';
      } else if (e.kind === 'inherits') {
        ctx.setLineDash([4 * lineScale, 4 * lineScale]);
        ctx.lineWidth = 1.1 * lineScale;
        ctx.strokeStyle = hot ? 'rgba(240,180,90,.9)' : 'rgba(190,140,220,.4)';
      } else if (e.kind === 'calls' || state.mode === 'subsystem') {
        ctx.lineWidth = hot ? 1.6 * lineScale : 0.8 * lineScale;
        ctx.strokeStyle = hot ? 'rgba(240,180,90,.85)' :
          (state.mode === 'subsystem' ? 'rgba(130,160,200,.36)' : 'rgba(110,140,180,.28)');
      } else {
        ctx.lineWidth = hot ? 1.7 * lineScale : 0.8 * lineScale;
        ctx.strokeStyle = hot ? 'rgba(240,180,90,.9)' : 'rgba(110,140,180,.27)';
      }
      if (faded) ctx.globalAlpha = 0.12;
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.setLineDash([]);
    });

    sim.nodes.forEach(function (n) {
      var isSel = n.id === selected;
      var isHover = n.id === state.hoverId;
      var isNb = neighbours && neighbours.has(n.id);
      var alpha = dimOthers && !isSel && !isNb ? 0.16 : 1;
      var color = colorFor(n.layer);
      ctx.globalAlpha = alpha;
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r + (isHover ? 1.5 : 0), 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = (isSel ? 2.6 : 1) * lineScale;
      ctx.strokeStyle = isSel ? '#f0b45a' :
        (isHover ? '#dce8f8' : 'rgba(10,15,24,.85)');
      ctx.stroke();
      if (n.kind === 'layer' || n.kind === 'subsystem') {
        ctx.beginPath();
        ctx.arc(n.x, n.y, Math.max(2, n.r * 0.28), 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(10,15,24,.35)';
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    });

    var showAllLabels = state.mode === 'layer' || state.mode === 'subsystem';
    ctx.textBaseline = 'middle';
    sim.nodes.forEach(function (n) {
      var isSel = n.id === selected;
      var isHover = n.id === state.hoverId;
      var isNb = neighbours && neighbours.has(n.id);
      if (dimOthers && !isSel && !isNb) return;
      var hub = state.mode === 'file' ? n.deg >= 14 : n.deg >= 4;
      var label = showAllLabels || isSel || isHover || hub ||
        (state.scale > 0.75 && (state.mode !== 'file' || n.deg >= 4));
      if (!label) return;
      var fs = 11 * lineScale;
      ctx.font = (isSel ? '600 ' : '') + fs + 'px -apple-system, "Segoe UI", ' +
        '"Microsoft YaHei", sans-serif';
      var text = n.label;
      if (text.length > 34) text = text.slice(0, 33) + '…';
      var tw = ctx.measureText(text).width;
      var ly = n.y + n.r + 4 * lineScale + fs / 2;
      ctx.fillStyle = 'rgba(8,12,20,.72)';
      ctx.fillRect(n.x - tw / 2 - 3 * lineScale, ly - fs / 2 - 1.5 * lineScale,
                   tw + 6 * lineScale, fs + 3 * lineScale);
      ctx.fillStyle = isSel ? '#ffd79a' : (state.mode === 'layer' ||
        state.mode === 'subsystem' ? '#dbe6f4' : '#c6d4e6');
      ctx.textAlign = 'center';
      ctx.fillText(text, n.x, ly);
    });

    if (state.mode === 'layer') {
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      sim.edges.forEach(function (e) {
        var a = sim.byId.get(e.src), b = sim.byId.get(e.dst);
        if (!a || !b) return;
        var fs = 10 * lineScale;
        ctx.font = fs + 'px -apple-system, "Segoe UI", sans-serif';
        ctx.fillStyle = 'rgba(190,208,230,.75)';
        ctx.fillStyle = 'rgba(8,12,20,.72)';
        var mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        var label = String(e.count || '');
        var tw = ctx.measureText(label).width;
        ctx.fillRect(mx - tw / 2 - 3 * lineScale, my - fs / 2 - 2 * lineScale,
                     tw + 6 * lineScale, fs + 4 * lineScale);
        ctx.fillStyle = 'rgba(190,208,230,.85)';
        ctx.fillText(label, mx, my);
      });
    }
    ctx.restore();
  }

  /* ---------------- 交互 ---------------- */

  function toWorld(sx, sy) {
    return { x: (sx - state.tx) / state.scale, y: (sy - state.ty) / state.scale };
  }
  function toScreen(x, y) {
    return { x: x * state.scale + state.tx, y: y * state.scale + state.ty };
  }
  function hitTest(sx, sy) {
    var p = toWorld(sx, sy);
    var best = null, bestD = Infinity;
    sim.nodes.forEach(function (n) {
      var dx = p.x - n.x, dy = p.y - n.y;
      var d = Math.sqrt(dx * dx + dy * dy);
      var r = Math.max(n.r, 6 / state.scale);
      if (d <= r && d < bestD) { best = n; bestD = d; }
    });
    return best;
  }

  var drag = null;
  var panning = false;
  var mouse = { x: 0, y: 0 };

  canvas.addEventListener('mousedown', function (ev) {
    var hit = hitTest(ev.offsetX, ev.offsetY);
    if (hit) {
      drag = { node: hit, moved: false, sx: ev.offsetX, sy: ev.offsetY };
      sim.alpha = Math.max(sim.alpha, 0.35);
    } else {
      panning = true;
      drag = { node: null, moved: false, sx: ev.offsetX, sy: ev.offsetY,
               tx: state.tx, ty: state.ty };
      canvas.classList.add('dragging');
    }
  });

  window.addEventListener('mousemove', function (ev) {
    var rect = canvas.getBoundingClientRect();
    var inside = ev.clientX >= rect.left && ev.clientX <= rect.right &&
                 ev.clientY >= rect.top && ev.clientY <= rect.bottom;
    if (!inside) {
      hideTooltip();
      if (!drag) return;
    }
    var sx = ev.clientX - rect.left, sy = ev.clientY - rect.top;
    mouse.x = sx; mouse.y = sy;

    if (drag) {
      var dx = sx - drag.sx, dy = sy - drag.sy;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      if (drag.node) {
        var p = toWorld(sx, sy);
        drag.node.x = p.x; drag.node.y = p.y;
        drag.node.vx = 0; drag.node.vy = 0;
        sim.alpha = Math.max(sim.alpha, 0.3);
        if (!sim.running) startAnimation();
        draw();
      } else if (panning) {
        state.tx = drag.tx + dx; state.ty = drag.ty + dy;
        draw();
      }
      return;
    }

    var hit = inside ? hitTest(sx, sy) : null;
    if (hit) {
      state.hoverId = hit.id;
      showTooltip(hit, sx, sy);
      canvas.style.cursor = 'pointer';
    } else {
      state.hoverId = null;
      hideTooltip();
      canvas.style.cursor = '';
    }
    draw();
  });

  window.addEventListener('mouseup', function (ev) {
    if (drag) {
      if (!drag.moved) {
        if (drag.node) selectNode(drag.node.id);
        else selectNode(null);
      }
      drag = null;
      panning = false;
      canvas.classList.remove('dragging');
    }
  });

  canvas.addEventListener('dblclick', function (ev) {
    var hit = hitTest(ev.offsetX, ev.offsetY);
    if (!hit) return;
    if (hit.kind === 'file') enterSymbols(hit.data.path, null);
  });

  canvas.addEventListener('wheel', function (ev) {
    ev.preventDefault();
    var rect = canvas.getBoundingClientRect();
    var sx = ev.clientX - rect.left, sy = ev.clientY - rect.top;
    var before = toWorld(sx, sy);
    var factor = Math.exp(-ev.deltaY * 0.0015);
    state.scale = Math.max(0.04, Math.min(6, state.scale * factor));
    state.tx = sx - before.x * state.scale;
    state.ty = sy - before.y * state.scale;
    draw();
  }, { passive: false });

  function showTooltip(node, sx, sy) {
    tooltip.classList.remove('hidden');
    tooltip.replaceChildren();
    var title = document.createElement('div');
    title.className = 'tt-title';
    title.textContent = node.label;
    var meta = document.createElement('div');
    meta.className = 'tt-meta';
    if (node.kind === 'file') meta.textContent = node.data.path;
    else if (node.kind === 'layer') {
      meta.textContent = node.data.files + ' 文件 · ' + node.data.symbols +
        ' 符号 · ' + node.data.loc + ' LOC';
    } else if (node.kind === 'subsystem') {
      meta.textContent = (node.data.layers || []).join(', ');
    } else {
      meta.textContent = node.data.path + ':' + (node.data.line || 0);
    }
    tooltip.appendChild(title);
    tooltip.appendChild(meta);
    var rect = $('stage').getBoundingClientRect();
    var x = sx + 16, y = sy + 16;
    if (x + 340 > rect.width) x = Math.max(8, sx - 350);
    if (y + 80 > rect.height) y = Math.max(8, sy - 70);
    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
  }
  function hideTooltip() { tooltip.classList.add('hidden'); }

  /* ---------------- 选择与详情 ---------------- */

  function selectNode(id) {
    state.selectedId = id;
    draw();
    renderDetails();
  }

  function centerNode(id) {
    var n = sim.byId.get(id);
    if (!n) return;
    var rect = canvas.getBoundingClientRect();
    state.tx = rect.width / 2 - n.x * state.scale;
    state.ty = rect.height / 2 - n.y * state.scale;
    draw();
  }

  function enterSymbols(path, symbolId) {
    state.focusPath = path;
    state.mode = 'symbol';
    state.selectedId = symbolId || null;
    buildView();
    if (symbolId) {
      setTimeout(function () { centerNode(symbolId); draw(); }, 60);
    } else if (sim.nodes.length) {
      var best = sim.nodes[0];
      sim.nodes.forEach(function (n) { if (n.deg > best.deg) best = n; });
      state.selectedId = best.id;
      renderDetails();
      draw();
    }
    showLoadingBriefly();
  }

  function backToFiles(path) {
    state.mode = 'file';
    state.focusPath = '';
    state.selectedId = path ? 'file:' + path : null;
    buildView();
    if (state.selectedId) setTimeout(function () { centerNode(state.selectedId); }, 60);
  }

  function h(tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  }

  function linkButton(text, onClick) {
    var b = h('button', 'link', text);
    b.type = 'button';
    b.addEventListener('click', onClick);
    return b;
  }

  function renderDetails() {
    var body = $('detailsBody');
    body.replaceChildren();
    var id = state.selectedId;
    if (!id || !sim.byId.has(id)) {
      body.appendChild(h('p', 'muted', '单击图中的节点查看详情。'));
      return;
    }
    var n = sim.byId.get(id);
    var d = n.data;

    var title = h('p');
    title.appendChild(h('b', '', n.label));
    title.appendChild(h('span', 'badge', kindLabel(n.kind)));
    body.appendChild(title);

    if (n.kind === 'file') {
      appendKv(body, [
        ['路径', d.path],
        ['分层', titleFor(d.layer)],
        ['LOC', d.loc],
        ['import', '入 ' + (importIn.get(d.id) || 0) + ' / 出 ' +
          (importOut.get(d.id) || 0)],
        ['符号', (symbolsByPath.get(d.path) || []).length]
      ]);
      var btn = h('button', 'toolbtn', '展开此文件的符号');
      btn.type = 'button';
      btn.style.marginTop = '6px';
      btn.addEventListener('click', function () { enterSymbols(d.path, null); });
      body.appendChild(btn);
      var callers = [];
      importEdges.forEach(function (e) { if (e.dst === d.id) callers.push(e.src); });
      appendLinks(body, '被这些文件导入', callers, 8, function (target) {
        backToFiles(nodeById.get(target) ? nodeById.get(target).path : '');
      });
    } else if (n.kind === 'layer') {
      appendKv(body, [
        ['分层 ID', d.layer],
        ['文件', d.files],
        ['LOC', d.loc],
        ['符号', d.symbols]
      ]);
    } else if (n.kind === 'subsystem') {
      appendKv(body, [
        ['职责', d.responsibility || ''],
        ['分层', (d.layers || []).join(', ')],
        ['路径', (d.paths || []).join(', ')]
      ]);
      var related = [];
      DATA.subsystems.edges.forEach(function (e) {
        if (e.src === d.id) related.push(['→ ' + e.kind + ' ' + subTitle(e.dst), e.note]);
        else if (e.dst === d.id) related.push(['← ' + e.kind + ' ' + subTitle(e.src), e.note]);
      });
      if (related.length) {
        body.appendChild(h('div', 'section-title', '关系'));
        var list = h('div', 'linklist');
        related.slice(0, 20).forEach(function (item) {
          var row = h('div', 'muted');
          row.textContent = item[0] + (item[1] ? ' · ' + item[1] : '');
          list.appendChild(row);
        });
        body.appendChild(list);
      }
    } else {
      appendKv(body, [
        ['类型', kindLabel(n.kind)],
        ['位置', d.path + ':' + (d.line || 0)],
        ['分层', titleFor(d.layer)],
        ['call', '入 ' + (callIn.get(d.id) || 0) + ' / 出 ' +
          (callOut.get(d.id) || 0)]
      ]);
      var callerIds = callersOf.get(d.id) || [];
      var calleeIds = calleesOf.get(d.id) || [];
      if (n.data.path !== state.focusPath) {
        var open = h('button', 'toolbtn', '打开所在文件视图');
        open.type = 'button';
        open.style.marginTop = '6px';
        open.addEventListener('click', function () {
          enterSymbols(n.data.path, n.id);
        });
        body.appendChild(open);
      }
      appendLinks(body, '被调用（调用方）', callerIds, 10, function (target) {
        navigateToSymbol(target, n.data.path);
      });
      appendLinks(body, '调用了', calleeIds, 10, function (target) {
        navigateToSymbol(target, n.data.path);
      });
    }
  }

  function navigateToSymbol(targetId, currentPath) {
    var target = nodeById.get(targetId);
    if (!target) return;
    if (target.path === currentPath && sim.byId.has(targetId)) {
      selectNode(targetId);
      centerNode(targetId);
    } else {
      enterSymbols(target.path, targetId);
    }
  }

  function appendKv(body, rows) {
    var kv = h('div', 'kv');
    rows.forEach(function (row) {
      kv.appendChild(h('div', 'k', row[0]));
      kv.appendChild(h('div', 'v', row[1]));
    });
    body.appendChild(kv);
  }

  function appendLinks(body, title, ids, limit, onClick) {
    if (!ids.length) return;
    body.appendChild(h('div', 'section-title', title + '（' + ids.length + '）'));
    var list = h('div', 'linklist');
    var seen = new Set();
    ids.slice(0, limit).forEach(function (id) {
      if (seen.has(id)) return;
      seen.add(id);
      var node = nodeById.get(id);
      var label = node ? node.name + (node.kind === 'file' ? '' : ' · ' +
        node.path.split('/').pop()) : id;
      list.appendChild(linkButton(label, function () { onClick(id); }));
    });
    body.appendChild(list);
  }

  function kindLabel(kind) {
    return ({ file: '文件', class: '类', function: '函数', method: '方法',
              layer: '分层', subsystem: '子系统' })[kind] || kind;
  }
  function subTitle(id) {
    var found = DATA.subsystems.nodes.filter(function (n) { return n.id === id; })[0];
    return found ? found.title : id;
  }

  /* ---------------- 侧栏与工具栏 ---------------- */

  function updateChrome() {
    $('generated').textContent = '生成于 ' + (DATA.generated_at || '未知') +
      ' · 快照 ' + (DATA.root || '');
    var s = DATA.metrics.summary || {};
    var stats = $('stats');
    stats.replaceChildren();
    [
      ['文件', s.files], ['符号', s.symbols], ['边', s.edges],
      ['import', s.import_edges], ['call', s.call_edges],
      ['分层', s.layers], ['循环', s.cycles]
    ].forEach(function (item) {
      var chip = h('span', 'chip');
      chip.appendChild(h('b', '', item[1]));
      chip.appendChild(document.createTextNode(' ' + item[0]));
      stats.appendChild(chip);
    });

    var tabs = $('tabs').querySelectorAll('button');
    tabs.forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-mode') === state.mode);
      b.classList.toggle('hidden', state.mode === 'symbol' &&
        b.getAttribute('data-mode') !== 'file');
    });
    $('symbolBar').classList.toggle('hidden', state.mode !== 'symbol');
    $('focusPath').textContent = state.mode === 'symbol' ? state.focusPath : '';

    renderLegend();
  }

  function renderLegend() {
    var legend = $('legend');
    legend.replaceChildren();
    DATA.metrics.layers.forEach(function (l) {
      var row = h('div', 'legend-row' + (state.hidden.has(l.layer) ? ' off' : ''));
      var sw = h('span', 'swatch');
      sw.style.background = colorFor(l.layer);
      row.appendChild(sw);
      row.appendChild(h('span', 'legend-name', l.title || l.layer));
      row.appendChild(h('span', 'legend-count',
        l.files + 'f / ' + l.symbols + 's'));
      row.title = l.layer + ' · ' + l.loc + ' LOC';
      row.addEventListener('click', function () {
        if (state.hidden.has(l.layer)) state.hidden.delete(l.layer);
        else state.hidden.add(l.layer);
        buildView();
      });
      legend.appendChild(row);
    });
  }

  function showLoadingBriefly() {
    loading.style.opacity = '1';
    loading.classList.remove('hidden');
    setTimeout(function () {
      loading.style.opacity = '0';
      setTimeout(function () { loading.classList.add('hidden'); }, 200);
    }, 40);
  }

  /* ---------------- 搜索 ---------------- */

  var searchIndex = DATA.nodes.map(function (n) {
    return { id: n.id, label: n.name, kind: n.kind, path: n.path, layer: n.layer };
  }).concat(DATA.subsystems.nodes.map(function (n) {
    return {
      id: 'sub:' + n.id, label: n.title, kind: 'subsystem', path: '',
      layer: (n.layers && n.layers[0]) || ''
    };
  }));

  var activeResult = -1;
  var currentResults = [];

  function runSearch(query) {
    var q = query.trim().toLowerCase();
    var box = $('searchResults');
    box.replaceChildren();
    currentResults = [];
    activeResult = -1;
    if (!q) return;
    var terms = q.split(/\s+/);
    currentResults = searchIndex.filter(function (item) {
      var hay = (item.label + ' ' + item.path + ' ' + item.layer).toLowerCase();
      return terms.every(function (t) { return hay.indexOf(t) >= 0; });
    }).slice(0, 24);
    currentResults.forEach(function (item, i) {
      var b = h('button', 'result');
      b.type = 'button';
      b.appendChild(h('span', 'tag', kindLabel(item.kind)));
      b.appendChild(document.createTextNode(item.label));
      if (item.path && item.kind !== 'file') {
        b.appendChild(h('span', 'muted', '  ' + item.path));
      }
      b.addEventListener('click', function () {
        $('search').value = item.label;
        box.replaceChildren();
        navigateToResult(item);
      });
      box.appendChild(b);
    });
    if (!currentResults.length) box.appendChild(h('div', 'empty', '无匹配结果'));
  }

  function navigateToResult(item) {
    if (item.kind === 'subsystem') {
      state.mode = 'subsystem';
      state.selectedId = item.id;
      buildView();
      setTimeout(function () { centerNode(item.id); draw(); }, 60);
    } else if (item.kind === 'file') {
      state.mode = 'file';
      state.selectedId = item.id;
      buildView();
      setTimeout(function () { centerNode(item.id); draw(); }, 60);
    } else {
      enterSymbols(item.path, item.id);
    }
  }

  $('search').addEventListener('input', function (ev) {
    runSearch(ev.target.value);
  });
  $('search').addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter') {
      var item = currentResults[Math.max(0, activeResult)];
      if (item) {
        $('searchResults').replaceChildren();
        navigateToResult(item);
      }
    } else if (ev.key === 'Escape') {
      $('search').blur();
    }
  });

  document.addEventListener('keydown', function (ev) {
    if (ev.key === '/' && document.activeElement !== $('search')) {
      ev.preventDefault();
      $('search').focus();
    } else if (ev.key === 'Escape' && document.activeElement !== $('search')) {
      selectNode(null);
    }
  });

  /* ---------------- 工具栏事件 ---------------- */

  $('tabs').addEventListener('click', function (ev) {
    var btn = ev.target.closest('button[data-mode]');
    if (!btn) return;
    var mode = btn.getAttribute('data-mode');
    if (mode === state.mode && mode !== 'symbol') return;
    state.mode = mode;
    state.focusPath = '';
    state.selectedId = null;
    buildView();
  });
  $('backBtn').addEventListener('click', function () {
    state.mode = 'file';
    state.focusPath = '';
    state.selectedId = null;
    buildView();
  });

  window.addEventListener('resize', resize);

  /* ---------------- 启动 ---------------- */

  loading.classList.add('hidden');
  resize();
  buildView();
})();
</script>
</body>
</html>
"""
