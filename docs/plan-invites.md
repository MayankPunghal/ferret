# Plan: invite and password-reset links, no email server needed (handoff, 2026-10-24)

Owner question: Google sign-in, account creation with email, or what? Decision (discussed 2026-10-24):
- **Now:** one-time links that the administrator passes on by any channel (Teams, Slack, in person).
- **Later, for the server edition:** generic OpenID Connect sign-in (Google Workspace, Microsoft Entra, Okta,
  Keycloak).
- **Never:** self-sign-up. Only an administrator decides who gets in.
- **Email:** optional, only to deliver the same links through the organisation's own mail service.

Nothing below is built yet.

## Today

- The administrator creates a user **with a password** (Users & access) and must tell it to the person.
  They then both know it.
- Resetting a password means the administrator types a new one (`PATCH /api/admin/users/{id}`). That signs
  the user out everywhere and revokes their assistant tokens.
- A user **cannot change their own password**.
- There is no limit on failed sign-ins: `login_failed` is audited but not slowed down.

## What to build

1. **Invite link.**
   - **Creating the user:** "Add user" asks for username, groups and administrator yes/no, but **no
     password**. It shows a link `https://<server>/#/welcome/<code>` with a Copy button and
     "valid for 7 days, works once".
   - **Opening the link:** the person chooses their own password (twice, at least 10 characters) and is
     signed in.
   - **Until then:** the user exists but cannot sign in. The list shows "invited, not joined yet" with
     **New link** and **Cancel**.
2. **Reset link.** On a user's row, **Reset password** gives the same kind of link (valid 24 hours). The old
   password keeps working until the link is used. Using it:
   - signs the user out everywhere;
   - revokes their assistant tokens (as today);
   - is audited.
3. **Change my password** for every user: current password + new password. It signs out their other
   sessions.
4. **Slow down guessing:** after 5 failed sign-ins for one username in 15 minutes, wait 30 s per attempt. The
   same applies to wrong codes on the welcome page. It must not reveal whether a username exists.
5. **First administrator:** unchanged (the first-run page). As a last resort, the command line
   `ferret reset-password <user>` prints a reset link for someone with access to the server.

## Design details

- **Storage:** one table, reusing the token design of `sessions`:
  `user_links(code_hash bytea primary key, user_id, purpose 'invite'|'reset', created_by, expires_at, used_at)`.
  - Only the SHA-256 of the code is stored, as for sessions, so a database copy cannot be used to take over
    accounts.
  - The code is `secrets.token_urlsafe(32)`.
- **One live link per user and purpose:** a new link cancels the old one.
- **Link address:** taken from a setting `public_url` (Settings → Server address), so the link works when
  the app is behind a proxy. When it is empty, the link uses the address the administrator's browser used,
  with a warning when that is `localhost` or `127.0.0.1`.
- **Logs → Activity:** the code is masked there automatically, because the trace masks any field named
  `token` or `code`. Add `code` to `trace.SECRET`.
- **Audit:** `invite_created`, `invite_used`, `reset_created`, `reset_used`, `password_changed`. The code
  itself is never logged.
- **Keep:** setting a password directly stays available to administrators (Users & access → More) for
  offline cases, but it is no longer the default.

## Optional later: email delivery

- When an organisation wants it, Settings gets an outgoing-mail section: host, port, STARTTLS/SSL, user,
  password (stored like other secrets), sender address, and a **Send test email** button.
- Point it at a mail service they already have: Microsoft 365, Google Workspace, a company relay, or a
  sending service (Amazon SES, Postmark, Mailgun).
- **We do not run a mail server.** Mail from a home-made server is unreliable and lands in spam.
- With mail set up, the invite and reset dialogs get a "Send to <email>" button next to Copy, and the user
  record gets an optional email address. Everything works the same without it.

## Tests

- **Invite:**
  - An invite link works once, then fails.
  - It fails after it expires.
  - A cancelled invite fails.
  - An invited user cannot sign in before joining.
- **Reset:**
  - The old password works until the reset link is used.
  - Using it signs out all sessions and revokes assistant tokens.
- **Change my password:** wrong current password → 400; other sessions are signed out; this one stays.
- **Guessing:** the delay starts after 5 failures, and the response is the same for unknown usernames.
- **Privacy:** codes never appear in the logs, Activity or audit detail.
- **Walkthrough:** `scripts/ui_check.py` invites a user, opens the link in a fresh browser context, sets a
  password and signs in.
