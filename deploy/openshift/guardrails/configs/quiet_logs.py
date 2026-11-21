"""Keep checked message text out of the pod log (review T051).

The NeMo server configures the root logger at INFO, and its action dispatcher, the regex and Presidio actions and the
event loop log action parameters and results, which contain the full text of every checked message (replies with
IBANs, e-mail addresses, salaries). There is no environment variable or config.yaml switch for this in the RHOAI
image, but the server imports this module when it loads a config, so the level is set here. Access lines of uvicorn
(method, path, status) carry no content and stay.
"""

import logging

logging.getLogger().setLevel(logging.WARNING)
for _name in ("nemoguardrails", "presidio-analyzer", "presidio_analyzer"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def init(app):  # noqa: ANN001 - hook called by NeMo with the LLMRails instance; nothing to register
    logging.getLogger().setLevel(logging.WARNING)
