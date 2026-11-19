/* Shared by the Pages publisher and the browser, after scope and read-state filtering. */
function paperBaseId(id) {
  return id.replace(/(\d)v\d+$/i, '$1');
}

function rankPapers(papers, votes, today) {
  const dayNumber = Date.parse(today) / 86400000;
  const rows = papers.map(paper => {
    const age = dayNumber - Date.parse(paper.published) / 86400000;
    const count = votes?.[paperBaseId(paper.id)];
    const score = Number.isSafeInteger(count) && count >= 0
      ? (count + 1) * (1 + 2 ** (-age / 30)) * 2 ** (-Math.max(0, age - 30) / 45)
      : -1;
    return {paper, age, score};
  }).sort((a, b) => b.score - a.score || a.age - b.age || a.paper.id.localeCompare(b.paper.id));

  const pools = [[], [], []];
  for (const row of rows) {
    if (row.score < 0) pools[row.age < 10 ? 0 : row.age < 60 ? 1 : 2].push(row);
  }
  const discoveries = new Map();
  const used = new Set();
  for (const [index, position] of [8, 16].entries()) {
    if (position > rows.length) break;
    for (let offset = 0; offset < pools.length; offset++) {
      const pool = pools[(dayNumber + index + offset) % pools.length];
      if (!pool.length) continue;
      const start = dayNumber % pool.length;
      const pick = used.has(pool[start]) ? pool[(start + 1) % pool.length] : pool[start];
      if (!used.has(pick)) {
        discoveries.set(position, pick);
        used.add(pick);
        break;
      }
    }
  }
  // Reserve both discovery slots before counting how many recent papers can enter.
  let recent = [...used].filter(row => row.age < 10).length;
  const head = [];
  for (let position = 1; position <= Math.min(20, rows.length); position++) {
    let pick = discoveries.get(position);
    if (!pick) {
      pick = rows.find(row => !used.has(row) && (row.age >= 10 || recent < 6))
        ?? rows.find(row => !used.has(row));
      used.add(pick);
      if (pick.age < 10) recent++;
    }
    head.push(pick);
  }
  return head.concat(rows.filter(row => !used.has(row))).map(row => row.paper);
}

if (typeof module !== 'undefined' && require.main === module) {
  const {papers, votes, today} = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
  process.stdout.write(JSON.stringify(rankPapers(papers, votes, today).map(paper => paper.id)));
}
