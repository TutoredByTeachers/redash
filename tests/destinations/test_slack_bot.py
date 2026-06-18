from unittest import mock

from redash.destinations.slack_bot import (
    API_URL,
    MAX_BLOCKS,
    SECTION_TEXT_LIMIT,
    SlackBot,
)
from redash.models import Alert


def _alert(custom_subject="Test Subject", custom_body="Test Body", name="Test Alert"):
    alert = mock.Mock()
    alert.id = 1
    alert.name = name
    alert.custom_subject = custom_subject
    alert.custom_body = custom_body
    return alert


def _query(query_id=10):
    query = mock.Mock()
    query.id = query_id
    return query


def _notify(options, new_state=Alert.TRIGGERED_STATE, alert=None, ok=True, status_code=200):
    alert = alert or _alert()
    destination = SlackBot(options)
    with mock.patch("redash.destinations.slack_bot.requests.post") as mock_post:
        response = mock.Mock()
        response.status_code = status_code
        response.json.return_value = {"ok": ok} if ok else {"ok": False, "error": "channel_not_found"}
        mock_post.return_value = response
        destination.notify(alert, _query(), mock.Mock(), new_state, mock.Mock(), "http://redash.local", {}, options)
    return mock_post


def test_notify_posts_once_per_channel_with_bearer_auth():
    options = {"bot_token": "xoxb-123", "channels": "C012ABC, C034DEF"}
    mock_post = _notify(options)

    assert mock_post.call_count == 2
    posted_channels = {call.kwargs["json"]["channel"] for call in mock_post.call_args_list}
    assert posted_channels == {"C012ABC", "C034DEF"}
    for call in mock_post.call_args_list:
        assert call.args[0] == API_URL
        assert call.kwargs["headers"]["Authorization"] == "Bearer xoxb-123"
        assert "blocks" in call.kwargs["json"]


def test_notify_skips_blank_channel_entries():
    options = {"bot_token": "xoxb-123", "channels": "C012ABC, ,  ,C034DEF"}
    mock_post = _notify(options)
    assert mock_post.call_count == 2


def test_api_error_logs_and_does_not_raise():
    options = {"bot_token": "xoxb-123", "channels": "C012ABC"}
    # ok=False -> Slack returns HTTP 200 with {"ok": false}. Must not raise.
    with mock.patch("redash.destinations.slack_bot.logging.error") as mock_log:
        mock_post = _notify(options, ok=False)
    assert mock_post.call_count == 1
    assert mock_log.called


def test_mention_prefix_normalizes_and_accepts_variants():
    f = SlackBot._mention_prefix
    # Bare id, pre-wrapped, leading '@', lowercase, copy-form with |label, Enterprise W id.
    assert f({"default_user_ids": "U012ABC, <@U034DEF>"}) == "<@U012ABC> <@U034DEF> "
    assert f({"default_user_ids": "@U012ABC"}) == "<@U012ABC> "
    assert f({"default_user_ids": "u012abc"}) == "<@U012ABC> "  # uppercased
    assert f({"default_user_ids": "<@U012ABC|alice>"}) == "<@U012ABC> "  # copy form, label dropped
    assert f({"default_user_ids": "W012ABCDE"}) == "<@W012ABCDE> "  # Enterprise grid user
    assert f({"default_user_ids": ""}) == ""
    assert f({}) == ""


def test_mention_prefix_routes_non_user_ids_and_specials():
    f = SlackBot._mention_prefix
    assert f({"default_user_ids": "S012GRP"}) == "<!subteam^S012GRP> "
    assert f({"default_user_ids": "C012CHAN"}) == "<#C012CHAN> "
    assert f({"default_user_ids": "@here"}) == "<!here> "
    assert f({"default_user_ids": "channel"}) == "<!channel> "


def test_mention_prefix_skips_and_logs_invalid_tokens():
    # Display names / emails / garbage must be skipped, NOT silently dropped: a warning fires.
    with mock.patch("redash.destinations.slack_bot.logging.warning") as warn:
        result = SlackBot._mention_prefix({"default_user_ids": "john.doe, U012ABC"})
    assert result == "<@U012ABC> "  # the valid one still resolves
    assert warn.called  # the invalid one logged


def test_mention_prefix_appears_in_first_block():
    options = {"bot_token": "xoxb-123", "channels": "C012ABC", "default_user_ids": "U012ABC"}
    mock_post = _notify(options)
    blocks = mock_post.call_args_list[0].kwargs["json"]["blocks"]
    assert blocks[0]["text"]["text"].startswith("<@U012ABC>")


def test_subject_typed_mention_renders_not_escaped_in_header():
    # A <@U...> typed into custom_subject must reach Slack unescaped (renders + notifies),
    # not as literal &lt;@U...&gt;. The subject arrives Mustache-HTML-escaped.
    alert = _alert(custom_subject="&lt;@U999ZZZ&gt; check this")
    options = {"bot_token": "xoxb-123", "channels": "C012ABC"}
    blocks = _notify(options, alert=alert).call_args_list[0].kwargs["json"]["blocks"]
    header = blocks[0]["text"]["text"]
    assert "<@U999ZZZ>" in header
    assert "&lt;" not in header and "&gt;" not in header


def test_body_data_angle_brackets_are_neutralized_but_entities_survive():
    # Result data containing < and > must NOT become broken Slack markup, while an
    # author-typed mention and link survive.
    body = "Alert <@U123ABC>: see <http://x.test|here>. Data: List&lt;String&gt; x &lt; 5"
    alert = _alert(custom_body=body)
    options = {"bot_token": "xoxb-123", "channels": "C012ABC"}
    blocks = _notify(options, alert=alert).call_args_list[0].kwargs["json"]["blocks"]
    rendered = blocks[-1]["text"]["text"]
    assert "<@U123ABC>" in rendered  # mention entity intact
    assert "<http://x.test|here>" in rendered  # link entity intact
    assert "List&lt;String&gt;" in rendered  # data angle brackets re-neutralized
    assert "<String>" not in rendered  # not re-exposed as markup


def test_fallback_text_includes_mentions_and_is_unescaped():
    alert = _alert(custom_subject="Sales &gt; 100 &amp; rising")
    options = {"bot_token": "xoxb-123", "channels": "C012ABC", "default_user_ids": "U012ABC"}
    payload = _notify(options, alert=alert).call_args_list[0].kwargs["json"]
    assert payload["text"].startswith("<@U012ABC>")  # mention carried into fallback
    assert "&gt;" not in payload["text"] and "&amp;" not in payload["text"]
    assert "Sales > 100 & rising" in payload["text"]


def test_split_text_respects_limit_and_round_trips():
    body = "\n".join("line {}".format(i) * 50 for i in range(400))
    chunks = SlackBot._split_text(body)
    assert all(len(c) <= SECTION_TEXT_LIMIT for c in chunks)
    assert "".join(chunks) == body
    assert len(chunks) > 1


def test_long_body_is_capped_at_max_blocks_with_notice():
    huge_body = "x" * (SECTION_TEXT_LIMIT * (MAX_BLOCKS + 10))
    alert = _alert(custom_body=huge_body)
    options = {"bot_token": "xoxb-123", "channels": "C012ABC"}
    mock_post = _notify(options, alert=alert)
    blocks = mock_post.call_args_list[0].kwargs["json"]["blocks"]
    assert len(blocks) <= MAX_BLOCKS
    assert "truncated" in blocks[-1]["text"]["text"]


def test_triggered_vs_ok_headline_and_emoji():
    options = {"bot_token": "xoxb-123", "channels": "C012ABC"}

    triggered = _notify(options, new_state=Alert.TRIGGERED_STATE).call_args_list[0].kwargs["json"]["blocks"]
    assert ":red_circle:" in triggered[0]["text"]["text"]
    assert "Test Subject" in triggered[0]["text"]["text"]

    recovered = (
        _notify(options, new_state=Alert.OK_STATE, alert=_alert(custom_subject=None))
        .call_args_list[0]
        .kwargs["json"]["blocks"]
    )
    assert ":large_green_circle:" in recovered[0]["text"]["text"]
    assert "went back to normal" in recovered[0]["text"]["text"]


def test_context_block_has_query_and_alert_links():
    options = {"bot_token": "xoxb-123", "channels": "C012ABC"}
    blocks = _notify(options).call_args_list[0].kwargs["json"]["blocks"]
    context_text = blocks[1]["elements"][0]["text"]
    assert "<http://redash.local/queries/10|View query>" in context_text
    assert "<http://redash.local/alerts/1|View alert>" in context_text


def test_oversized_subject_keeps_balanced_bold_and_mention_prefix():
    alert = _alert(custom_subject="A" * (SECTION_TEXT_LIMIT * 2))
    options = {"bot_token": "xoxb-123", "channels": "C012ABC", "default_user_ids": "U012ABC"}
    blocks = _notify(options, alert=alert).call_args_list[0].kwargs["json"]["blocks"]
    header = blocks[0]["text"]["text"]
    assert len(header) <= SECTION_TEXT_LIMIT
    assert header.startswith("<@U012ABC>")  # mentions never cut
    assert header.count("*") == 2  # bold stays balanced (opening + closing)
