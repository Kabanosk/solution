# Slack setup for the Autonomous CFO bot

The bot uses Slack **Socket Mode**: it opens an outgoing websocket to Slack, so no public URL, ingress or
OpenShift route is needed. Setup takes about 10 minutes. You need to be able to install apps in the workspace
(the owner of a fresh workspace can).

What the bot does in Slack: DMs employees (every message starts with its level label `[AUTO]`, `[NOTIFY]`,
`[APPROVAL]` or `[SIMULATED]`), shows Approve / Reject buttons to the CEO, and answers free-text DMs from known
employees. Code: `backend/cfo/channels/` (`base.py` is the interface, `slack.py` the Slack adapter).

## 1. Create a workspace

1. Go to <https://slack.com/get-started#/createnew> and create a free workspace, for example `pizzeria-amalfi-demo`.
2. Invite the demo people (CEO, office manager, head chef) or use second accounts of your own. Everyone who
   should get DMs must be a member of the workspace.

## 2. Create the app from the manifest

1. Open <https://api.slack.com/apps> and click **Create New App**, then **From a manifest**.
2. Pick the workspace, choose the **YAML** tab and paste the content of `deploy/slack-app-manifest.yaml`.
3. Click **Next**, review the summary (scopes `chat:write`, `im:write`, `im:history`, `users:read`,
   `app_mentions:read`; Socket Mode on; interactivity on) and click **Create**.

## 3. Install it and collect the two tokens

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
slugs of the HR sheet). This is the supported way. The `slack_id` column of the HR CSV is **not** read by the
seed job, so filling it in has no effect; `Employee.slack_user_id` can only be set in the database or in code.
```
SLACK_USER_MAP=vincenzo=U01AAAAAAA,grazyna=U02BBBBBBB,rosa=U03CCCCCCC
```

Answers to `@Autonomous CFO` mentions in a channel never appear in the channel: the bot sends them to the asker as a
DM and shows only an ephemeral "I answered you in a DM" in the channel (visible to the asker alone). The bot has to
be a member of the channel to see the mention. Every bot message, including those ephemeral notes, starts with a
level label.

Messages from people who are not mapped are never passed to the agent (the bot replies that it only talks to
registered people), and their button clicks are ignored.

## 5. Check it works

1. Start the backend as usual (it calls `SlackNotifier.from_settings(employees).start()`), or try it from a shell:
   ```
   uv run --project backend python -c "
   from cfo.channels.base import approval_actions
   from cfo.channels.slack import SlackNotifier
   from cfo.domain import Employee, PolicyLevel
   ceo = Employee(id='vincenzo', name='Vincenzo', role='CEO', slack_user_id='U01AAAAAAA')
   n = SlackNotifier.from_settings([ceo])
   n.on_approval(print)
   n.start()
   n.send_dm(ceo, 'Test: request a EUR 30,000 credit line?', PolicyLevel.APPROVAL, approval_actions('apr_test'))
   import time; time.sleep(120)
   "
   ```
2. In Slack, open the **Autonomous CFO** app (under Apps in the sidebar, tab **Messages**). A message labelled
   `[APPROVAL]` with **Approve** and **Reject** buttons arrives. Clicking one prints an `ApprovalAnswer` in the
   shell and replaces the buttons with "Approved by Vincenzo".
3. Type a sentence into the same DM: it reaches the chat handler registered with `on_chat`.

## Troubleshooting

- `invalid_auth` / `not_authed`: wrong or missing `SLACK_BOT_TOKEN`; it must start with `xoxb-`.
- `SLACK_APP_TOKEN (xapp-...) is required`: the app-level token is missing or lacks scope `connections:write`.
- Typing in the Messages tab is greyed out ("sending messages to this app has been turned off"): create the app
  from the manifest again, or enable **App Home, Messages Tab, Allow users to send Slash commands and messages**.
- Buttons do nothing: check that **Interactivity & Shortcuts** is on (it is in the manifest) and that the bot process
  is running; clicks are delivered over the socket only while it is connected.
- DMs do not arrive for one person: their Slack user id is missing from `SLACK_USER_MAP` (and `Employee.slack_user_id`)
  (`send_dm` raises `ValueError`).
