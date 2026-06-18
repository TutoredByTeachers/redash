import html
import logging
import re

import requests

from redash.destinations import BaseDestination, register
from redash.models import Alert

API_URL = "https://slack.com/api/chat.postMessage"

# Slack Block Kit hard limits: a section's text object caps at 3000 characters and a
# message accepts at most 50 blocks. We leave a little headroom on the text cap for the
# mrkdwn we add around chunks, and reserve a block for the "output truncated" notice.
SECTION_TEXT_LIMIT = 2900
MAX_BLOCKS = 50

STATE_EMOJI = {
    Alert.TRIGGERED_STATE: ":red_circle:",
    Alert.OK_STATE: ":large_green_circle:",
    Alert.UNKNOWN_STATE: ":white_circle:",
}

# A Slack user id is "U…"/"W…"; a user-group (subteam) is "S…"; a channel is "C…"/"G…".
# Accept a bare id, an already-wrapped "<@U…>" / "<#C…>" / "<!subteam^S…>", or Slack's
# copy form "<@U…|label>".
_WRAPPED_RE = re.compile(r"^<[@#!]?([A-Z0-9^]+)(?:\|[^>]*)?>$", re.IGNORECASE)
_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{6,}$")
_SPECIAL = {"here": "<!here>", "channel": "<!channel>", "everyone": "<!everyone>"}

# A valid Slack mrkdwn entity: a mention/channel/special (<@…>, <#…>, <!…>) or a
# <url> / <url|label> link. Used to keep author-typed entities intact while
# neutralizing stray angle brackets that arrive from query result data.
_SLACK_ENTITY_RE = re.compile(r"<(?:[@#!][^>]+|https?://[^>]+|mailto:[^>]+)>")


class SlackBot(BaseDestination):
    """Slack alert destination that posts via the modern Web API (chat.postMessage)
    using a bot token. Unlike the legacy incoming-webhook ``slack`` destination, this
    one renders Block Kit messages, so it supports real ``<@USERID>`` mentions that
    notify, ``<https://url|label>`` hyperlinks, long bodies split across multiple blocks
    (no "Show more..." collapse), and posting to one or more channels chosen per alert.
    """

    @classmethod
    def name(cls):
        return "Slack (Bot)"

    @classmethod
    def type(cls):
        return "slack_bot"

    @classmethod
    def icon(cls):
        return "fa-slack"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "bot_token": {
                    "type": "string",
                    "title": "Slack Bot Token (xoxb-...)",
                },
                "channels": {
                    "type": "string",
                    "title": "Channels (comma-separated channel IDs or #names)",
                    "description": (
                        "The bot must be a member of each channel (invite it with "
                        "/invite @your-bot). Mentioned users are only notified if they "
                        "are also members of the channel; in a private channel a "
                        "non-member is not notified at all."
                    ),
                },
                "default_user_ids": {
                    "type": "string",
                    "title": "Always-mention Slack member IDs (comma-separated)",
                    "description": (
                        "Slack MEMBER IDs to @mention on every alert, e.g. "
                        "U012AB3CD,U034CD5EF. Get an ID from the person's Slack profile "
                        "-> More (...) -> Copy member ID. Display names and @usernames "
                        "do NOT work here. You may also use @here, @channel, @everyone, "
                        "or a user-group ID (S...)."
                    ),
                },
                "thread_ts": {
                    "type": "string",
                    "title": "Thread timestamp (optional)",
                },
            },
            "secret": ["bot_token"],
            "required": ["bot_token", "channels"],
        }

    @staticmethod
    def _split_text(text, limit=SECTION_TEXT_LIMIT):
        """Chunk a long string into pieces no longer than ``limit``, preferring to break
        on line boundaries so rendered tables/lists stay readable."""
        if not text:
            return []

        chunks = []
        current = ""
        for line in text.splitlines(keepends=True):
            # A single line longer than the limit has to be hard-split.
            while len(line) > limit:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(line[:limit])
                line = line[limit:]
            if len(current) + len(line) > limit:
                chunks.append(current)
                current = line
            else:
                current += line
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _normalize_mention(token):
        """Turn one configured token into a Slack mention entity, or return None (the
        caller logs the skip). Accepts:
          - user ids U…/W…             -> <@U…>
          - user-group (subteam) S…    -> <!subteam^S…>
          - channel ids C…/G…          -> <#C…>
          - @here / @channel / @everyone
          - already-wrapped <@U…>, <@U…|label>, <#C…>, <!subteam^S…>
        A leading '@' is stripped and ids are upper-cased before classification."""
        token = token.strip()
        if not token:
            return None

        # Unwrap an already-bracketed form down to its inner id (drops any |label).
        match = _WRAPPED_RE.match(token)
        if match:
            token = match.group(1)

        token = token.lstrip("@").strip()
        if not token:
            return None

        if token.lower() in _SPECIAL:
            return _SPECIAL[token.lower()]

        # A subteam may arrive as "subteam^S123" or bare "S123".
        if "^" in token:
            token = token.partition("^")[2]
        upper = token.upper()

        if not _ID_RE.match(upper):
            return None
        prefix = upper[0]
        if prefix in ("U", "W"):
            return "<@{}>".format(upper)
        if prefix == "S":
            return "<!subteam^{}>".format(upper)
        if prefix in ("C", "G"):
            return "<#{}>".format(upper)
        # Unknown id class — don't guess; let the caller log it.
        return None

    @classmethod
    def _mentions(cls, options):
        """Return the mention entities from ``default_user_ids``, logging any token we
        could not turn into a valid mention so a misconfiguration is visible instead of
        silently dropped."""
        raw = (options.get("default_user_ids") or "").strip()
        if not raw:
            return []

        mentions = []
        for token in (t.strip() for t in raw.split(",") if t.strip()):
            entity = cls._normalize_mention(token)
            if entity:
                mentions.append(entity)
            else:
                logging.warning(
                    "Slack(bot) ignoring unrecognized mention token %r (expected a Slack member ID like U012AB3CD)",
                    token,
                )
        return mentions

    @classmethod
    def _mention_prefix(cls, options):
        mentions = cls._mentions(options)
        return (" ".join(mentions) + " ") if mentions else ""

    @staticmethod
    def _render_mrkdwn(text):
        """Prepare a Mustache-HTML-escaped string for Slack Block Kit mrkdwn.

        ``custom_subject``/``custom_body`` are rendered with HTML escaping, so author-typed
        Slack entities (``<@U…>`` mentions, ``<#C…>`` channels, ``<url|label>`` links) arrive
        escaped and would show as literal text. We unescape so they render -- but a naive
        blanket unescape would also re-expose ``<``/``>`` from query result data and let Slack
        mis-parse it as broken markup. So we keep recognized entities verbatim and re-escape
        only the stray angle brackets around them."""
        if not text:
            return ""

        unescaped = html.unescape(text)

        out = []
        last = 0
        for match in _SLACK_ENTITY_RE.finditer(unescaped):
            segment = unescaped[last : match.start()]
            out.append(segment.replace("<", "&lt;").replace(">", "&gt;"))
            out.append(match.group(0))  # keep the entity verbatim
            last = match.end()
        tail = unescaped[last:]
        out.append(tail.replace("<", "&lt;").replace(">", "&gt;"))
        return "".join(out)

    def _build_blocks(self, alert, query, new_state, host, mention_prefix):
        emoji = STATE_EMOJI.get(new_state, "")
        if new_state == Alert.TRIGGERED_STATE:
            headline = alert.custom_subject or "{} just triggered".format(alert.name)
        else:
            headline = "{} went back to normal".format(alert.name)
        # Render the subject so an intentional <@U…> in it pings, while data angle
        # brackets stay literal.
        headline = self._render_mrkdwn(headline)

        query_link = "{host}/queries/{query_id}".format(host=host, query_id=query.id)
        alert_link = "{host}/alerts/{alert_id}".format(host=host, alert_id=alert.id)

        header_text = "{}{} *{}*".format(mention_prefix, emoji, headline).strip()
        # custom_subject is template-rendered and can exceed the section limit. Trim the
        # headline (not the composed string) so the leading mentions and the closing "*"
        # are never severed.
        if len(header_text) > SECTION_TEXT_LIMIT:
            overflow = len(header_text) - (SECTION_TEXT_LIMIT - 1)
            headline = headline[: max(0, len(headline) - overflow)].rstrip() + "…"
            header_text = "{}{} *{}*".format(mention_prefix, emoji, headline).strip()

        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "<{}|View query>  •  <{}|View alert>".format(query_link, alert_link)}
                ],
            },
        ]

        # custom_body is Mustache-rendered with HTML escaping; _render_mrkdwn restores
        # author-typed mentions/links while keeping result-data angle brackets literal.
        body = self._render_mrkdwn(alert.custom_body)
        for chunk in self._split_text(body):
            if len(blocks) >= MAX_BLOCKS - 1:
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "_…output truncated; open the query to see full results_",
                        },
                    }
                )
                break
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
        return blocks

    def notify(self, alert, query, user, new_state, app, host, metadata, options):
        # Documentation: https://api.slack.com/methods/chat.postMessage
        headers = {
            "Authorization": "Bearer {}".format(options.get("bot_token")),
            "Content-Type": "application/json; charset=utf-8",
        }

        mentions = self._mentions(options)
        mention_prefix = (" ".join(mentions) + " ") if mentions else ""
        blocks = self._build_blocks(alert, query, new_state, host, mention_prefix)
        # Fallback text shown in notification previews and on clients that can't render
        # blocks. Lead with the mentions so the preview also carries the ping, and
        # unescape so the subject doesn't show &gt;/&amp; literally.
        subject = html.unescape(alert.custom_subject or alert.name or "")
        fallback = (mention_prefix + subject).strip()[:300]
        thread_ts = (options.get("thread_ts") or "").strip() or None

        for channel in (c.strip() for c in (options.get("channels") or "").split(",")):
            if not channel:
                continue
            payload = {
                "channel": channel,
                "text": fallback,
                "blocks": blocks,
                "unfurl_links": False,
            }
            if thread_ts:
                payload["thread_ts"] = thread_ts
            self.post_message(payload, headers, channel)

    def post_message(self, payload, headers, channel):
        # Slack's Web API returns HTTP 200 even on logical failure, so we must inspect
        # the "ok" flag in the JSON body rather than trusting the status code alone.
        try:
            resp = requests.post(API_URL, json=payload, headers=headers, timeout=5.0)
            if resp.status_code != 200:
                logging.error("Slack(bot) HTTP error to %s. status_code => %s", channel, resp.status_code)
                return
            data = resp.json()
            if not data.get("ok"):
                logging.error("Slack(bot) API error to %s: %s", channel, data.get("error"))
        except Exception:
            logging.exception("Slack(bot) send ERROR to channel %s.", channel)


register(SlackBot)
