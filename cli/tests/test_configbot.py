import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from configbot import main, render_change


@pytest.mark.parametrize("change, expected", [
    ({"op": "replace", "path": "/cpu", "old": 1500, "new": 1200}, "~ /cpu: 1500 → 1200"),
    ({"op": "add", "path": "/envs/MODE", "old": None, "new": "fast"}, '+ /envs/MODE = "fast"'),
    ({"op": "remove", "path": "/envs/MODE", "old": "fast", "new": None}, '- /envs/MODE (was "fast")'),
    # history entries carry raw patch ops: no "old", value under "value"
    ({"op": "replace", "path": "/cpu", "value": 1200}, "~ /cpu = 1200"),
    ({"op": "remove", "path": "/envs/MODE"}, "- /envs/MODE"),
])
def test_render_change(change, expected):
    assert render_change(change).strip() == expected


MESSAGE_RESULT = {"app": "storefront", "applied": True, "revision": 3,
                  "changes": [{"op": "replace", "path": "/cpu", "old": 1500, "new": 1200}]}


@patch("configbot.call", return_value=MESSAGE_RESULT)
def test_message_sends_request_and_apply_flag(mock_call, capsys):
    main(["lower storefront cpu to 80%", "--apply", "--author", "arda"])
    mock_call.assert_called_once_with(
        "POST", "/edits", {"text": "lower storefront cpu to 80%", "apply": True, "author": "arda"})
    assert "revision #3" in capsys.readouterr().out


@patch("configbot.call", return_value=[])
def test_history_routes_to_history_endpoint(mock_call, capsys):
    main(["history", "storefront", "--limit", "5"])
    mock_call.assert_called_once_with("GET", "/history/storefront?limit=5")
    assert "no history yet" in capsys.readouterr().out


@patch("configbot.call", return_value={"id": 4, "reverts": 3, "patch": []})
def test_undo_routes_to_undo_endpoint(mock_call, capsys):
    main(["undo", "storefront", "--author", "arda"])
    mock_call.assert_called_once_with("POST", "/undo/storefront?author=arda")
    assert "reverted #3" in capsys.readouterr().out
