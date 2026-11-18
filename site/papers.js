"use strict";

const form = document.querySelector("#paper-controls");
const levels = ["#level1", "#level2", "#level3"].map(id => form.querySelector(id));
const query = form.querySelector("#query");
const info = document.querySelector("#paper-count");
const prev = document.querySelector("#prev");
const next = document.querySelector("#next");
const memo = new Map();
let requestNumber = 0;

function readFile(file) {
  if (!memo.has(file)) {
    memo.set(file, fetch(file, {cache: "no-cache"}).then(response => {
      if (!response.ok) throw new Error("加载失败");
      return response.json();
    }).catch(error => { memo.delete(file); throw error; }));
  }
  return memo.get(file);
}

function navigate(topic, text, page = 1) {
  const url = new URL(location.href);
  url.search = "";
  if (topic) url.searchParams.set("topic", topic);
  if (text) url.searchParams.set("q", text);
  if (page > 1) url.searchParams.set("page", page);
  history.pushState(null, "", url);
  render();
}

function updateFilters(tree, path) {
  const selected = [];
  const labels = ["全部方向", "全部领域", "全部主题"];
  levels.forEach((select, level) => {
    select.replaceChildren(new Option(labels[level], ""));
    for (const [key, node] of Object.entries(tree)) select.add(new Option(node.label, key));
    select.disabled = !Object.keys(tree).length;
    if (Object.hasOwn(tree, path[level])) {
      select.value = path[level];
      selected.push(path[level]);
      tree = tree[path[level]].children;
    } else {
      tree = {};
    }
  });
  return selected.join("/");
}

async function render() {
  const ticket = ++requestNumber;
  const params = new URLSearchParams(location.search);
  const text = (params.get("q") || "").trim();
  query.value = text;
  try {
    const data = await readFile("index-data.json");
    if (ticket !== requestNumber) return;
    const topic = updateFilters(data.taxonomy, (params.get("topic") || "").split("/"));
    const codes = new Set(data.paths.flatMap((path, i) =>
      !topic || path === topic || path.startsWith(topic + "/") ? [i] : []));
    let hits;
    if (text) {
      info.textContent = "正在检索标题和摘要…";
      const index = await openSearchIndex();
      hits = await scoreIndex(index, expandedSearchWords(text, index.expansions));
    }
    const matched = [];
    data.rows.forEach(([id, paths, batch], rank) => {
      if ((!topic || paths.some(code => codes.has(code))) && (!hits || hits.has("arxiv:" + id))) {
        matched.push({rank, batch, id});
      }
    });
    const pages = Math.max(1, Math.ceil(matched.length / data.pageSize));
    const requested = Number(params.get("page") || 1);
    const page = Number.isSafeInteger(requested) ? Math.max(1, Math.min(pages, requested)) : 1;
    const slice = matched.slice((page - 1) * data.pageSize, page * data.pageSize);
    const cards = await Promise.all(slice.map(async ({rank, id}) => {
      const file = await readFile("entries/" + Math.floor(rank / data.chunkSize) + ".json");
      const html = file[rank % data.chunkSize];
      if (!html?.includes('data-id="' + id + '"')) throw new Error("索引已更新");
      return html;
    }));
    if (ticket !== requestNumber) return;
    info.textContent = "共 " + matched.length.toLocaleString("zh-CN") + " 篇";
    let previous;
    const blocks = [];
    slice.forEach(({batch}, i) => {
      if (batch !== previous) blocks.push("<h3>" + (batch === 0 ? "最近 10 天" : batch*10 + "–" + (batch*10+9) + " 天前") + "</h3>");
      previous = batch;
      blocks.push(cards[i]);
    });
    document.querySelector("#paper-rows").innerHTML = blocks.join("") || "<p>没有匹配的论文。可以缩短关键词或放宽目录范围。</p>";
    document.querySelector("#page").textContent = page + " / " + pages;
    prev.disabled = page === 1;
    next.disabled = page === pages;
    for (const [button, step] of [[prev, -1], [next, 1]]) button.onclick = () => {
      navigate(topic, text, page + step);
      info.scrollIntoView({block: "start"});
    };
  } catch (error) {
    if (ticket !== requestNumber) return;
    info.textContent = "论文索引暂时无法读取，请刷新页面重试。";
    prev.disabled = next.disabled = true;
  }
}

levels.forEach((select, index) => select.addEventListener("change", () => {
  const path = levels.slice(0, index + 1).map(item => item.value).filter(Boolean).join("/");
  navigate(path, query.value.trim());
}));
form.addEventListener("submit", event => {
  event.preventDefault();
  navigate(levels.map(item => item.value).filter(Boolean).join("/"), query.value.trim());
});
window.addEventListener("popstate", render);
render();
