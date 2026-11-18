const modules = {
  arxiv: { name: 'arXiv 论文', description: '按三级研究方向浏览。最近十天优先，每批按 Hugging Face 推荐票排序。', unit: '篇', file: 'papers-reader.json', entries: [] },
  x: { name: 'X 与小红书', description: '关注账号发布的技术长帖与笔记。', unit: '条', file: 'x.json', entries: [] },
  wechat: { name: '微信公众号文章', description: '中文论文解读与工程实践。', unit: '篇', file: 'wechat.json', entries: [] },
  blogs: { name: '技术博客', description: '研究者和工程团队亲笔写下的方法、实验与实践。', unit: '篇', file: 'blogs.json', entries: [] },
  events: { name: 'AI 事件', description: '可核实的模型、产品与开源发布。', unit: '条', file: 'events.json', entries: [] },
  podcasts: { name: '播客', description: 'AI 研究与工程的完整视频对谈。', unit: '期', file: 'podcasts.json', entries: [] }
};

const icons = {
  arxiv: '<path d="M12 5.5C8.8 3.3 5.3 3 2 4.3v14c3.3-1.3 6.8-1 10 1.2 3.2-2.2 6.7-2.5 10-1.2v-14c-3.3-1.3-6.8-1-10 1.2v14"/>',
  x: '<path d="M3 4.5h18v12H9l-5.5 4v-4H3zM7 9h10M7 12h7"/>',
  wechat: '<path d="M5 2.5h10l4 4v15H5zM14 2.5v5h5M8 11h8M8 14.5h8M8 18h5"/>',
  blogs: '<path d="m4 16 11-11 4 4L8 20l-5 1zM13 7l4 4M12 21h9"/>',
  events: '<path d="M12 2.5 22 8 12 13.5 2 8zM2 12l10 5.5L22 12M2 16l10 5.5L22 16"/>',
  podcasts: '<path d="M8.5 6a3.5 3.5 0 0 1 7 0v5a3.5 3.5 0 0 1-7 0zM5 11a7 7 0 0 0 14 0M12 18v4M8.5 22h7"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  arrow: '<path d="M3 12h17m-6-6 6 6-6 6"/>',
  external: '<path d="M6 18 18 6M8 6h10v10"/>',
  updates: '<path d="M9 3c0 5-3 8-8 8 5 0 8 3 8 8 0-5 3-8 8-8-5 0-8-3-8-8Zm10 10c0 2-1.5 3.5-3.5 3.5 2 0 3.5 1.5 3.5 3.5 0-2 1.5-3.5 3.5-3.5-2 0-3.5-1.5-3.5-3.5Z"/>'
};
function icon(name) {
  return `<svg viewBox="0 0 24 24" class="icon" aria-hidden="true">${icons[name]}</svg>`;
}

const headerArt = {
  day: './assets/spectral-fold-day.webp',
  night: './assets/spectral-fold-night.webp'
};
const zone = 'Asia/Shanghai';
const dateFormat = new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' });
function retentionWindow(now = new Date()) {
  const parts = Object.fromEntries(dateFormat.formatToParts(now).map(part => [part.type, part.value]));
  const end = [parts.year, parts.month, parts.day].join('-');
  const previous = new Date(Date.UTC(+parts.year, +parts.month - 4, 1));
  const lastDay = new Date(Date.UTC(previous.getUTCFullYear(), previous.getUTCMonth() + 1, 0)).getUTCDate();
  previous.setUTCDate(Math.min(+parts.day, lastDay));
  return { start: previous.toISOString().slice(0, 10), end };
}
function retained(entry, dateRange) {
  return typeof entry.published === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(entry.published) &&
    entry.published >= dateRange.start && entry.published <= dateRange.end;
}

const saveKey = 'aifrontier-reading';
function collectionOrder(value) {
  const parts = typeof value === 'string' && /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(?:Z|\+00:00)$/.exec(value);
  return parts && Number.isFinite(Date.parse(value)) ? parts[1] + '.' + (parts[2] ?? '').padEnd(9, '0') : '';
}
function readState() {
  const seen = {};
  let theme = 'night';
  try {
    const saved = JSON.parse(localStorage.getItem(saveKey));
    theme = saved?.theme === 'day' ? 'day' : 'night';
    const range = retentionWindow();
    for (const key of Object.keys(modules)) {
      const mark = saved?.seen?.[key];
      if (collectionOrder(mark)) seen[key] = {cutoff: mark, items: {}};
      if (mark?.items && !Array.isArray(mark.items) && typeof mark.items === 'object') {
        const items = Object.fromEntries(Object.entries(mark.items).filter(([, published]) => retained({published}, range)));
        seen[key] = {cutoff: collectionOrder(mark.cutoff) ? mark.cutoff : '', items};
      }
    }
  } catch {
    // Keep reading state in memory when browser storage is unavailable.
  }
  return {theme, seen};
}
const state = readState();
function persist() {
  const stored = readState().seen;
  for (const [key, mark] of Object.entries(stored)) {
    const own = state.seen[key];
    state.seen[key] = own ? {
      cutoff: collectionOrder(own.cutoff) >= collectionOrder(mark.cutoff) ? own.cutoff : mark.cutoff,
      items: {...mark.items, ...own.items},
    } : mark;
  }
  try { localStorage.setItem(saveKey, JSON.stringify(state)); } catch { /* in-memory state */ }
}
const page = document.querySelector('#page');
const nav = document.querySelector('#site-nav');
const sheet = document.querySelector('#updates');
const status = document.querySelector('#status');
const menuButton = document.querySelector('.menu-button');
let currentModule;
let automaticChecks;
let updatesExpanded = false;
let sheetTrigger;
let batch = [];

function applyTheme() {
  document.documentElement.dataset.theme = state.theme;
  const illustration = page.querySelector('.feed-visual img');
  if (illustration) illustration.src = headerArt[state.theme];
  for (const button of document.querySelectorAll('.theme-switch button')) {
    button.setAttribute('aria-pressed', String(state.theme === button.dataset.theme));
  }
}

const route = key => '#/' + key;
const legacyTaxonomy = {llm: {label: '大语言模型', children: {
  evaluation: {label: '通用大模型评测', children: {general: {label: '综合能力', children: {}}}}
}}};
let taxonomy = legacyTaxonomy;
const cardPageSize = 30;
const cardPages = {};
let homeItems = [];
let homeFeed = [];
let paperHeat;
let activeSearch;
let paperScope = [];
const channelNames = {arxiv: '论文', x: '社交', wechat: '公众号', blogs: '技术博客', events: 'AI 事件', podcasts: '播客'};
const blogKinds = {author: {label: '个人作者'}, team: {label: '研究与工程团队'}};
const number = new Intl.NumberFormat('zh-CN');

function entriesFor(key, range = retentionWindow()) {
  return modules[key].entries.filter(entry => retained(entry, range));
}

function currentVotes() {
  const checked = Date.parse(paperHeat?.checkedAt);
  const elapsed = Date.now() - checked;
  return Number.isFinite(elapsed) && elapsed >= 0 && elapsed <= 48 * 3_600_000 ? paperHeat?.votes : null;
}

function orderPaperSearch(items, today) {
  const votesById = currentVotes();
  const todayMs = Date.parse(today);
  const papers = [];
  for (const item of items) {
    if (item.key !== 'arxiv') continue;
    const age = (todayMs - Date.parse(item.entry.published)) / 86_400_000;
    const batch = Math.floor(age / 10);
    const count = votesById?.[item.entry.id];
    const votes = Number.isSafeInteger(count) && count >= 0 ? count : undefined;
    papers.push({...item, batch, votes});
  }
  // Stable ties keep the relevance order. Other modules keep their original slots.
  papers.sort((a, b) => a.batch - b.batch || (b.votes ?? -1) - (a.votes ?? -1) ||
    b.entry.published.localeCompare(a.entry.published) || a.entry.id.localeCompare(b.entry.id));
  let index = 0;
  return items.map(item => item.key === 'arxiv' ? papers[index++] : item);
}

function trendingPapers(today) {
  const votesById = currentVotes();
  if (!votesById) return [];
  const todayMs = Date.parse(today);
  const ranked = [];
  for (const entry of modules.arxiv.entries) {
    const votes = votesById[entry.id];
    if (!Number.isSafeInteger(votes) || votes <= 0) continue;
    const age = (todayMs - Date.parse(entry.published)) / 86_400_000;
    if (!Number.isInteger(age) || age < 0 || age >= 10) continue;
    ranked.push({key: 'arxiv', entry, votes, score: votes});
  }
  return ranked.sort((a, b) => b.score - a.score || b.entry.published.localeCompare(a.entry.published) ||
    a.entry.id.localeCompare(b.entry.id));
}
function newEntries(key) {
  const mark = state.seen[key];
  if (!mark) return [];
  const cutoff = collectionOrder(mark.cutoff);
  return entriesFor(key).filter(entry => !Object.hasOwn(mark.items, entry.id) && entry.collection > cutoff)
    .sort((left, right) => right.collection.localeCompare(left.collection));
}

function acknowledge(key, entries) {
  const mark = state.seen[key] ?? {cutoff: '', items: {}};
  mark.items = {...mark.items, ...Object.fromEntries(entries.map(entry => [entry.id, entry.published]))};
  state.seen[key] = mark;
}

function escapeHtml(input) {
  return String(input).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}
function safeUrl(value) {
  if (typeof value !== 'string') return;
  try {
    const url = new URL(value);
    if (url.protocol === 'https:') return url;
  } catch {
    // Malformed URLs are ignored.
  }
}
function comparePublication(left, right) {
  return right.published.localeCompare(left.published) || right.collection.localeCompare(left.collection);
}
function renderNav(key) {
  const link = (path, label, current) => '<a href="' + path + '"' + (current ? ' aria-current="page"' : '') + '>' + label + '</a>';
  nav.innerHTML = link('#/', '首页', !key) + Object.entries(modules).map(([id, module]) => link(route(id), module.name, id === key)).join('');
}

function dateLabel(value) {
  if (typeof value !== 'string') return '未知';
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? dateFormat.format(timestamp) : '未知';
}

function durationLabel(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds / 60) % 60;
  return [hours && hours + ' 小时', minutes && minutes + ' 分钟', seconds < 60 && seconds + ' 秒']
    .filter(Boolean).join(' ');
}

function namedList(values) {
  return Array.isArray(values) ? values.filter(value => typeof value === 'string' && value).join('、') : '';
}

function cardDetails(key, entry) {
  if (key === 'events') {
    return '<div class="source-details"><span>官宣 ' + dateLabel(entry.announcedAt) +
      '</span><span>开放 ' + dateLabel(entry.availableAt) + '</span></div>';
  }
  if (key !== 'podcasts') return '';
  const guests = namedList(entry.guests);
  const topics = namedList(entry.topics);
  const duration = durationLabel(entry.durationSeconds);
  const details = [
    typeof entry.show === 'string' && entry.show,
    guests && '嘉宾 ' + guests,
    duration,
    topics && '主题 ' + topics,
  ].filter(Boolean);
  return details.length
    ? '<div class="source-details">' + details.map(detail => '<span>' + escapeHtml(detail) + '</span>').join('') + '</div>'
    : '';
}

function evidenceIdentity(url) {
  const host = url.port && url.port !== '80' ? url.host : url.hostname.replace(/^www\./, '');
  const path = url.pathname.replace(/\/+$/, '') || '/';
  const query = [...url.searchParams].filter(([name]) => !/^(utm_.*|trk|_bhlid|fbclid|gclid)$/i.test(name));
  query.sort(([left, a], [right, b]) => left.localeCompare(right) || a.localeCompare(b));
  return host + path + '?' + new URLSearchParams(query) + url.hash;
}

function evidenceLinks(links, original, originalSource) {
  if (!Array.isArray(links)) return '';
  const primary = evidenceIdentity(original);
  const seen = new Set([primary]);
  return links.map(link => {
    const url = safeUrl(link?.url);
    if (!url) return '';
    let timestamp = '';
    if (Number.isInteger(link.offsetSeconds) && link.offsetSeconds > 0) {
      const seconds = link.offsetSeconds;
      if (/(^|\.)(youtube\.com|youtu\.be|bilibili\.com)$/.test(url.hostname)) {
        url.searchParams.set('t', String(seconds));
      }
      timestamp = ' · ' + Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
    }
    const key = evidenceIdentity(url);
    const names = Array.isArray(link.sources)
      ? [...new Set(link.sources.filter(name => typeof name === 'string' && name.trim()))] : [];
    if (seen.has(key)) {
      const alternates = key === primary ? names.filter(name => name !== originalSource) : [];
      return alternates.length ? '<small>其他来源 · ' + escapeHtml(alternates.join('、')) + '</small>' : '';
    }
    seen.add(key);
    const title = typeof link.title === 'string' ? link.title : '';
    const source = names.join('、') || (typeof link.source === 'string' ? link.source : '');
    const published = typeof link.published === 'string' ? link.published : '';
    const label = title || source || '查看来源';
    const meta = [title && source, published].filter(Boolean).join(' · ');
    return '<a href="' + escapeHtml(url.href) + '" target="_blank" rel="noopener noreferrer">' +
      escapeHtml(label + timestamp) + ' ' + icon('external') + (meta ? '<small>' + escapeHtml(meta) + '</small>' : '') + '</a>';
  }).join('');
}

const abstractRequests = new Map();

function readAbstractBatch(file) {
  if (!abstractRequests.has(file)) {
    const request = fetch('./' + file, {cache: 'no-cache'}).then(response => {
      if (!response.ok) throw new Error('Could not read paper abstracts');
      return response.json();
    }).catch(error => {
      abstractRequests.delete(file);
      throw error;
    });
    abstractRequests.set(file, request);
  }
  return abstractRequests.get(file);
}

function coverSentence(text) {
  const segments = typeof Intl.Segmenter === 'function'
    ? [...new Intl.Segmenter('en', {granularity: 'sentence'}).segment(text)].map(part => part.segment.trim())
    : text.split(/(?<=[.!?。！？])\s+/);
  const sentences = [];
  for (const segment of segments) {
    const previous = sentences.at(-1) ?? '';
    if (/\b(?:vs|e\.g|i\.e|et al|Fig|Eq)\.$/i.test(previous) || previous.lastIndexOf('(') > previous.lastIndexOf(')')) {
      sentences[sentences.length - 1] += ' ' + segment;
    } else sentences.push(segment);
  }
  const complete = sentences.filter(sentence => /[.!?。！？][”"’)]?$/.test(sentence));
  const candidates = complete.filter(sentence => sentence.length <= 360);
  return candidates.find(sentence => /\d/.test(sentence) && /\b(?:outperforms?|achieves?|improves? from|reduces? by|reaches?)\b/i.test(sentence))
    ?? candidates.find(sentence => /\bwe (?:introduce|present|propose|demonstrate|show that|find that|release)\b/i.test(sentence))
    ?? complete[0] ?? text;
}

function loadPaperText(element) {
  const {abstractFile, paperId} = element.dataset;
  if (!/^paper-abstracts\/\d{4,}\.json$/.test(abstractFile)) return;
  delete element.dataset.abstractFile;
  const full = Boolean(element.closest('.full-abstract'));
  readAbstractBatch(abstractFile).then(abstracts => {
    if (Object.hasOwn(abstracts, paperId) && typeof abstracts[paperId] === 'string') {
      const text = abstracts[paperId];
      const passage = element.closest('#feed-results') && activeSearch ? searchPassage(text, activeSearch) : '';
      if (passage || !element.dataset.matchedPassage) element.textContent = full ? text : passage || coverSentence(text);
    }
  }).catch(() => {
    if (!full) return;
    element.dataset.abstractFile = abstractFile;
    element.textContent = '原摘要暂时无法载入。重新展开可重试，也可打开原文阅读。';
  });
}
const summaryObserver = typeof IntersectionObserver === 'function' ? new IntersectionObserver(changes => {
  for (const {target, isIntersecting} of changes) {
    if (!isIntersecting) continue;
    summaryObserver.unobserve(target);
    loadPaperText(target);
  }
}, {rootMargin: '80px'}) : null;
function observeSummaries(container) {
  for (const summary of container.querySelectorAll('.source-summary[data-abstract-file]')) {
    summaryObserver?.observe(summary);
  }
}
function abstractAttributes(entry) {
  return ' data-abstract-file="' + escapeHtml(entry.abstractFile) + '" data-paper-id="' + escapeHtml(entry.id) + '"';
}

function shortSummary(value, limit = 245) {
  if (value.length <= limit) return value;
  const start = value.slice(0, limit);
  return (/[a-z0-9]$/i.test(start) ? start.replace(/\s+\S{1,25}$/, '') : start).trimEnd() + '…';
}

function searchContext(entry, key, query) {
  const candidates = [[modules[key].name, '栏目'], [entry.source, '来源'], [entry.id, '编号']];
  if (entry.show) candidates.push([entry.show, '节目']);
  for (const name of entry.guests ?? []) candidates.push([name, '嘉宾']);
  for (const topic of entry.topics ?? []) candidates.push([topic, '话题']);
  for (const path of entry.paths ?? []) {
    const labels = [];
    let level = taxonomy;
    for (const id of path) {
      const node = level?.[id];
      if (!node) break;
      labels.push(node.label);
      level = node.children;
    }
    if (labels.length) candidates.push([labels.join(' › '), '研究方向']);
  }
  for (const [value, label] of candidates) {
    const tokens = new Set(expandedSearchWords(value, query.expansions));
    if (query.terms.every(term => tokens.has(term))) return label + ' · ' + value;
  }
  return '';
}

function cardRows(items, {titlesOnly = false} = {}) {
  return items.map(({key, entry, votes, batch}) => {
    const anchor = safeUrl(entry.original);
    if (!anchor) return '';
    const attributes = ' href="' + escapeHtml(anchor.href) + '" target="_blank" rel="noopener noreferrer"';
    if (titlesOnly) {
      return '<a class="source-card update-title-link"' + attributes + '><h3 class="source-title">' +
        escapeHtml(entry.title) + '</h3>' + icon('external') + '</a>';
    }
    const revision = entry.revision
      ? '<span class="source-revision">已修订 ' + escapeHtml(entry.revision.version) + '</span>'
      : '';
    const points = key === 'events' ? entry.points : entry.highlights?.points;
    const version = entry.highlights?.version;
    const highlights = Array.isArray(points) && points.length
      ? '<div class="source-highlights"><strong>中文要点' +
        (version && version !== entry.version ? ' · ' + escapeHtml(version) : '') +
        '</strong><ul>' + points.map(point => '<li>' + escapeHtml(point) + '</li>').join('') + '</ul></div>'
      : '';
    const passage = activeSearch ? searchPassage(entry.summary, activeSearch) ||
      searchPassage((points ?? []).join(' '), activeSearch) || searchContext(entry, key, activeSearch) : '';
    const excerpt = passage || (key === 'arxiv' && !entry.abstractFile ? coverSentence(entry.summary) : shortSummary(entry.summary));
    const attributesForAbstract = key === 'arxiv' && entry.abstractFile ? abstractAttributes(entry) : '';
    const ageLabel = batch === undefined ? '' : batch === 0 ? '近 10 天' : (batch * 10) + '–' + (batch * 10 + 9) + ' 天前';
    const heatLabel = votes !== undefined ? 'HF 推荐 ' + number.format(votes) + ' 票' : ageLabel ? '暂无公开票数' : '';
    const abstract = currentModule === 'arxiv' && (entry.abstractFile || excerpt !== entry.summary)
      ? '<details class="full-abstract"><summary>论文原摘要</summary><p' +
        attributesForAbstract +
        '>' + escapeHtml(entry.summary) + '</p></details>' : '';
    const card = '<a class="source-card" data-channel="' + key + '"' + attributes + '>' +
      '<div class="source-copy"><div class="source-line"><span class="source-kicker">' + icon(key) + escapeHtml(modules[key].name) +
      '</span>' + revision + (heatLabel ? '<span class="source-revision">' + (ageLabel ? ageLabel + ' · ' : '') + heatLabel + '</span>' : '') +
      '<time datetime="' + escapeHtml(entry.publishedAt ?? entry.published) + '">' + escapeHtml(entry.published) + '</time></div>' +
      '<h3 class="source-title">' + escapeHtml(entry.title) + '</h3>' +
      highlights +
      '<p class="source-summary"' + (passage ? ' data-matched-passage="true"' : '') + attributesForAbstract + '>' + escapeHtml(excerpt) + '</p>' +
      cardDetails(key, entry) +
      '<div class="source-meta"><span class="byline">' + escapeHtml(entry.source) + '</span>' +
      '<span class="source-out">打开原文 ' + icon('external') + '</span></div></div></a>';
    const alternatives = evidenceLinks(entry.evidence, anchor, entry.source) +
      (votes !== undefined ? '<a href="https://huggingface.co/papers/' + encodeURIComponent(entry.id) + '" target="_blank" rel="noopener noreferrer">Hugging Face 投票页 ' + icon('external') + '</a>' : '');
    return '<article class="source-record">' + card + abstract +
      (alternatives ? '<div class="source-evidence" aria-label="其他来源">' + alternatives + '</div>' : '') + '</article>';
  }).join('');
}

function cardList(name, items, options = {}) {
  if (!items.length) return '<p class="empty-state">这个范围还没有收录内容。</p>';
  cardPages[name] = {items, offset: cardPageSize, titlesOnly: options.titlesOnly};
  return '<div class="source-cards">' + cardRows(items.slice(0, cardPageSize), options) + '</div>' +
    (items.length > cardPageSize
      ? '<button class="button button-outline load-more" type="button" data-action="more" data-list="' + name + '">' +
        (options.titlesOnly ? '加载更多标题' : '加载更多') + '</button>'
      : '');
}

function appendCards(button) {
  const list = cardPages[button.dataset.list];
  const cards = button.previousElementSibling;
  const firstNew = cards.childElementCount;
  cards.insertAdjacentHTML('beforeend', cardRows(list.items.slice(list.offset, list.offset + cardPageSize), {titlesOnly: list.titlesOnly}));
  if (!list.titlesOnly) observeSummaries(cards);
  list.offset += cardPageSize;
  const first = cards.children[firstNew];
  (first?.matches('a') ? first : first?.querySelector('a'))?.focus({preventScroll: !list.titlesOnly});
  if (list.offset >= list.items.length) button.remove();
}

function cover(item) {
  const heading = '<div class="cover-label"><span class="signal" aria-hidden="true"></span><h1>最新情报</h1>' +
    (item?.votes ? '<a class="heat-link" href="#/arxiv/trending">新论文热榜 ' + icon('chevron') + '</a>' : '') + '</div>';
  const artwork = '<figure class="feed-visual" aria-hidden="true"><img src="' + headerArt[state.theme] + '" alt="" fetchpriority="high"></figure>';
  if (!item) return '<header class="feed-head">' + artwork + '<div class="cover-copy">' + heading + '<p>当前还没有可阅读的情报。</p></div></header>';
  const {entry, key} = item;
  const title = escapeHtml(entry.title).replace(/^([^:：]{2,20})([:：])\s*/, '<em>$1$2</em> ');
  const point = (key === 'events' ? entry.points : entry.highlights?.points)?.[0];
  const abstract = !point && key === 'arxiv' && entry.abstractFile
    ? abstractAttributes(entry) : '';
  return '<header class="feed-head">' + artwork + '<div class="cover-copy">' + heading +
    '<a class="cover-story" href="' + escapeHtml(entry.original) + '" target="_blank" rel="noopener noreferrer">' +
    '<h2>' + title + '</h2><p' + abstract + '>' + escapeHtml(point || coverSentence(entry.summary)) + '</p>' +
    '<div class="cover-tail"><span class="cover-cta">阅读原文 ' + icon('external') + '</span>' +
    '<span class="cover-credit">' + escapeHtml(modules[key].name) + ' <time>' + escapeHtml(entry.published) + '</time>' +
    (item.votes ? '<span>Hugging Face 推荐 ' + number.format(item.votes) + ' 票</span>' : '') + '</span></div></a></div></header>';
}

function renderHome() {
  const range = retentionWindow();
  homeItems = Object.keys(modules).flatMap(key => entriesFor(key, range).map(entry => ({key, entry})))
    .sort((left, right) => comparePublication(left.entry, right.entry));
  const lead = trendingPapers(range.end)[0] ?? homeItems[0];
  homeFeed = homeItems.filter(item => item.entry !== lead?.entry);
  const channels = Object.entries(modules).map(([key, module]) => {
    const count = newEntries(key).length;
    const label = count ? '新增 ' + number.format(count) + ' ' + module.unit : module.condition && !entriesFor(key, range).length ? '暂无收录' : '无新增';
    const compact = count ? '+' + number.format(count) : label;
    return '<a class="channel-link" data-channel="' + key + '" href="' + route(key) + '" aria-label="' + module.name + '，' + label + '"' +
      (module.condition ? ' title="' + escapeHtml(module.condition.message) + '"' : '') + '>' +
      '<span class="channel-icon">' + icon(key) + '</span><span class="channel-name"><span class="wide">' + module.name + '</span><span class="narrow">' + channelNames[key] + '</span></span>' +
      '<span class="channel-count' + (count ? '' : ' count-read') + '"><span class="wide">' + label + '</span><span class="narrow">' + compact + '</span></span>' +
      icon('chevron') + '</a>';
  }).join('');
  const otherLeads = Object.keys(modules).filter(key => key !== lead?.key)
    .map(key => ({key, entry: entriesFor(key, range)[0]})).filter(item => item.entry)
    .sort((left, right) => comparePublication(left.entry, right.entry));
  const briefs = otherLeads.map(({key, entry}) => '<a class="brief" data-channel="' + key + '" href="' +
    escapeHtml(entry.original) + '" target="_blank" rel="noopener noreferrer"><span class="brief-meta">' +
    modules[key].name + '<time>' + entry.published + '</time></span><strong>' + escapeHtml(entry.title) + '</strong>' +
    '<span class="brief-summary">' + escapeHtml(shortSummary(entry.points?.[0] || entry.summary, 130)) + '</span>' + icon('external') + '</a>').join('');
  return '<div class="feed-page">' + cover(lead) + '<div class="feed-layout">' +
    '<aside class="feed-rail" aria-labelledby="channels-heading"><h2 id="channels-heading">栏目新增</h2><div class="channel-links">' + channels + '</div></aside>' +
    '<section class="feed-primary" aria-labelledby="latest-heading"><div class="feed-section-heading"><div><h2 id="latest-heading">情报索引</h2>' +
    '<p class="range-line">最近 3 个月 <span>·</span> <span id="feed-order">按发布时间</span></p></div>' +
    '<label class="feed-search">' + icon('search') + '<input type="search" id="feed-query" aria-label="搜索主题、作者、来源或完整摘要" aria-controls="feed-results" placeholder="搜索主题、作者或摘要"></label></div>' +
    '<div class="index-meta"><span id="result-total" role="status">收录 ' + number.format(homeItems.length) + ' 条</span><span>' +
    range.start.replaceAll('-', '.') + ' — ' + range.end.replaceAll('-', '.') + '</span></div>' +
    '<div id="feed-results">' + (homeItems.length === 1 ? '' : cardList('home', homeFeed)) + '</div></section>' +
    (briefs ? '<aside class="briefs"><h2>各栏最新</h2>' + briefs + '</aside>' : '') + '</div></div>';
}

let searchSequence = 0;
page.addEventListener('input', async event => {
  if (event.target.id === 'paper-query' && !event.isComposing) {
    const field = event.target;
    const term = field.value.trim();
    const address = location.hash.split('?')[0] + (term ? '?q=' + encodeURIComponent(term) : '');
    history.replaceState(null, '', address);
    const sequence = ++searchSequence;
    const total = page.querySelector('.module-total');
    if (term) total.textContent = '正在检索…';
    const found = term ? await findArticles(term, paperScope) : null;
    if (sequence !== searchSequence || !field.isConnected) return;
    activeSearch = found?.query ?? null;
    const rows = found ? orderPaperSearch(found.items, retentionWindow().end) : paperScope;
    total.textContent = number.format(rows.length) + ' 篇' + (found?.limited ? ' · 全文索引暂不可用' : '');
    page.querySelector('#paper-results').innerHTML = !rows.length && term ? '<p class="empty-state">没有匹配的论文。</p>' : cardList('module', rows);
    observeSummaries(page);
    return;
  }
  if (event.target.id !== 'feed-query') return;
  if (event.isComposing) return;
  const sequence = ++searchSequence;
  const field = event.target;
  const term = field.value.trim();
  const total = page.querySelector('#result-total');
  let items = homeFeed;
  if (term) {
    total.textContent = '正在检索…';
    await new Promise(resolve => setTimeout(resolve, 140));
    if (!field.isConnected || sequence !== searchSequence) return;
    const result = await findArticles(term, homeItems);
    if (!field.isConnected || sequence !== searchSequence) return;
    items = orderPaperSearch(result.items, retentionWindow().end);
    activeSearch = result.query;
    total.textContent = '找到 ' + number.format(items.length) + ' 条' + (result.limited ? ' · 全文索引暂不可用，仅查已载入内容' : '');
  } else {
    activeSearch = null;
    total.textContent = '收录 ' + number.format(homeItems.length) + ' 条';
  }
  page.querySelector('#feed-order').textContent = !term ? '按发布时间' : items.some(item => item.key === 'arxiv')
    ? '论文每 10 天分组 · ' + (currentVotes() ? '组内按 Hugging Face 推荐票' : '票数暂不可用，组内按相关度') : '按相关度';
  summaryObserver?.disconnect();
  page.querySelector('#feed-results').innerHTML = term && !items.length
    ? '<p class="empty-state">没有匹配的情报。可换一个关键词或清空搜索。</p>'
    : !term && homeItems.length === 1 ? '' : cardList('home', items);
  observeSummaries(page);
});
page.addEventListener('compositionend', event => {
  if (['feed-query','paper-query'].includes(event.target.id)) event.target.dispatchEvent(new Event('input', {bubbles: true}));
});
page.addEventListener('toggle', event => {
  if (!event.target.matches('details.full-abstract[open]')) return;
  const text = event.target.querySelector('[data-abstract-file]');
  if (text) loadPaperText(text);
}, true);

function breadcrumb(items) {
  return '<nav class="breadcrumbs" aria-label="当前位置"><a href="#/">首页</a>' +
    items.map(([label, href]) => icon('chevron') + (href ? '<a href="' + href + '">' + label + '</a>' : '<span aria-current="page">' + label + '</span>')).join('') + '</nav>';
}

function updatesButton(key) {
  const count = newEntries(key).length;
  return count
    ? '<button class="button button-outline" type="button" data-action="updates">' + icon('updates') + ' 本次新增 <span>' + count + ' ' + modules[key].unit + '</span></button>'
    : (modules[key].condition ? '' : '<span class="count-badge count-read">无新增</span>');
}

function researchRoute(fields) {
  const path = [];
  let children = taxonomy;
  for (const id of fields) {
    if (!Object.hasOwn(children, id)) break;
    const node = children[id];
    const href = route('arxiv') + '/' + [...path.map(part => part.id), id].map(encodeURIComponent).join('/');
    path.push({id, label: node.label, href});
    children = node.children;
  }
  return {path, children};
}

function automaticCheckLine(key) {
  if (!automaticChecks) return '';
  const check = automaticChecks[key];
  if (check?.archivedAt) return '<p class="module-automatic">已核验内容截至 ' +
    escapeHtml(check.archivedAt.slice(0,10)) + ' · 本栏目尚未启用云端定时更新</p>';
  const labels = {ok: '成功', partial: '部分成功', failed: '失败', unconfigured: '未配置'};
  if (!Object.hasOwn(labels, check?.status) || !collectionOrder(check.checkedAt)) {
    return '<p class="module-automatic">尚无自动检查记录</p>';
  }
  const {unit} = modules[key];
  const count = value => Number.isSafeInteger(value) && value >= 0 ? ' · 新增 ' + value + ' ' + unit : '';
  const time = new Intl.DateTimeFormat('zh-CN', {
    timeZone: zone, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
  let line = '自动检查 ' + time.format(new Date(check.checkedAt)) + ' · ' + labels[check.status] + count(check.inserted);
  if (check.status !== 'ok' && collectionOrder(check.lastSuccessAt)) {
    line += ' · 上次成功 ' + time.format(new Date(check.lastSuccessAt)) + count(check.lastSuccessInserted);
  }
  if (key === 'arxiv' && collectionOrder(check.lastSuccessAt) &&
      Date.now() - Date.parse(check.lastSuccessAt) > 7 * 86_400_000) {
    line += ' · 近 7 天未成功更新';
  }
  if (key === 'arxiv' && check.coverageIncomplete === true) line += ' · 部分历史公告待核验';
  return '<p class="module-automatic">' + escapeHtml(line) + '</p>';
}

function sourceDetails(module) {
  if (!module.sources?.length) return '';
  const labels = {ok:'已检查',partial:'部分收录',failed:'暂未读到',unconfigured:'待配置'};
  const list = module.sources.map(s => '<li>' + escapeHtml(s.account || s.name || s.id) +
    ' · ' + (labels[s.status] || '待核验') + '</li>').join('');
  const baseline = (module.accounts || []).map(a => '<li><a href="https://www.xiaohongshu.com/user/profile/' +
    encodeURIComponent(a.id) + '">' + escapeHtml(a.name) + '</a> · 已记录 ' + a.baseline.length + ' 篇笔记<ul>' +
    a.samples.map(s => '<li><a href="https://www.xiaohongshu.com/explore/' + encodeURIComponent(s.id) + '">' +
    escapeHtml(s.title || '查看笔记') + '</a></li>').join('') + '</ul></li>').join('');
  return '<details class="module-automatic"><summary>订阅与采集状态</summary><ul>' + list + '</ul>' +
    (baseline ? '<p>小红书账号基线 · ' + escapeHtml(module.baselineDate) + '</p><ul>' + baseline + '</ul>' : '') + '</details>';
}

function renderModule(key, path, children, revisionsOnly) {
  const module = modules[key];
  const current = path.at(-1);
  const trending = key === 'arxiv' && current?.id === 'trending';
  const crumbs = current
    ? [[module.name, route(key)], ...path.map(({label, href}, index) => [escapeHtml(label), index < path.length - 1 ? href : null])]
    : [[module.name]];
  const directions = Object.entries(children).map(([id, child]) =>
    '<a class="direction-link" href="' + (current?.href ?? route(key)) + '/' + encodeURIComponent(id) + '"><strong>' +
    escapeHtml(child.label) + '</strong>' + icon('chevron') + '</a>'
  ).join('');
  const entries = entriesFor(key);
  const recentCutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const revisions = key === 'arxiv'
    ? entries.filter(entry => Date.parse(entry.revision?.updatedAt) >= recentCutoff)
      .sort((left, right) => Date.parse(right.revision.updatedAt) - Date.parse(left.revision.updatedAt))
    : [];
  const visible = trending ? [] : revisionsOnly ? revisions : entries.filter(entry => {
    if (key === 'blogs') return !current || entry.kind === current.id;
    const paths = entry.paths ?? (entry.topic === 'general' ? [['llm', 'evaluation', 'general']] : []);
    return !current || paths.some(parts => path.every(({id}, index) => parts[index] === id));
  });
  let contents = trending ? trendingPapers(retentionWindow().end) : visible.map(entry => ({key, entry}));
  if (key === 'arxiv' && !revisionsOnly && !trending) contents = orderPaperSearch(contents, retentionWindow().end);
  paperScope = key === 'arxiv' ? contents : [];
  const search = key === 'arxiv' && !revisionsOnly && !trending ?
    '<label class="feed-search">' + icon('search') + '<input type="search" id="paper-query" aria-label="在此目录搜索标题、作者和完整摘要" placeholder="搜索标题、作者或摘要"></label>' : '';
  return breadcrumb(crumbs) + '<section class="module-page" data-channel="' + key + '"><header class="module-lead"><div class="module-identity"><span class="module-symbol" aria-hidden="true">' + icon(key) + '</span><div>' +
    '<h1>' + escapeHtml(current?.label ?? module.name) + '</h1>' +
    (trending ? '<p>最近 10 天首次发表的论文，按 Hugging Face Daily Papers 社区推荐票排序。</p>' :
      current ? '' : '<p>' + module.description + '</p>') + '</div></div>' +
    '<div class="update-trigger">' +
    (key === 'arxiv' && !current ? '<a class="button button-outline" href="#/arxiv/trending">新论文热榜</a>' : '') +
    (!current && revisions.length
      ? '<a class="button button-outline" href="#/arxiv/revisions">近期修订 ' + revisions.length + ' 篇</a>'
      : '') +
    updatesButton(key) + '</div></header><div class="module-layout">' +
    (directions ? '<section class="direction-block" aria-labelledby="direction-heading"><h2 id="direction-heading">' +
      (key === 'blogs' ? '作者类型' : ['研究方向', '子领域', '具体主题'][path.length]) + '</h2><div class="direction-links">' + directions + '</div></section>' : '') +
    '<section class="entries-block" aria-labelledby="entries-heading"><h2 id="entries-heading">' +
    (trending ? '近 10 天热门论文' : revisionsOnly ? '过去 7 天' : current && key === 'arxiv' ? '主题论文' : '最近 3 个月') +
    ' <span class="module-total" role="status">' + number.format(contents.length) + ' ' + module.unit + '</span></h2>' + search +
    (trending && contents.length ? '<p class="module-automatic">推荐票更新于 ' +
      escapeHtml(new Intl.DateTimeFormat('zh-CN', {timeZone: zone, dateStyle: 'short', timeStyle: 'short'}).format(new Date(paperHeat.checkedAt))) +
      '（北京时间）</p>' : automaticCheckLine(key)) + (module.condition
      ? '<p class="module-notice" role="status">' + module.condition.message + (contents.length ? ' 以下为近期已核实条目。' : '') + '</p>'
      : '') + (!current && ['x','wechat','blogs'].includes(key) ? sourceDetails(module) : '') +
      '<div id="paper-results">' + (trending && !contents.length ? '<p class="empty-state">近 10 天暂无可核验的推荐票。</p>' :
      contents.length || !module.condition ? cardList('module', contents) : '') + '</div></section></div></section>';
}

function render() {
  activeSearch = null;
  const [routePath, queryString] = (location.hash || '#/').slice(2).split('?');
  const fields = routePath.split('/');
  const key = Object.hasOwn(modules, fields[0]) ? fields[0] : undefined;
  const enteredModule = currentModule !== key;
  if (enteredModule) updatesExpanded = false;
  if (sheet.open) {
    sheetTrigger = null;
    sheet.close();
  }
  currentModule = key;
  renderNav(key);
  menuButton.setAttribute('aria-expanded', 'false');
  let {path, children} = key === 'arxiv' ? researchRoute(fields.slice(1)) : {path: [], children: {}};
  if (key === 'blogs') {
    const kind = fields[1];
    if (Object.hasOwn(blogKinds, kind)) path = [{id: kind, label: blogKinds[kind].label}];
    else children = blogKinds;
  }
  const revisionsOnly = key === 'arxiv' && fields[1] === 'revisions';
  const trending = key === 'arxiv' && fields[1] === 'trending';
  if (revisionsOnly) path.push({id: 'revisions', label: '近期修订'});
  if (trending) path.push({id: 'trending', label: '新论文热榜'});
  summaryObserver?.disconnect();
  page.innerHTML = key ? renderModule(key, path, revisionsOnly || trending ? {} : children, revisionsOnly) : renderHome();
  const input = page.querySelector('#paper-query');
  const query = new URLSearchParams(queryString).get('q');
  if (input && query) {
    input.value = query;
    input.dispatchEvent(new Event('input', {bubbles:true}));
  }
  const intro = page.querySelector('.cover-story [data-abstract-file]');
  if (intro) loadPaperText(intro);
  observeSummaries(page);
  const title = key ? (path.at(-1)?.label ?? modules[key].name) : 'AI 前沿情报';
  document.title = 'AIfrontier · ' + title;
  window.scrollTo(0, 0);
  if (key) page.focus({preventScroll:true});
  if (key && enteredModule && newEntries(key).length) openUpdates();
}

function paintSheet() {
  const {name, unit} = modules[currentModule];
  sheet.innerHTML = '<div class="sheet-handle" aria-hidden="true"></div><header class="sheet-heading">' +
    '<h2 id="update-title">' + icon('updates') + ' 本次新增</h2><strong>' + batch.length + ' ' + unit + '</strong></header>' +
    '<p id="update-description">' + name + '在上次确认后收录的内容</p>' +
    (!updatesExpanded ? '<ul class="sheet-preview">' + batch.slice(0, 2).map(entry => '<li>' + escapeHtml(entry.title) + '</li>').join('') + '</ul>' : '') +
    (updatesExpanded ? '<div id="update-list" class="sheet-list">' + cardList('updates', batch.map(entry => ({key:currentModule, entry})), {titlesOnly: true}) + '</div>' : '') +
    '<div class="sheet-actions"><button class="button button-primary" type="button" data-action="toggle-updates" aria-expanded="' + updatesExpanded + '" autofocus>' +
    (updatesExpanded ? '收起标题' : '查看新增') + '</button><button class="button button-outline" type="button" data-action="later">稍后</button>' +
    '<button class="button button-outline" type="button" data-action="mark-read">标为已看</button></div>';
}
function openUpdates(trigger) {
  if (!currentModule) return;
  batch = newEntries(currentModule);
  if (!batch.length) return;
  sheetTrigger = trigger || page.querySelector('[data-action="updates"]') || page;
  paintSheet();
  sheet.showModal();
}
sheet.addEventListener('close', () => {
  if (sheet.open) return;
  const returnFocus = sheetTrigger;
  sheetTrigger = null;
  if (returnFocus?.isConnected) returnFocus.focus({preventScroll:true});
});
sheet.addEventListener('click', event => {
  if (event.target !== sheet) return;
  const bounds = sheet.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) sheet.close();
});

document.addEventListener('click', event => {
  const theme = event.target.closest('.theme-switch button');
  if (theme) {
    state.theme = theme.dataset.theme;
    applyTheme();
    persist();
    return;
  }
  if (event.target.closest('.menu-button')) {
    menuButton.setAttribute('aria-expanded', String(menuButton.getAttribute('aria-expanded') !== 'true'));
    return;
  }
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  switch (button.dataset.action) {
    case 'more':
      appendCards(button);
      break;
    case 'updates':
      openUpdates(button);
      break;
    case 'toggle-updates':
      updatesExpanded = !updatesExpanded;
      paintSheet();
      sheet.querySelector('[data-action="toggle-updates"]').focus({preventScroll:true});
      break;
    case 'later':
      sheet.close();
      break;
    case 'mark-read': {
      acknowledge(currentModule, batch);
      persist();
      const module = modules[currentModule];
      for (const control of page.querySelectorAll('[data-action="updates"]')) control.outerHTML = updatesButton(currentModule);
      const seen = page.querySelector('.count-read');
      sheetTrigger = page;
      if (seen) {
        seen.setAttribute('tabindex', '-1');
        seen.textContent = '本批已看';
        sheetTrigger = seen;
      }
      sheet.close();
      status.textContent = module.name + '已确认 ' + batch.length + ' ' + module.unit + '新增。';
      break;
    }
  }
});

function sourceCondition(sources) {
  const statuses = sources.map(source => source?.status);
  if (statuses.some(status => !['ok', 'unconfigured'].includes(status))) {
    const hasAvailable = statuses.includes('ok') || statuses.includes('partial');
    const label = hasAvailable ? '部分来源暂不可用' : '来源暂不可用';
    return {label, message: label + '。'};
  }
  if (!statuses.length || statuses.includes('unconfigured')) {
    const label = statuses.includes('ok') ? '部分来源未配置' : '来源未配置';
    return {label, message: label + '。'};
  }
}

async function readModuleData(key, file) {
  if (key === 'arxiv' && typeof DecompressionStream === 'function') {
    try {
      const index = await fetch('./papers-reader.json.gz', {cache: 'no-cache'});
      if (index.ok && index.body) {
        const reader = new Response(index.body.pipeThrough(new DecompressionStream('gzip')));
        return await reader.json();
      }
    } catch {
      // The normal JSON snapshot also works without a compressed index.
    }
  }
  const response = await fetch('./' + file, {cache: 'no-cache'});
  if (!response.ok) throw new Error('Could not read ' + file);
  return response.json();
}

async function loadModule(key, module) {
  try {
    const data = await readModuleData(key, module.file);
    const records = key === 'arxiv' ? data.papers : data.items;
    if (!Array.isArray(records) || (key !== 'arxiv' && !Array.isArray(data.sources))) {
      throw new Error('Invalid feed ' + module.file);
    }
    const entries = new Map();
    for (const entry of records) {
      if (!entry || !['id', 'title', 'summary', 'source', 'published'].every(field => typeof entry[field] === 'string')) continue;
      const collection = collectionOrder(entry.collected);
      if (!entry.id || !collection || !safeUrl(entry.original)) continue;
      entries.set(entry.id, {...entry, collection});
    }
    module.entries = [...entries.values()].sort(comparePublication);
    if (key === 'arxiv') taxonomy = data.taxonomy ?? legacyTaxonomy;
    else {
      module.sources = data.sources;
      module.accounts = data.accounts;
      module.baselineDate = data.baselineDate;
      module.condition = sourceCondition(data.sources);
    }
    return !module.condition || entriesFor(key).length > 0 || data.sources.some(source => ['ok', 'partial'].includes(source?.status));
  } catch {
    module.sources = [];
    module.condition = {label: '数据暂不可用', message: '数据暂不可用，请刷新页面重试。'};
    return false;
  }
}

async function start() {
  applyTheme();
  renderNav();
  if (!location.hash || location.hash === '#/') {
    page.setAttribute('aria-busy', 'true');
    page.innerHTML = '<div class="feed-head loading-cover"><figure class="feed-visual" aria-hidden="true"><img src="' +
      headerArt[state.theme] + '" alt="" fetchpriority="high"></figure><div class="cover-copy"><p class="cover-label">' +
      '<span class="signal" aria-hidden="true"></span>正在载入近期情报…</p><div class="skeleton-lines" aria-hidden="true"></div></div></div>';
  }
  const manifest = fetch('./collection-status.json', {cache: 'no-cache'})
    .then(response => response.ok ? response.json() : null)
    .then(data => { automaticChecks = data?.modules; })
    .catch(() => {});
  const heat = fetch('./paper-heat.json', {cache: 'no-cache'})
    .then(response => response.ok ? response.json() : null)
    .then(data => { paperHeat = data; })
    .catch(() => {});
  const loaded = await Promise.all(Object.entries(modules).map(async ([key, module]) => {
    if (await loadModule(key, module) && !state.seen[key]) {
      acknowledge(key, entriesFor(key));
      return true;
    }
    return false;
  }));
  await Promise.all([manifest, heat]);
  if (loaded.some(Boolean)) persist();
  page.removeAttribute('aria-busy');
  window.addEventListener('hashchange', render);
  render();
}

start();
