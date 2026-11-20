from scout.demo import seed_slack


def test_seed_slack_posts_chat_context_as_colleagues(fake_slack):
    posted = seed_slack(fake_slack)
    assert posted == ["chat_refund", "overspend_chat"]
    msgs = fake_slack.named("chat_postMessage")
    assert msgs[0]["channel"] == "C_FIN" and msgs[0]["username"] == "Grazyna Nowak" and "PFB-2611" in msgs[0]["text"]
    assert msgs[1]["username"] == "Vincenzo Amalfi"
