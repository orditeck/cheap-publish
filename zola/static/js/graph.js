// Query dark mode setting
function isDark() {
  return (
    localStorage.getItem("theme") === "dark" ||
    (!localStorage.getItem("theme") &&
      window.matchMedia("(prefers-color-scheme: dark)").matches)
  );
}

// Get URL of current page and also current node
var curr_url = decodeURI(window.location.href.replace(location.origin, ""));
if (curr_url.endsWith("/")) {
  curr_url = curr_url.slice(0, -1);
}

// Get graph element
var container = document.getElementById("graph");
var curr_node = null;

// Parse nodes and edges
try {
  curr_node = graph_data.nodes.find(
    (node) => decodeURI(node.url).toLowerCase() == curr_url.toLowerCase()
  );
} catch (error) { }

var nodes = null;
var edges = new vis.DataSet(graph_data.edges);

if (curr_node && graph_is_local) {
  // Get nodes connected to current
  var connected_nodes = graph_data.edges
    .filter((edge) => edge.from == curr_node.id || edge.to == curr_node.id)
    .map((edge) => {
      if (edge.from == curr_node.id) {
        return edge.to;
      }
      return edge.from;
    });

  nodes = new vis.DataSet(
    graph_data.nodes.filter(
      (node) =>
        node.id == curr_node.id || connected_nodes.includes(node.id)
    )
  );
} else {
  nodes = new vis.DataSet(graph_data.nodes);
}

// Get nodes and edges from generated javascript
var max_node_val = Math.max(...nodes.map((node) => node.value));

// Highlight current node and set to center
if (curr_node) {
  nodes.update({
    id: curr_node.id,
    value: Math.max(4, max_node_val * 2.5),
    shape: "dot",
    color: "#a6a7ed",
    font: {
      strokeWidth: 1,
    },
    x: 0,
    y: 0,
    fixed: { x: true, y: true }
  });
}

// Construct graph
var options = ___GRAPH_OPTIONS___;

var graph = new vis.Network(
  container,
  {
    nodes,
    edges,
  },
  options
);

// Clickable URL
graph.on("selectNode", function (params) {
  if (params.nodes.length === 1) {
    var node = nodes.get(params.nodes[0]);
    window.open(node.url, "_self");
  }
});
