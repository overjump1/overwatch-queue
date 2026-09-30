// The privacy policy, served at /privacy. The App Store listing and the iPhone app's About screen
// link here rather than into the repository, so what a reviewer opens is this page and nothing
// else. Keep it true: anything the apps or this worker start keeping belongs in the table.
// The Workers Logs row stays while wrangler.toml has invocation logs on.

export const PRIVACY_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QueueFox — Privacy Policy</title>
<style>
  :root { color-scheme: light dark; --text: #1d1d1f; --muted: #6e6e73; --line: #d2d2d7; --bg: #ffffff; }
  @media (prefers-color-scheme: dark) { :root { --text: #f5f5f7; --muted: #a1a1a6; --line: #3a3a3c; --bg: #111114; } }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 720px; margin: 0 auto; padding: 40px 16px 64px; }
  h1 { font-size: 28px; margin: 0 0 4px; }
  .updated { color: var(--muted); margin: 0 0 24px; }
  table { width: 100%; border-collapse: collapse; margin: 24px 0; font-size: 15px; }
  th, td { text-align: left; vertical-align: top; padding: 10px 8px; border-bottom: 1px solid var(--line); }
  th { font-weight: 600; }
  code { font: 14px ui-monospace, SFMono-Regular, Menlo, monospace; }
  @media (max-width: 560px) {
    table, thead, tbody, tr, th, td { display: block; }
    thead { display: none; }
    tr { border-bottom: 1px solid var(--line); padding: 8px 0; }
    td { border: 0; padding: 4px 0; }
    td:first-child { font-weight: 600; }
  }
</style>
</head>
<body>
<main>
<h1>QueueFox — Privacy Policy</h1>
<p class="updated">Last updated: 30 September 2026</p>

<p>QueueFox has no accounts, no sign-in, no ads and no analytics. Here is everything it stores.</p>

<table>
<thead><tr><th>What</th><th>Where</th><th>How to remove it</th></tr></thead>
<tbody>
<tr><td>A random pairing ID, the state of your queue (mode and times), and the push tokens that let us notify your devices</td><td>Our server, on Cloudflare</td><td><strong>Unpair</strong> in the phone app removes that phone's tokens. <strong>Reset QR code</strong> in the Windows app deletes the whole pairing. Otherwise it stays until you do one of these.</td></tr>
<tr><td>The timings of your last notification test (<strong>Test notifications</strong> in the Windows app)</td><td>Our server, on Cloudflare</td><td>Replaced by your next test, and deleted with the pairing.</td></tr>
<tr><td>Server logs of each request, which include the pairing ID, part of a push token and network details such as your IP address</td><td>Cloudflare</td><td>Deleted automatically after 3 days. They can't be removed sooner.</td></tr>
<tr><td>The pairing ID, the role icons and a log file</td><td>Your PC, in <code>%APPDATA%\\QueueFox</code></td><td>Delete the folder, or uninstall the app.</td></tr>
<tr><td>The pairing ID</td><td>Your phone</td><td>Unpair, or uninstall the app.</td></tr>
</tbody>
</table>

<p>None of this includes your name, email address, game account login or precise location.</p>

<p>The camera is only used to scan the pairing QR code. To spot the role-select screen, the Windows app
briefly looks at the game's window. Neither image is saved or sent anywhere.</p>

<p>Notifications are delivered by Google (Firebase Cloud Messaging) and Apple. Update checks go to
GitHub, and the Windows app downloads its role icons from Fandom once. Like any internet request, these services
see your IP address. Apart from the 3-day server logs above, we don't keep it.</p>
</main>
</body>
</html>
`;
