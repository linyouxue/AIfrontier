// Manual source check inside the same RSSHub container as the scheduled job.
// Print only fixed categories, HTTP status and booleans. Response bodies remain in memory.
const cookie = process.env.XIAOHONGSHU_COOKIE;
const accounts = process.argv.slice(2).filter(value => /^[0-9a-f]{24}$/.test(value));
if (!accounts.length) process.exit(0);
async function request(url, authenticated = false) {
  return fetch(url, {headers: authenticated && cookie ? {Cookie: cookie} : {},
    redirect: 'manual', signal: AbortSignal.timeout(90000)});
}
async function inspect(stage, probe) {
  try { console.log(stage, await probe()); }
  catch { console.log(stage, 'transport-or-format-error'); }
}
await inspect('rsshub-health', async () => (await request('http://127.0.0.1:1200/healthz')).status);
await inspect('xiaohongshu-session', async () => {
  if (!cookie) return 'not-configured';
  const response = await request('https://edith.xiaohongshu.com/api/sns/web/v2/user/me', true);
  if (!response.ok) return 'HTTP ' + response.status;
  const data = await response.json();
  return data.code === 0 && data.data?.user_id && data.data?.guest !== true ? 'recognized' : 'not-confirmed';
});
for (const owner of accounts) {
  await inspect(owner + ' profile', async () => {
    const response = await request('https://www.xiaohongshu.com/user/profile/' + owner, true);
    const location = response.headers.get('location') || '';
    if (response.status >= 300 && response.status < 400) {
      const target = new URL(location, response.url);
      const message = ['error_msg', 'errorMsg', 'message', 'msg'].map(key => target.searchParams.get(key) || '').join(' ');
      const kind = /ip.{0,20}(风险|异常|限制)|网络环境|network environment/i.test(message) ? 'network-restricted'
        : /captcha|verify|security|验证/i.test(target.pathname + ' ' + message) ? 'verification'
        : /login|登录/i.test(target.pathname + ' ' + message) ? 'login'
        : /404|error/i.test(target.pathname) ? 'error-page' : 'redirect';
      const code = target.searchParams.get('error_code') || target.searchParams.get('code');
      return [response.status, kind, /^-?\d{1,8}$/.test(code || '') ? 'code=' + code : ''].filter(Boolean).join(' ');
    }
    const html = await response.text();
    return [response.status, 'profile-state=' + html.includes('"userPageData"'),
      'note-cards=' + html.includes('"noteCard"')].join(' ');
  });
}
