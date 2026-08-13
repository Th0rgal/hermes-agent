"""D3: monitor controllers flood the Desktop conversation by re-emitting the
same material state every tick with a moving checkpoint number.  The normalized
STATE_SIGNATURE collapses those into one delivery key."""

from cron.scheduler import _normalized_state_signature


def test_moving_checkpoint_normalizes_equal():
    a = _normalized_state_signature(
        "SCANNER DEAD - checkpoint=2755198976\n"
        "[STATE_SIGNATURE: coldcard|scan|2755198976|scanner-dead|await-relaunch]"
    )
    b = _normalized_state_signature(
        "SCANNER DEAD - checkpoint=2307719168\n"
        "[STATE_SIGNATURE: coldcard|scan|2307719168|scanner-dead|await-relaunch]"
    )
    assert a is not None and a == b


def test_genuine_state_change_differs():
    dead = _normalized_state_signature(
        "[STATE_SIGNATURE: coldcard|scan|123|scanner-dead|await-relaunch]"
    )
    found = _normalized_state_signature(
        "[STATE_SIGNATURE: coldcard|scan|none|match-found|verify-next]"
    )
    assert dead != found


def test_no_signature_returns_none():
    # Ordinary cron output without a signature is never signature-deduped.
    assert _normalized_state_signature("just a normal report, 64% done") is None
    assert _normalized_state_signature("") is None
