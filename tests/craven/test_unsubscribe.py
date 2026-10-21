from harvey.craven.unsubscribe import mint_token, token_in_body, unsubscribe_url, verify_token
from tests.craven.conftest import NODE_TOKEN, SECRET


def test_round_trip_lowercases_the_email():
    tok = mint_token(SECRET, "craven", "Info@Swan.pub", "camp-42")
    assert verify_token(SECRET, tok) == {"tenant": "craven", "email": "info@swan.pub", "campaign_id": "camp-42"}


def test_python_mints_exactly_what_node_minted():
    assert mint_token(SECRET, "craven", "Info@Swan.pub", "camp-42") == NODE_TOKEN


def test_python_verifies_a_node_minted_token():
    assert verify_token(SECRET, NODE_TOKEN)["email"] == "info@swan.pub"


def test_tampering_or_wrong_secret_fails():
    tok = mint_token(SECRET, "craven", "info@swan.pub", "c")
    payload, sig = tok.split(".")
    assert verify_token("other-secret", tok) is None
    assert verify_token(SECRET, payload + "." + sig[:-2] + "AA") is None
    assert verify_token(SECRET, payload) is None
    assert verify_token(SECRET, "") is None
    forged_payload = payload[:-4] + "AAAA"
    assert verify_token(SECRET, forged_payload + "." + sig) is None


def test_link_format_matches_v1_route_and_is_found_in_a_body():
    tok = mint_token(SECRET, "craven", "info@swan.pub", "c")
    url = unsubscribe_url("https://u.test/", tok)
    assert url == f"https://u.test/unsubscribe?t={tok}"
    assert token_in_body(f"Hello.\n\nStop these emails: {url}") == tok
    assert token_in_body("no link here") is None
