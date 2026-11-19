// Manual source check inside the same RSSHub container as the scheduled job.
// Print only fixed categories, HTTP status and booleans. Response bodies remain in memory.
const cookie = process.env.XIAOHONGSHU_COOKIE;
const accounts = ['5d405f0c0000000016019755', '64e013a7000000000100de51'];
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
      const path = new URL(location, response.url).pathname;
      return [response.status, /captcha|verify|security/i.test(path) ? 'verification' : /login/i.test(path) ? 'login' : 'redirect'].join(' ');
    }
    const html = await response.text();
    return [response.status, 'profile-state=' + html.includes('"userPageData"'),
      'note-cards=' + html.includes('"noteCard"')].join(' ');
  });
  await inspect(owner + ' feed', async () => {
    const response = await request('http://127.0.0.1:1200/xiaohongshu/user/' + owner + '/notes');
    const body = await response.text();
    if (response.ok) return response.status + ' rss-items=' + (body.match(/<item[\s>]/g) || []).length;
    const markers = [
      ['captcha', /CaptchaError|验证码|captcha challenge/i], ['login', /LoginRequiredError|登录失效/],
      ['timeout', /TimeoutError|timeout .*exceeded/i], ['browser-launch', /failed to launch|executable doesn.t exist/i],
      ['page-structure', /SyntaxError|TypeError|Cannot extract|Unexpected token/i], ['upstream-denied', /403 Forbidden|429 Too Many Requests/i],
      ['config', /ConfigNotFoundError/], ['unavailable', /ServiceUnavailableError/],
    ];
    return response.status + ' ' + (markers.find(([, regex]) => regex.test(body))?.[0] || 'unclassified');
  });
}
