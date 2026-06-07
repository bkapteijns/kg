"""Render a graph snapshot to a single self-contained interactive HTML file.

No Python dependencies and no build step: the graph data is embedded as JSON
and a small D3 force-directed renderer (loaded from a CDN) draws it. Edge width
scales with weight, edge color encodes its source, node radius scales with
degree. Open the file in any browser; drag nodes, hover for paths.
"""
from __future__ import annotations

import json

from kg.graph import Graph

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>kg graph</title>
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<style>
  html, body {{ margin: 0; height: 100%; font: 13px system-ui, sans-serif; background: #14161a; color: #e6e6e6; }}
  #chart {{ width: 100vw; height: 100vh; }}
  .link {{ stroke-opacity: 0.5; }}
  .node circle {{ stroke: #14161a; stroke-width: 1.5px; cursor: grab; }}
  .node text {{ fill: #cfd3da; pointer-events: none; font-size: 11px; }}
  #legend {{ position: fixed; top: 12px; left: 12px; background: rgba(20,22,26,0.85);
             padding: 10px 12px; border-radius: 6px; line-height: 1.6; }}
  #legend .row {{ display: flex; align-items: center; gap: 6px; }}
  #legend .swatch {{ width: 12px; height: 12px; border-radius: 2px; }}
  #legend h1 {{ font-size: 13px; margin: 0 0 6px; font-weight: 600; }}
  #legend .muted {{ color: #8b909a; font-size: 11px; }}
</style>
</head>
<body>
<div id="legend"></div>
<svg id="chart"></svg>
<script>
const DATA = {data};

const svg = d3.select("#chart");
const width = window.innerWidth, height = window.innerHeight;
svg.attr("viewBox", [0, 0, width, height]);

const sources = Array.from(new Set(DATA.edges.map(e => e.source))).sort();
const color = d3.scaleOrdinal(sources, d3.schemeTableau10);

const maxW = d3.max(DATA.edges, e => e.weight) || 1;
const widthScale = d3.scaleSqrt([0, maxW], [1, 8]);
const maxDeg = d3.max(DATA.nodes, n => n.degree) || 1;
const radius = d3.scaleSqrt([0, maxDeg], [4, 22]);

// d3 mutates these objects in place during simulation.
const nodes = DATA.nodes.map(n => ({{...n}}));
const byId = new Map(nodes.map(n => [n.id, n]));
const links = DATA.edges.map(e => ({{source: e.src, target: e.dst, weight: e.weight, src: e.source}}));

const sim = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(links).id(d => d.id).distance(90))
  .force("charge", d3.forceManyBody().strength(-300))
  .force("center", d3.forceCenter(width / 2, height / 2))
  .force("collide", d3.forceCollide().radius(d => radius(d.degree) + 6));

const container = svg.append("g");
svg.call(d3.zoom().scaleExtent([0.2, 5]).on("zoom", e => container.attr("transform", e.transform)));

const link = container.append("g").selectAll("line")
  .data(links).join("line")
  .attr("class", "link")
  .attr("stroke", d => color(d.src))
  .attr("stroke-width", d => widthScale(d.weight))
  .append("title").text(d => `${{d.src}} · weight ${{d.weight}}`);

const node = container.append("g").selectAll("g")
  .data(nodes).join("g").attr("class", "node")
  .call(d3.drag()
    .on("start", (e, d) => {{ if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }})
    .on("drag", (e, d) => {{ d.fx = e.x; d.fy = e.y; }})
    .on("end", (e, d) => {{ if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }}));

node.append("circle")
  .attr("r", d => d.seed ? radius(d.degree) + 4 : radius(d.degree))
  .attr("fill", d => d.seed ? "#f2a93b" : "#5b8def")
  .attr("stroke", d => d.seed ? "#fff" : "#14161a")
  .attr("stroke-width", d => d.seed ? 2.5 : 1.5);
node.append("title").text(d => `${{d.path}} · degree ${{d.degree}}`);
node.append("text")
  .attr("x", d => radius(d.degree) + 4).attr("y", 4)
  .text(d => d.path.split("/").pop());

sim.on("tick", () => {{
  container.selectAll("line")
    .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
    .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
  node.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
}});

const legend = d3.select("#legend");
legend.append("h1").text("kg graph");
if (DATA.seed) legend.append("div").attr("class", "muted").text(`seed: ${{DATA.seed}}`);
legend.append("div").attr("class", "muted")
  .text(`${{nodes.length}} files · ${{links.length}} edges`);
sources.forEach(s => {{
  const row = legend.append("div").attr("class", "row");
  row.append("span").attr("class", "swatch").style("background", color(s));
  row.append("span").text(s);
}});
</script>
</body>
</html>
"""


def render_html(
    graph: Graph,
    sources: list[str] | None = None,
    seed: str | None = None,
    hops: int = 1,
) -> str:
    """Return a complete HTML document visualizing the graph.

    If `seed` is given, only the ego graph within `hops` edges is rendered."""
    data = graph.viz_data(sources=sources, seed=seed, hops=hops)
    return _TEMPLATE.format(data=json.dumps(data))
