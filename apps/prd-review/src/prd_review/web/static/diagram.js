if (window.mermaid) {
  window.mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    suppressErrorRendering: true,
    theme: "neutral",
  });
  function drawVisibleDiagrams() {
    const nodes = [...document.querySelectorAll("pre.mermaid:not([data-requested])")]
      .filter((node) => !node.closest("details:not([open])"));
    if (!nodes.length) return;
    const sources = nodes.map((node) => node.textContent);
    nodes.forEach((node) => { node.dataset.requested = "true"; });
    window.mermaid.run({ nodes }).catch(() => {
      nodes.forEach((node, index) => { node.textContent = sources[index]; });
    });
  }
  document.querySelectorAll("details.module").forEach((item) => {
    item.addEventListener("toggle", drawVisibleDiagrams);
  });
  drawVisibleDiagrams();
}
