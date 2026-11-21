# Slack setup for the Autonomous CFO bot

The bot uses Slack **Socket Mode**: it opens an outgoing websocket to Slack, so no public URL, ingress or
OpenShift route is needed. Setup takes about 10 minutes. You need to be able to install apps in the workspace
(the owner of a fresh workspace can).

What Scout does in Slack: DMs employees (messages start with a level label such as `[AUTO]`, `[NOTIFY]`,
`[MESSAGE]`, `[APPROVAL]` or `[SIMULATED]`), shows approval buttons, answers known employees' DMs, and continues
channel conversations in the thread where someone mentioned Scout. Code: `backend/scout/slack_ui.py` and
`backend/scout/worker.py`.

## 1. Create a workspace

1. Go to <https://slack.com/get-started#/createnew> and create a free workspace, for example `pizzeria-amalfi-demo`.
2. Invite the demo people (CEO, office manager, head chef) or use second accounts of your own. Everyone who
   should get DMs must be a member of the workspace.

## 2. Create the app from the manifest

1. Open <https://api.slack.com/apps> and click **Create New App**, then **From a manifest**.
2. Pick the workspace, choose the **YAML** tab and paste the content of `deploy/slack-app-manifest.yaml`.
3. Click **Next**, review the summary (scopes `chat:write`, `im:write`, `im:history`, `users:read`,
   `app_mentions:read`; Socket Mode on; interactivity on) and click **Create**.

## 3. Install it and collect the app-level token

For Scout workspace onboarding, the **bot token comes from each owner's OAuth install** and is encrypted per workspace.
The singleton event receiver uses only `SLACK_APP_TOKEN` as app-level transport; it does not use a global bot token for
new organizations. `SLACK_BOT_TOKEN` and `SLACK_USER_MAP` below are legacy demo options only. The installing owner
is linked through their verified OAuth user ID, not a copied member ID. Other colleagues need an explicit stable
Slack user-ID link in their own tenant. Connected pre-upgrade installations must disconnect and reconnect to
record this link and grant the new scopes.

## 3a. Legacy demo setup: bot and app tokens

| Token | Looks like | Where to get it | Environment variable |
|---|---|---|---|
| Bot token | `xoxb-...` | **OAuth & Permissions**, **Install to Workspace** (confirm), then copy **Bot User OAuth Token** | `SLACK_BOT_TOKEN` |
| App-level token | `xapp-...` | **Basic Information**, section **App-Level Tokens**, **Generate Token and Scopes**, name it `socket`, add scope `connections:write`, **Generate**, copy | `SLACK_APP_TOKEN` |

Put both into `.env` at the repository root (copy `.env.example`, the file is git-ignored). On OpenShift put
them into a Secret and expose them as environment variables of the backend deployment. Never commit them.

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

If you change scopes later, click **Reinstall to Workspace** and use the new bot token.

## 4. Map employees to Slack users

The bot needs the Slack user id (`U01ABC...`, not the display name) of each employee. 

**Find the ids**
- In Slack: open a person's profile, click the three dots, **Copy member ID**.
- Or list everybody with the bot token (scope `users:read`):
  ```
  curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" https://slack.com/api/users.list \
    | python3 -c "import sys,json; [print(m['id'], m['name'], m.get('real_name','')) for m in json.load(sys.stdin)['members'] if not m['is_bot']]"
  ```

**Environment variable `SLACK_USER_MAP`** (comma-separated `employee_id=slack_user_id`, employee ids are the
slugs of the HR sheet). This is the supported way; the HR sheet carries no Slack ids, and
`Employee.slack_user_id` can only be set in the database or in code.
```
SLACK_USER_MAP=vincenzo=U01AAAAAAA,grazyna=U02BBBBBBB,rosa=U03CCCCCCC
```

Mention `@Scout` in a channel to start a conversation. Scout answers in that same thread; later replies there
continue it without another mention and retain its earlier context. Unrelated channel messages and threads are
ignored. Scout must be a member of the channel, and its Slack app must subscribe to `app_mention`,
`message.channels` and `message.groups` (alongside `message.im` for DMs). These channel-message events are needed
even when `app_mention` is already enabled: Slack does not emit another mention event for an ordinary thread reply.

For an existing app, apply the updated manifest in **App Manifest**, or enable these events in **Event Subscriptions**.
A code rollout alone cannot change Slack's event subscriptions. Existing `channels:history` and `groups:history`
scopes are listed in the manifest; reinstall the app if Slack requests newly granted scopes.

Messages from people who are not mapped are never passed to the agent (the bot replies that it only talks to
registered people), and their button clicks are ignored.

## 5. Check it works

1. Start the configured Scout worker (the API serves dashboard requests; the worker owns Slack Socket Mode):

   ```bash
   uv run --project backend python -m scout.worker
   ```

2. From a mapped Slack account, mention Scout in a channel it belongs to. Confirm its answer is in the mention's
   thread, then reply there **without another mention**. Confirm the next answer stays in the same thread and
   understands the previous question. A normal message elsewhere in the channel must not trigger Scout.
3. Ask Scout in dashboard chat to message another mapped employee. Reply in that employee's exact message thread.
   With the dashboard chat active, confirm the reply appears as an attributed update. With that chat closed or a
   different dashboard page active, confirm the requester receives a `[MESSAGE]` Slack fallback after about 12 seconds.
4. Automated regression tests use FakeSlack/FakeLLM and isolated local Postgres schemas; they do not message real
   people. Live checks like steps 2–3 are deliberate employee messages and should be performed only when authorized.

## Troubleshooting

- `invalid_auth` / `not_authed`: wrong or missing `SLACK_BOT_TOKEN`; it must start with `xoxb-`.
- `SLACK_APP_TOKEN (xapp-...) is required`: the app-level token is missing or lacks scope `connections:write`.
- Typing in the Messages tab is greyed out ("sending messages to this app has been turned off"): create the app
  from the manifest again, or enable **App Home, Messages Tab, Allow users to send Slash commands and messages**.
- Buttons do nothing: check that **Interactivity & Shortcuts** is on (it is in the manifest) and that the bot process
  is running; clicks are delivered over the socket only while it is connected.
- DMs do not arrive for one person: their Slack user id is missing from `SLACK_USER_MAP` (and `Employee.slack_user_id`)
  (`send_dm` raises `ValueError`).

## People: Slack is the source of truth

Everyone in the installed workspace can use Scout, registered or not:

- Right after the install (and every `SLACK_SWEEP_SECONDS`, default 600), Scout DMs each human member who has no
  Scout account and asks for their first and last name. Each person is asked once (`slack_members`).
- A new member who joins the workspace gets the same DM at once (`team_join` event; the sweep catches them otherwise).
- Replying with a name (for example `Anna Kowalska`) creates their account: an `employees` row (role `Staff`) linked
  to their Slack user id. Any other DM from someone without an account gets the question again.
- Asking Scout to message someone (`send_dm`, handing over a task) works for any workspace member: if they never
  registered, their account is created from their Slack profile name first.
