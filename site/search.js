// The builder in scripts/search_index.py uses the same token and shard rules.
const searchStopWords = new Set('a an and are as at be by for from in is of on or that the to with'.split(' '));
function searchStem(word) {
  if (!/^[a-z]+$/.test(word)) return word;
  if (word.length > 4 && word.endsWith('ies')) word = word.slice(0, -3) + 'y';
  else if (word.length > 4 && /(?:sses|ches|shes|xes|zes)$/.test(word)) word = word.slice(0, -2);
  else if (word.length > 3 && /s$/.test(word) && !/(?:ss|us|is)$/.test(word)) word = word.slice(0, -1);
  if (word.length > 5 && word.endsWith('ing')) word = word.slice(0, -3);
  else if (word.length > 4 && word.endsWith('ed')) word = word.slice(0, -2);
  if (word.length > 3 && word.endsWith('e') && !word.endsWith('ee')) word = word.slice(0, -1);
  return word;
}

function searchWords(text) {
  const folded = text.normalize('NFKD').toLowerCase().replace(/\p{M}/gu, '');
  const result = [];
  for (const part of folded.split(/([\u3400-\u9fff]+)/u)) {
    if (/^[\u3400-\u9fff]/u.test(part)) {
      if (part.length === 1) result.push(part);
      else for (let i = 0; i < part.length - 1; i++) result.push(part.slice(i, i + 2));
    } else {
      for (const word of part.match(/[\p{L}\p{N}]+/gu) ?? []) {
        if (!searchStopWords.has(word)) result.push(searchStem(word));
      }
    }
  }
  return result;
}

function expandedSearchWords(text, expansions) {
  return searchWords(text).flatMap(word => expansions.get(word) ?? [word]);
}

function searchPassage(text, query) {
  if (!text || !query?.terms.length) return '';
  let best = '', score = 0;
  // Keep decimal numbers intact. For a long sentence inspect overlapping word windows.
  const sentences = text.split(/(?<=[。！？])|(?<=[.!?])\s+(?=[A-Z0-9"“(])/u);
  for (const sentence of sentences) {
    for (let pos = 0; pos < sentence.length; pos += 185) {
      const slice = sentence.slice(pos, pos + 265).trim();
      const words = new Set(expandedSearchWords(slice, query.expansions));
      const hits = query.terms.filter(term => words.has(term)).length;
      if (hits > score) {
        best = (pos ? '…' : '') + slice + (pos + 265 < sentence.length ? '…' : '');
        score = hits;
      }
      if (hits === query.terms.length) return best;
      if (pos + 265 >= sentence.length) break;
    }
  }
  return best;
}

async function fetchSearchFile(name) {
  const response = await fetch('./search-index/' + name, {cache: 'no-cache'});
  if (!response.ok) throw new Error('Search index unavailable');
  return response.json();
}

let searchManifest;
function openSearchIndex() {
  if (!searchManifest) {
    searchManifest = fetchSearchFile('manifest.json').then(data => {
      const forms = new Map(), expansions = new Map();
      for (const [long, abbr] of Object.entries(data.aliases)) {
        const phrase = long.split(' ');
        if (!forms.has(phrase[0])) forms.set(phrase[0], []);
        forms.get(phrase[0]).push({phrase, abbr});
        expansions.set(abbr, phrase);
      }
      for (const group of forms.values()) group.sort((a, b) => b.phrase.length - a.phrase.length);
      return {build: data.build, ids: data.ids, forms, expansions, shards: new Map()};
    }).catch(error => { searchManifest = null; throw error; });
  }
  return searchManifest;
}

async function searchPostings(index, term) {
  let bucket = 0;
  for (const char of term) bucket = (31 * bucket + char.codePointAt(0)) % 128;
  if (!index.shards.has(bucket)) {
    const file = bucket.toString(16).padStart(2, '0') + '.json';
    const read = fetchSearchFile(file).then(data => {
      if (data.build !== index.build) {
        throw new Error('Search index has been updated', {cause: 'new-build'});
      }
      return data.terms;
    }).catch(error => { index.shards.delete(bucket); throw error; });
    index.shards.set(bucket, read);
  }
  const shard = await index.shards.get(bucket);
  const raw = Object.hasOwn(shard, term) ? shard[term] : [];
  const inverseFrequency = Math.log1p(index.ids.length / (raw.length / 2 + 1));
  const postings = new Map();
  let doc = 0;
  for (let i = 0; i < raw.length; i += 2) {
    doc += raw[i];
    postings.set(doc, inverseFrequency * raw[i + 1]);
  }
  return postings;
}

function loadedSearchScore({key, entry}, query) {
  const fields = [entry.title, [entry.id, entry.source, modules[key].name, entry.show, ...(entry.guests ?? []),
    ...(entry.topics ?? []), ...(entry.highlights?.points ?? []), ...(entry.points ?? [])].filter(Boolean).join(' '), entry.summary];
  const words = fields.map(field => new Set(expandedSearchWords(field, query.expansions)));
  let score = 0;
  for (const term of query.terms) {
    if (!words.some(set => set.has(term))) return 0;
    score += (words[0].has(term) ? 5 : 0) + (words[1].has(term) ? 4 : 0) + (words[2].has(term) ? 2 : 0);
  }
  return score;
}

async function scoreIndex(index, words) {
  // A span can match its words, a shorter acronym plus surrounding words, or
  // a single acronym. Keep the best complete interpretation for each article.
  const paths = words.map((term, i) => [{term, length: 1},
    ...(index.forms.get(term) ?? []).filter(({phrase}) => phrase.every((word, j) => words[i + j] === word))
      .map(({abbr, phrase}) => ({term: abbr, length: phrase.length}))]);
  const terms = [...new Set(paths.flat().map(edge => edge.term))];
  const lists = new Map(await Promise.all(terms.map(async term => [term, await searchPostings(index, term)])));
  const suffix = [];
  for (let i = words.length - 1; i >= 0; i--) {
    const matches = new Map();
    for (const {term, length} of paths[i]) {
      const list = lists.get(term);
      const next = suffix[i + length];
      const candidates = next && next.size < list.size ? next : list;
      for (const doc of candidates.keys()) {
        if (!list.has(doc) || (next && !next.has(doc))) continue;
        const score = length * list.get(doc) + (next?.get(doc) ?? 0);
        matches.set(doc, Math.max(matches.get(doc) ?? 0, score));
      }
    }
    suffix[i] = matches;
  }
  return new Map(Array.from(suffix[0] ?? [], ([doc, score]) => [index.ids[doc], score]));
}

async function findArticles(text, items, retry = true) {
  let query = {terms: [], expansions: new Map()};
  let scores, limited = false;
  try {
    const index = await openSearchIndex();
    const words = expandedSearchWords(text, index.expansions);
    query = {expansions: index.expansions, terms: [...new Set(words)]};
    scores = await scoreIndex(index, words);
  } catch (error) {
    if (error.cause === 'new-build') {
      searchManifest = null;
      if (retry) return findArticles(text, items, false);
    }
    limited = true;
    query.terms = [...new Set(expandedSearchWords(text, query.expansions))];
  }
  const matches = [];
  if (query.terms.length) {
    const literal = text.toLowerCase();
    for (const item of items) {
      const score = limited ? loadedSearchScore(item, query) : scores.get(item.key + ':' + item.entry.id);
      if (score) matches.push({item, score: score * (item.entry.title.toLowerCase().includes(literal) ? 1.25 : 1)});
    }
    matches.sort((a, b) => b.score - a.score); // Stable ties retain publication order.
  }
  return {items: matches.map(match => match.item), query, limited};
}
